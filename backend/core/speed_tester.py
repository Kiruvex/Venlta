"""速度测试门面（Facade）

实际延迟测试委托给 SingboxWorker.test_latency（通过 Clash API 测量），
带宽测试通过下载测试文件测量实际吞吐量。
本模块在此基础上添加并发控制（限制同时测试的节点数量）和结果聚合（批量回调、延迟持久化）。
两者职责不重叠：
- SingboxWorker.test_latency：单次批量延迟测试（并发5线程），通过 latencyResult 信号推送结果
- SpeedTester：在 SingboxWorker 之上封装，添加队列管理、并发限制、结果聚合、带宽测试等功能

注意：SpeedTester 继承 QObject 以连接 SingboxManager.latencyResult 信号，
跟踪每批次测试完成情况。信号连接在 __init__ 中自动建立。
"""

import logging
import time
import httpx
from PySide6.QtCore import QObject, Signal
from typing import Callable

logger = logging.getLogger(__name__)

# 最大同时测试的节点批次数（避免同时发起过多 Clash API 请求导致超时）
MAX_CONCURRENT_BATCHES = 3

# 带宽测试配置
SPEED_TEST_URL = "https://c.speed.cloudflare.com/__down?bytes=10000000"  # 10MB 测试文件
SPEED_TEST_TIMEOUT = 15  # 带宽测试超时（秒），比延迟测试长
SPEED_TEST_MAX_DURATION = 10  # 最大下载时长（秒），达到后提前终止


