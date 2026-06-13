"""统计采集器 - 流量/连接数据采集

使用 QThread + httpx 连接池提升性能。注意：Clash API `/traffic` 端点是 SSE 流式响应，
不能使用简单 GET 请求；应改用 `/connections` 端点（返回 JSON），从中提取累计流量并计算速率。
"""

import json
import time
import logging
import os
import traceback
from PySide6.QtCore import QObject, Signal, QThread
import httpx

logger = logging.getLogger(__name__)

class StatsWorker(QThread):
    """统计采集 Worker，运行在独立 QThread 中，使用 httpx 连接池"""
    trafficUpdated = Signal(dict)
    connectionsUpdated = Signal(dict)

    def __init__(self, singbox_mgr, clash_api_secret: str = ""):
        super().__init__()
        self.singbox_mgr = singbox_mgr
        self._clash_api_secret = clash_api_secret
        self._running = False
        self._poll_interval = 1.0
        self._client: httpx.Client | None = None
        self._last_upload = 0
        self._last_download = 0
        self._last_time: float | None = None
        self._consecutive_errors = 0
        self._need_rebuild = False

    def run(self):
        self._running = True
        headers = {}
        if self._clash_api_secret:
            headers["Authorization"] = f"Bearer {self._clash_api_secret}"
        # 创建持久连接池，避免每次轮询建立新连接
        # 不设置 base_url，因为每次轮询动态获取端口构建完整 URL
        try:
            self._client = httpx.Client(
                timeout=httpx.Timeout(2.0, connect=1.0),
                limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
                headers=headers,
            )
        except Exception as e:
            logger.error(f"StatsWorker: failed to create httpx client: {e}")
            self._running = False
            return
        # 注意：此 Worker 使用 msleep() 轮询循环，而非 QThread 事件循环。
        # 不调用 self.exec()，因此 workerThread.quit()（要求事件循环运行中）
        # 对此 Worker 无实际效果。stop_polling() 通过设置 _running=False 退出循环。
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                # 捕获所有异常，防止未处理的异常导致线程崩溃或 Qt 信号异常
                self._consecutive_errors += 1
                if self._consecutive_errors <= 3:
                    logger.warning(f"StatsWorker unexpected error ({self._consecutive_errors}x): {e}")
                else:
                    logger.debug(f"StatsWorker unexpected error: {e}")
            self.msleep(int(self._poll_interval * 1000))
        # 清理
        self._cleanup_client()

    def _poll_once(self):
        """执行一次轮询，提取为方法便于异常捕获"""
        if not self._running:
            return
        # 检查是否需要重建客户端（sing-box 重启后由 reset_on_singbox_restart 设置）
        if self._need_rebuild:
            self._need_rebuild = False
            self._rebuild_client()
        # 使用 .get() 避免 KeyError（跨线程读取时 dict 可能正在被修改）
        # 注意：此方法在 QThread 中运行，singbox_mgr.get_state() 返回 _cached_state 的浅拷贝，
        # 主线程可能正在更新 _cached_state。CPython GIL 保证 dict 单次操作原子性，
        # 但不保证多次读取的一致性（如 isRunning=True 但 currentMode 可能已变）。
        # 当前设计下这种不一致性可接受，因为统计信息是周期性采集的。
        try:
            is_running = self.singbox_mgr.get_state().get("isRunning", False)
        except Exception:
            # singbox_mgr 可能已被销毁（应用退出时）
            return
        if not is_running:
            # sing-box 未运行时重置流量计数器，避免下次启动时计算出不合理的速率
            self._last_upload = 0
            self._last_download = 0
            self._last_time = None
            return
        try:
            # 动态获取 Clash API 端口（用户可能在运行时修改端口设置）
            clash_api_port = self.singbox_mgr.config_mgr.get_clash_api_port()
        except Exception:
            return
        if self._client is None or self._client.is_closed:
            return
        try:
            resp = self._client.get(f"http://127.0.0.1:{clash_api_port}/connections")
        except (httpx.ConnectError, httpx.TimeoutException, httpx.PoolTimeout,
                httpx.ConnectTimeout, httpx.ReadTimeout, OSError, ConnectionError):
            # 连接失败：sing-box 可能未就绪或已停止
            self._consecutive_errors += 1
            if self._consecutive_errors <= 3:
                logger.warning(f"StatsWorker poll error ({self._consecutive_errors}x): connection failed")
            # ★ 首次连接失败即重建 httpx 客户端 ★
            # TUN 模式切换或 sing-box 重启后，旧连接立即失效，
            # 不应等待 3 次失败才重建（否则流量图表长时间显示 0）
            if self._consecutive_errors >= 1:
                self._rebuild_client()
            return
        except Exception as e:
            self._consecutive_errors += 1
            if self._consecutive_errors <= 3:
                logger.warning(f"StatsWorker poll error ({self._consecutive_errors}x): {e}")
            if self._consecutive_errors >= 3:
                self._rebuild_client()
            return
        if resp.status_code == 200:
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                return
            # 从 connections 响应中提取累计流量，计算速率
            now = time.time()
            upload_total = data.get("uploadTotal", 0)
            download_total = data.get("downloadTotal", 0)
            if self._last_time is not None:
                dt = now - self._last_time
                if dt > 0:
                    up_rate = (upload_total - self._last_upload) / dt
                    down_rate = (download_total - self._last_download) / dt
                    self.trafficUpdated.emit({
                        "uploadRate": up_rate,
                        "downloadRate": down_rate,
                        "totalUpload": upload_total,
                        "totalDownload": download_total,
                    })
            self._last_upload = upload_total
            self._last_download = download_total
            self._last_time = now
            # 连接数据
            conns = data.get("connections", [])
            self.connectionsUpdated.emit({
                "count": len(conns),
                "connections": conns,
            })
            # 成功后重置连续错误计数
            self._consecutive_errors = 0

    def _cleanup_client(self):
        """安全关闭 httpx 客户端"""
        if self._client is not None:
            try:
                if not self._client.is_closed:
                    self._client.close()
            except Exception:
                pass
            self._client = None

    def _rebuild_client(self):
        """重建 httpx 客户端连接池，修复因 sing-box 重启导致的连接失效

        当 sing-box 重启（如 TUN 模式切换）时，原有 TCP 连接失效，
        httpx 的连接池可能保留失效的 keepalive 连接，导致后续请求持续失败。
        重建客户端可以清除失效连接并建立新的连接池。
        """
        logger.info("StatsWorker: rebuilding httpx client after consecutive errors")
        self._cleanup_client()
        headers = {}
        if self._clash_api_secret:
            headers["Authorization"] = f"Bearer {self._clash_api_secret}"
        try:
            self._client = httpx.Client(
                timeout=httpx.Timeout(2.0, connect=1.0),
                limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
                headers=headers,
            )
            self._consecutive_errors = 0
        except Exception as e:
            logger.error(f"StatsWorker: failed to rebuild httpx client: {e}")

    def stop_polling(self):
        self._running = False
        # 注意：msleep 最长等待 _poll_interval 秒后退出。
        # 如需更快响应停止，可将 _poll_interval 缩短或在 msleep 前检查 _running 标志。
        # 当前 1 秒轮询间隔的停止延迟可接受。

    def reset_on_singbox_restart(self):
        """在 sing-box 重启后重置连接状态

        TUN 模式切换或 sing-box 重启后，httpx 连接池中的 keepalive 连接失效，
        调用此方法立即重建客户端，避免流量图表长时间显示 0。
        此方法可从主线程安全调用（仅设置标志，不操作 httpx 客户端）。
        """
        self._last_upload = 0
        self._last_download = 0
        self._last_time = None
        self._consecutive_errors = 0
        # 标记需要重建客户端，在下次 _poll_once 时执行
        # （不能在主线程直接操作 QThread 中的 httpx 客户端）
        self._need_rebuild = True


class StatsCollector(QObject):
    """统计采集器，对外暴露接口，内部委托给 QThread Worker"""
    trafficUpdated = Signal(dict)
    connectionsUpdated = Signal(dict)

    def __init__(self, singbox_mgr, clash_api_secret: str = ""):
        super().__init__()
        self.workerThread = QThread()
        self.worker = StatsWorker(singbox_mgr, clash_api_secret)
        # Worker 移到 QThread，Collector 留在主线程
        self.worker.moveToThread(self.workerThread)

        self.worker.trafficUpdated.connect(self.trafficUpdated.emit)
        self.worker.connectionsUpdated.connect(self.connectionsUpdated.emit)

        self.workerThread.started.connect(self.worker.run)

    def start(self):
        if self.workerThread.isRunning():
            return  # 防止重复启动
        self.workerThread.start()

    def stop(self):
        self.worker.stop_polling()
        # quit() 发送退出事件给 QThread 的事件循环，但 StatsWorker 使用 msleep 轮询
        # 而非 exec() 事件循环，因此 quit() 本身无实际效果。线程实际由 stop_polling()
        # 设置 _running = False 后在 msleep 超时退出。quit() 保留以备未来改用 exec()。
        self.workerThread.quit()
        self.workerThread.wait(3000)