class SpeedTester(QObject):
    """速度测试门面
    
    封装 SingboxWorker.test_latency，添加并发控制、结果聚合和带宽测试。
    
    使用场景：
    1. 前端点击"测试延迟"按钮 → SpeedTester.test() → SingboxWorker.test_latency()
    2. 前端点击"测试速度"按钮 → SpeedTester.test_speed() → 直接 HTTP 下载测速
    3. 结果通过 latencyResult/speedResult 信号异步推送到前端
    4. SpeedTester 通过连接 latencyResult 信号跟踪批次完成情况，
       所有批次完成后调用 on_complete 回调进行结果聚合和延迟持久化
    
    注意：当前实现中 SingboxWorker.test_latency 已经内置了并发5线程的延迟测试，
    SpeedTester 的并发控制主要防止多个测试批次同时执行导致 API 过载。
    """
    
    # 带宽测试结果信号（包含 nodeId + speed 字节/秒）
    speedResult = Signal(dict)
    
    def __init__(self, singbox_mgr, db=None, parent=None):
        """初始化速度测试器
        
        Args:
            singbox_mgr: SingboxManager 实例，用于调用 test_latency 和连接信号
            db: DatabaseManager 实例，用于持久化速度测试结果（可选）
            parent: Qt 父对象
        """
        super().__init__(parent)
        self.singbox_mgr = singbox_mgr
        self.db = db
        self._running_batches = 0  # 当前正在执行的测试批次数
        self._pending_batches: list[list[str]] = []  # 等待执行的测试批次队列
        self._all_results: list[dict] = []  # 所有批次的聚合结果
        self._total_batches = 0  # 当前测试任务的总批次数
        self._completed_count = 0  # 已完成的批次数
        self._on_complete: Callable[[dict], None] | None = None  # 所有批次完成后的回调
        self._active_tags: set[str] = set()  # 当前正在测试的节点 tag 集合，用于匹配结果
        self._speed_testing = False  # 是否有带宽测试正在进行
        
        # 连接 SingboxManager 的 latencyResult 信号，跟踪批次完成情况
        # SingboxManager.latencyResult 转发自 SingboxWorker.latencyResult，
        # 每次调用 test_latency 后会触发一次，包含该批次所有节点的测试结果
        self.singbox_mgr.latencyResult.connect(self._on_latency_result)
    
    def test(self, node_tags: list[str], batch_size: int = 20, on_complete: Callable[[dict], None] | None = None):
        """测试节点延迟（自动分批，并发控制）
        
        将大量节点分批测试，每批最多 batch_size 个节点。
        同时最多执行 MAX_CONCURRENT_BATCHES 批测试。
        
        Args:
            node_tags: 要测试的节点 tag 列表
            batch_size: 每批测试的节点数量（默认20）
            on_complete: 所有批次完成后的回调，参数为聚合结果 dict
        """
        if not node_tags:
            if on_complete:
                on_complete({"ok": True, "results": []})
            return
        
        # 重置状态
        self._all_results = []
        self._completed_count = 0
        self._on_complete = on_complete
        self._active_tags = set(node_tags)
        
        # 分批
        self._pending_batches = [node_tags[i:i + batch_size] for i in range(0, len(node_tags), batch_size)]
        self._total_batches = len(self._pending_batches)
        
        # 启动初始批次
        self._try_next_batch()
    
    def _get_current_node(self) -> str | None:
        """获取当前 selector 选中的节点 tag（测速前保存，测速后恢复）"""
        try:
            from urllib.parse import quote
            from core.config_manager import PROXY_SELECTOR_TAG
            clash_api_port = self.singbox_mgr.config_mgr.get_clash_api_port()
            headers = self.singbox_mgr.get_clash_api_headers()
            resp = httpx.get(
                f"http://127.0.0.1:{clash_api_port}/proxies/{quote(PROXY_SELECTOR_TAG, safe='')}",
                headers=headers,
                timeout=3,
            )
            if resp.status_code == 200:
                return resp.json().get("now", None)
        except Exception:
            pass
        return None
    
    def _switch_node(self, tag: str) -> bool:
        """切换 selector 到指定节点 tag"""
        try:
            from urllib.parse import quote
            from core.config_manager import PROXY_SELECTOR_TAG
            clash_api_port = self.singbox_mgr.config_mgr.get_clash_api_port()
            headers = self.singbox_mgr.get_clash_api_headers()
            resp = httpx.put(
                f"http://127.0.0.1:{clash_api_port}/proxies/{quote(PROXY_SELECTOR_TAG, safe='')}",
                json={"name": tag},
                headers=headers,
                timeout=3,
            )
            return resp.status_code == 204
        except Exception:
            return False

    def test_speed(self, node_tags: list[str], test_url: str | None = None):
        """测试节点带宽（下载速度，单位字节/秒）
        
        使用 httpx 通过代理下载测试文件，测量实际吞吐量。
        每个节点串行测试（避免大量并发下载占满带宽），结果通过 speedResult 信号推送。
        测速完成后自动恢复到测速前用户选择的节点。
        
        原理：通过 sing-box 的 SOCKS5/HTTP 代理下载测试文件，
        根据下载量和耗时计算平均速度。使用流式读取避免大文件占满内存。
        
        Args:
            node_tags: 要测试速度的节点 tag 列表
            test_url: 测速文件 URL（默认使用 Cloudflare 10MB 测试文件）
        """
        if not node_tags or self._speed_testing:
            return
        
        # 检查代理是否正在运行
        if not self.singbox_mgr.get_state().get("isRunning"):
            logger.warning("Speed test requested but proxy is not running")
            # 推送所有节点失败结果
            for tag in node_tags:
                self.speedResult.emit({"results": [{"nodeId": tag, "speed": -1, "error": "Proxy is not running"}]})
            return
        
        self._speed_testing = True
        url = test_url or SPEED_TEST_URL
        
        # 获取代理端口（使用 mixed inbound，同时提供 HTTP+SOCKS5，端口均为 http_port）
        try:
            socks_port = self.singbox_mgr.config_mgr.db.get_setting('http_port', 10809)
        except Exception:
            socks_port = 10809
        
        def test_one_speed(tag: str) -> dict:
            """测试单个节点的带宽
            
            使用 Clash API 先切换到目标节点，
            然后通过 SOCKS5 代理下载测速文件计算速度。
            """
            try:
                # 先切换到目标节点
                if not self._switch_node(tag):
                    return {"nodeId": tag, "speed": -1, "error": "Failed to switch node"}
                
                # 等待节点切换生效（短暂延迟让路由更新）
                time.sleep(0.3)
                
                # 通过 SOCKS5 代理下载测速文件
                proxy_url = f"socks5://127.0.0.1:{socks_port}"
                start_time = time.time()
                total_bytes = 0
                
                with httpx.stream(
                    "GET", url,
                    proxy=proxy_url,
                    timeout=SPEED_TEST_TIMEOUT,
                    follow_redirects=True,
                ) as response:
                    if response.status_code != 200:
                        return {"nodeId": tag, "speed": -1, "error": f"HTTP {response.status_code}"}
                    
                    for chunk in response.iter_bytes(chunk_size=65536):
                        total_bytes += len(chunk)
                        elapsed = time.time() - start_time
                        # 达到最大测试时长后提前终止，避免下载过大文件
                        if elapsed >= SPEED_TEST_MAX_DURATION:
                            break
                
                elapsed = time.time() - start_time
                speed = total_bytes / elapsed if elapsed > 0 else 0
                
                return {"nodeId": tag, "speed": int(speed)}
            except Exception as e:
                return {"nodeId": tag, "speed": -1, "error": str(e)}
        
        # 在后台线程中串行测试每个节点（避免并发下载占满带宽）
        # 串行保证每个节点测试时独占代理带宽，结果更准确
        all_results: list[dict] = []
        
        # 注意：_get_current_node() 和 _switch_node() 涉及 HTTP 请求，
        # 必须在后台线程中执行，否则会阻塞主线程（GUI 冻结）
        
        def run_speed_tests():
            # 保存测速前用户选择的节点，测速完成后恢复（移至后台线程避免阻塞 GUI）
            original_node = self._get_current_node()
            logger.info(f"Speed test starting, current node: {original_node}, testing {len(node_tags)} nodes")
            try:
                for tag in node_tags:
                    if not self._speed_testing:
                        break  # 支持中途取消
                    result = test_one_speed(tag)
                    all_results.append(result)
                    # 每个节点完成后立即推送结果，前端可实时更新
                    self.speedResult.emit({"results": [result]})
            finally:
                self._speed_testing = False
                
                # 恢复用户原始选择的节点
                if original_node:
                    try:
                        self._switch_node(original_node)
                        logger.info(f"Speed test complete, restored node to: {original_node}")
                    except Exception as e:
                        logger.warning(f"Failed to restore original node after speed test: {e}")
                
                # 刷新代理状态，确保前端显示正确的当前节点
                try:
                    from PySide6.QtCore import QMetaObject, Qt
                    QMetaObject.invokeMethod(
                        self.singbox_mgr.worker, "_refresh_state_async", Qt.QueuedConnection
                    )
                except Exception:
                    pass
                
                # 批量持久化速度结果到数据库
                if self.db and all_results:
                    valid_results = [r for r in all_results if r.get("speed", -1) >= 0]
                    if valid_results:
                        try:
                            tag_to_id = self.db.get_node_ids_by_tags([r["nodeId"] for r in valid_results])
                            batch_updates = []
                            for r in valid_results:
                                if r["nodeId"] in tag_to_id:
                                    batch_updates.append({
                                        'id': tag_to_id[r["nodeId"]],
                                        'speed': r["speed"],
                                        'last_test_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                    })
                            if batch_updates:
                                self.db.batch_update_node_latency(batch_updates)
                        except Exception as e:
                            logger.warning(f"Failed to persist speed results: {e}")
        
        # 在独立线程中执行测速，避免阻塞 GUI
        import threading
        t = threading.Thread(target=run_speed_tests, daemon=True)
        t.start()
    
    def _on_latency_result(self, result: dict):
        """延迟测试结果信号处理
        
        由 SingboxManager.latencyResult 信号触发，
        每次对应一个批次的测试结果。
        """
        # 收集结果
        if result.get("results"):
            self._all_results.extend(result["results"])
            # 从活跃 tag 集合中移除已完成的节点
            for r in result["results"]:
                tag = r.get("nodeId", "")
                self._active_tags.discard(tag)
        
        self._running_batches = max(0, self._running_batches - 1)
        self._completed_count += 1
        
        # 尝试执行下一批
        self._try_next_batch()
        
        # 所有批次完成
        if self._completed_count >= self._total_batches:
            if self._on_complete:
                self._on_complete({"ok": True, "results": self._all_results})
                self._on_complete = None  # 避免重复调用
    
    def _try_next_batch(self):
        """尝试执行下一批测试（受并发数限制）"""
        if self._running_batches >= MAX_CONCURRENT_BATCHES:
            return
        
        if not self._pending_batches:
            return
        
        batch = self._pending_batches.pop(0)
        self._running_batches += 1
        
        # 委托给 SingboxManager.test_latency（实际由 SingboxWorker 在 QThread 中执行）
        self.singbox_mgr.test_latency(batch)
    
    @property
    def is_testing(self) -> bool:
        """是否有延迟测试正在进行"""
        return self._running_batches > 0 or len(self._pending_batches) > 0
    
    @property
    def is_speed_testing(self) -> bool:
        """是否有带宽测试正在进行"""
        return self._speed_testing
