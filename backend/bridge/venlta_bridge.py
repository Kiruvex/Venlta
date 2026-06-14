from PySide6.QtCore import QObject, Slot, Signal
import json
import logging
import threading
from pathlib import Path
from typing import Dict, Any
from core.tun_elevator import TunElevator
from bridge.result import BridgeResult, bridge_method

logger = logging.getLogger(__name__)

class VenltaBridge(QObject):
    # 前端可监听的信号（统一返回 BridgeResult JSON）
    proxyStateChanged = Signal(str)
    logEmitted = Signal(str)  # 与前端 api.ts 信号名保持一致（QWebChannel 按后端 Signal 名暴露）
    trafficStatsUpdated = Signal(str)
    connectionsUpdated = Signal(str)
    latencyResult = Signal(str)
    subscriptionUpdated = Signal(str)  # 订阅更新结果通知（sub_id + 结果 JSON）
    speedResult = Signal(str)  # 带宽测试结果通知
    connectionClosed = Signal(str)  # 关闭连接结果通知
    downloadProgress = Signal(str)  # 下载进度通知（downloading/verifying/done/error）

    def __init__(self, *, singbox_mgr, config_mgr, sys_proxy, tun_elevator, sub_mgr, stats, db, updater, port_detector, speed_tester=None):
        """VenltaBridge 构造函数，使用关键字参数提高可读性和可维护性

        所有参数必须以关键字形式传入（* 强制），避免位置参数顺序错误。
        """
        super().__init__()
        self.singbox_mgr = singbox_mgr
        self.config_mgr = config_mgr
        self.sys_proxy = sys_proxy
        self.tun_elevator = tun_elevator
        self.sub_mgr = sub_mgr
        self.stats = stats
        self.db = db
        self.updater = updater
        self.port_detector = port_detector
        self.speed_tester = speed_tester

        # 存储上次检查更新的结果，供下载时获取 download_url
        self._pending_app_update = None

        # 互斥锁：防止 toggleSystemProxy / toggleTun 并发执行产生竞态
        # 例如：用户快速连续切换两个开关，两者都试图 start/stop sing-box
        self._toggle_lock = threading.Lock()

        # 连接内部信号到桥接信号
        # 注意：信号连接中的 .to_json() 是必需的，因为信号直接 emit 到前端，不经过 bridge_method
        self.singbox_mgr.stateChanged.connect(
            lambda d: self.proxyStateChanged.emit(BridgeResult.success(d).to_json())
        )
        # ★ sing-box 重启时重置统计采集器连接状态 ★
        # TUN 模式切换或 sing-box 重启后，httpx 连接池中的 keepalive 连接失效，
        # 需要立即重建客户端，避免流量图表长时间显示 0。
        self.singbox_mgr.stateChanged.connect(self._on_singbox_state_changed)
        self.singbox_mgr.logEmitted.connect(
            lambda d: self.logEmitted.emit(BridgeResult.success({"log": d}).to_json())
        )
        self.singbox_mgr.latencyResult.connect(
            lambda d: self.latencyResult.emit(BridgeResult.success(d).to_json())
        )
        self.singbox_mgr.connectionClosed.connect(
            lambda d: self.connectionClosed.emit(BridgeResult.success(d).to_json())
        )
        self.stats.trafficUpdated.connect(
            lambda d: self.trafficStatsUpdated.emit(BridgeResult.success(d).to_json())
        )
        self.stats.connectionsUpdated.connect(
            lambda d: self.connectionsUpdated.emit(BridgeResult.success(d).to_json())
        )
        # 带宽测试结果信号（SpeedTester → VenltaBridge → 前端）
        if self.speed_tester:
            self.speed_tester.speedResult.connect(
                lambda d: self.speedResult.emit(BridgeResult.success(d).to_json())
            )

    # ---------- 内部信号处理 ----------

    def _on_singbox_state_changed(self, state: dict):
        """sing-box 状态变化时重置统计采集器

        当 sing-box 重启（TUN 切换、手动重启、崩溃恢复等）时，
        StatsWorker 的 httpx 连接池中的 keepalive 连接会失效，
        导致流量图表持续显示 0。调用 reset_on_singbox_restart() 
        立即标记需要重建客户端。
        """
        if state.get("isRunning"):
            self.stats.worker.reset_on_singbox_restart()

    # ---------- 代理控制 ----------
    @Slot(str, result=str)
    @bridge_method
    def switchMode(self, mode: str) -> str:
        """切换代理模式（route/global/direct），通过 SingboxWorker 异步执行 Clash API 调用

        注意：前端使用 'route' 表示规则路由模式，但 Clash API 使用 'rule'。
        此处需要将前端的 'route' 转换为 API 的 'rule'，与 _get_current_mode() 的反向转换对应。

        重要：Clash API 调用可能阻塞数秒（超时），不能在主线程执行，
        否则会冻结 GUI。改为通过 SingboxWorker 的 QueuedConnection 异步执行，
        结果通过 proxyStateChanged 信号推送，前端无需等待同步返回。
        """
        # 前端 'route' → Clash API 'rule'（前端不直接暴露 'rule' 概念）
        api_mode = "rule" if mode == "route" else mode
        # 委托给 SingboxWorker 异步执行（QueuedConnection 不阻塞主线程）
        # 返回 success 仅表示"切换指令已发送"，实际结果通过 proxyStateChanged 信号通知
        self.singbox_mgr.switch_mode_async(api_mode)
        return BridgeResult.success({"status": "switching"})

    @Slot(result=str)
    @bridge_method
    def getProxyState(self) -> str:
        state = self.singbox_mgr.get_state()
        return BridgeResult.success(state)

    @Slot(result=str)
    @bridge_method
    def startProxy(self) -> str:
        # 端口冲突检测
        ports = self.config_mgr.get_used_ports()
        conflict = self.port_detector.check_ports(ports)
        if conflict:
            return BridgeResult.fail(
                code="PORT_IN_USE",
                message=f"Port {conflict['port']} is already in use by another application",
                detail=f"port={conflict['port']}, pid={conflict['pid']}, process={conflict['process']}"  # 详细信息仅记录在日志中，不暴露给前端
            )
        self.singbox_mgr.start()
        # 系统代理与 TUN 完全独立（与 NekoBox 一致）
        # 启动代理时，根据 system_proxy_enabled 设置决定是否设置系统代理，
        # 不再根据 TUN 状态决定。TUN 和系统代理可以同时开启。
        # 使用 mixed inbound（同时提供 HTTP+SOCKS5），端口均为 http_port
        sys_proxy_enabled = self.db.get_setting('system_proxy_enabled', False)
        if sys_proxy_enabled:
            http_port = self.db.get_setting('http_port', 10809)
            self.sys_proxy.set_enabled(True, port=http_port, socks_port=http_port)
        # 注意：start() 通过 QMetaObject.invokeMethod(QueuedConnection) 异步执行，
        # 此处返回 success 仅表示"启动指令已发送"，不代表 sing-box 已成功运行。
        # 前端应监听 proxyStateChanged 信号获取实际启动状态，
        # 信号会在 SingboxWorker.start_singbox() 完成后触发 stateChanged，
        # 经 VenltaBridge 转发为 proxyStateChanged 到前端。
        # 若启动失败，logEmitted 信号会推送错误日志，前端 LogsPage 可查看。
        return BridgeResult.success({"status": "starting"})

    @Slot(result=str)
    @bridge_method
    def stopProxy(self) -> str:
        """停止 sing-box 并清理所有代理模式

        停止 sing-box 进程，同时关闭系统代理设置。
        TUN 设备由 sing-box 进程退出时自动销毁。
        """
        self.singbox_mgr.stop()
        # 停止时总是清理系统代理，避免 OS 代理指向已关闭的端口
        self.sys_proxy.set_enabled(False)
        return BridgeResult.success()

    @Slot(result=str)
    @bridge_method
    def restartProxy(self) -> str:
        self.singbox_mgr.restart()
        return BridgeResult.success()

    @Slot(bool, result=str)
    @bridge_method
    def toggleTun(self, enabled: bool) -> str:
        """Toggle TUN mode (independent of system proxy, with mutex guard)

        Architecture (NekoBox-inspired): sing-box creates/destroys TUN devices natively,
        no external helper needed. TunElevator manages privilege elevation.

        使用互斥锁确保 toggleTun / toggleSystemProxy 不会并发执行，
        避免竞态条件（如一个在 start() 同时另一个在 stop()）。
        """
        if not self._toggle_lock.acquire(blocking=False):
            return BridgeResult.fail("TOGGLE_BUSY", "Another toggle operation is in progress")
        try:
            return self._toggleTun_inner(enabled)
        finally:
            self._toggle_lock.release()

    def _toggleTun_inner(self, enabled: bool) -> str:
        """toggleTun 的实际实现（已持有互斥锁）"""
        import platform as _platform

        if enabled:
            # Check if we have TUN privileges
            if self.tun_elevator.needs_elevation():
                method = self.tun_elevator.get_elevation_method()
                logger.info(f"TUN needs elevation, method={method}")

                if method == "setcap":
                    result = self.tun_elevator.check_and_grant_capability()
                    logger.info(f"TUN setcap grant result: {result}")
                    if not result.get("ok"):
                        error = result.get("error", "Failed to grant NET_ADMIN capability")
                        logger.error(f"TUN setcap grant failed: {error}")
                        return BridgeResult.fail(
                            "TUN_CAPABILITY_GRANT_FAILED",
                            f"无法授予 TUN 权限: {error}。请手动执行: sudo setcap cap_net_admin,cap_net_raw+ep $(which sing-box)"
                        )
                elif method == "pkexec":
                    # pkexec will prompt at launch time, OK to proceed
                    logger.info("TUN will use pkexec for elevation at launch time")
                elif method == "unavailable":
                    return BridgeResult.fail(
                        "TUN_CAPABILITY_MISSING",
                        self.tun_elevator.get_elevation_error_message()
                    )
            else:
                logger.info("TUN: no elevation needed (already has capability)")

        self.config_mgr.set_tun_enabled(enabled)
        try:
            self.config_mgr.regenerate()
        except RuntimeError as e:
            # 配置重生成失败：回滚 DB 中的 tun_enabled 设置，避免状态不一致
            # 典型场景：TUN 配置语法错误导致 sing-box check 失败，配置被回滚到非 TUN 版本
            # 如果不回滚 DB 设置，DB 中 tun_enabled=True 但实际配置无 TUN → 下次启动仍会失败
            logger.error(f"Config regeneration after TUN toggle failed: {e}")
            self.config_mgr.set_tun_enabled(not enabled)  # 回滚
            return BridgeResult.fail(
                "CONFIG_REGENERATION_FAILED",
                f"配置重生成失败: {e}"
            )

        # 读取当前真实状态（在锁内，不会被并发修改）
        is_running = self.singbox_mgr.get_state().get("isRunning")
        sys_proxy_enabled = self.db.get_setting('system_proxy_enabled', False)

        if enabled:
            if is_running:
                self.singbox_mgr.restart()
            else:
                self.singbox_mgr.start()
        else:
            if is_running:
                if sys_proxy_enabled:
                    self.singbox_mgr.restart()
                else:
                    self.singbox_mgr.stop()

        self.singbox_mgr.update_cached_state(isTunEnabled=enabled)
        if not enabled and not sys_proxy_enabled and is_running:
            self.singbox_mgr.update_cached_state(isRunning=False)
        if enabled and not is_running:
            self.singbox_mgr.update_cached_state(isRunning=True)
        self.proxyStateChanged.emit(
            BridgeResult.success(self.singbox_mgr.get_state()).to_json()
        )

        return BridgeResult.success()

    @Slot(result=str)
    @bridge_method
    def checkTunCapability(self) -> str:
        """检查当前环境是否具备 TUN 模式所需的权限

        Returns:
            {"can_create_tun": bool, "platform": str, "details": str, "elevation_method": str}
        """
        status = self.tun_elevator.get_capability_status()
        return BridgeResult.success(status)

    @Slot(result=str)
    @bridge_method
    def grantTunCapability(self) -> str:
        """Grant TUN capability to sing-box

        Platform behavior:
        Linux: Grant NET_ADMIN capability via pkexec setcap (one-time auth).
               After update, capability is lost and must be re-granted.
        Windows: No persistent capability; UAC elevation happens at launch time.
                 This method returns success with method="uac" to indicate that
                 elevation will be handled automatically when TUN starts.
        macOS: No persistent capability; osascript credential prompt happens at launch time.
               This method returns success with method="osascript".

        After granting, call toggleTun(True) to enable TUN.

        Returns:
            {"ok": bool, "error": str, "already_has": bool, "method": str}
        """
        import platform as _platform
        method = self.tun_elevator.get_elevation_method()

        if method == "none":
            return BridgeResult.success({
                "already_has": True,
                "method": "none",
            })

        if method in ("uac", "osascript"):
            # Windows/macOS: elevation happens at launch time, nothing to pre-grant
            return BridgeResult.success({
                "already_has": False,
                "method": method,
            })

        # Linux: try setcap grant
        result = self.tun_elevator.check_and_grant_capability()
        if result.get("ok"):
            return BridgeResult.success({
                "already_has": result.get("already_has", False),
                "method": method,
            })
        # setcap failed, check for pkexec fallback
        if method == "setcap" and self.tun_elevator.get_elevation_method() != "unavailable":
            # Even if setcap failed, pkexec fallback may work
            actual_method = self.tun_elevator.get_elevation_method()
            return BridgeResult.success({
                "already_has": False,
                "method": actual_method,
            })
        return BridgeResult.fail("TUN_CAPABILITY_GRANT_FAILED", result.get("error", "Failed to grant capability"))

    @Slot(bool, result=str)
    @bridge_method
    def toggleSystemProxy(self, enabled: bool) -> str:
        """Toggle system proxy mode (independent of TUN, with mutex guard)

        与 NekoBox 一致：系统代理和 TUN 是完全独立的两个模式。
        可以同时开启 TUN 和系统代理，也可以单独开启其中任何一个。

        使用互斥锁确保 toggleSystemProxy / toggleTun 不会并发执行，
        避免竞态条件（如一个在 start() 同时另一个在 stop()）。
        """
        if not self._toggle_lock.acquire(blocking=False):
            return BridgeResult.fail("TOGGLE_BUSY", "Another toggle operation is in progress")
        try:
            return self._toggleSystemProxy_inner(enabled)
        finally:
            self._toggle_lock.release()

    def _toggleSystemProxy_inner(self, enabled: bool) -> str:
        """toggleSystemProxy 的实际实现（已持有互斥锁）"""
        self.db.update_setting('system_proxy_enabled', enabled)

        # 读取当前真实状态（在锁内，不会被并发修改）
        is_running = self.singbox_mgr.get_state().get("isRunning")
        tun_enabled = self.config_mgr.get_tun_enabled()

        if enabled:
            if is_running:
                http_port = self.db.get_setting('http_port', 10809)
                self.sys_proxy.set_enabled(True, port=http_port, socks_port=http_port)
            else:
                # sing-box 未运行：启动 sing-box
                # 端口冲突检测
                ports = self.config_mgr.get_used_ports()
                conflict = self.port_detector.check_ports(ports)
                if conflict:
                    # 回滚设置
                    self.db.update_setting('system_proxy_enabled', not enabled)
                    return BridgeResult.fail(
                        code="PORT_IN_USE",
                        message=f"Port {conflict['port']} is already in use by another application",
                    )
                self.singbox_mgr.start()
                # sing-box 启动是异步的（QMetaObject.invokeMethod QueuedConnection），
                # 但系统代理可以立即设置：浏览器等应用会自动重试连接，
                # sing-box 监听端口就绪后代理即可工作。
                # 注意：此处必须设置系统代理，否则仅启动 sing-box 而不设代理，
                # 应用/浏览器不会将流量转发到 sing-box 的监听端口。
                http_port = self.db.get_setting('http_port', 10809)
                self.sys_proxy.set_enabled(True, port=http_port, socks_port=http_port)
        else:
            # 移除 OS 代理设置
            self.sys_proxy.set_enabled(False)
            # 如果 TUN 也关闭，且 sing-box 正在运行 → 停止 sing-box
            if not tun_enabled and is_running:
                self.singbox_mgr.stop()

        self.singbox_mgr.update_cached_state(isSystemProxyEnabled=enabled)
        if not enabled and not tun_enabled and is_running:
            self.singbox_mgr.update_cached_state(isRunning=False)
        if enabled and not is_running:
            self.singbox_mgr.update_cached_state(isRunning=True)
        self.proxyStateChanged.emit(
            BridgeResult.success(self.singbox_mgr.get_state()).to_json()
        )

        return BridgeResult.success({"system_proxy_enabled": enabled})

    # ---------- 节点管理 ----------
    @Slot(result=str)
    @bridge_method
    def listNodes(self) -> str:
        nodes = self.db.get_nodes()
        return BridgeResult.success(nodes)

    @Slot(str, result=str)
    @bridge_method
    def addNode(self, node_json: str) -> str:
        node_data = json.loads(node_json)
        node_id = self.db.add_node(node_data)
        self.config_mgr.regenerate()
        return BridgeResult.success({"id": node_id})

    @Slot(str, str, result=str)
    @bridge_method
    def updateNode(self, node_id: str, updates_json: str) -> str:
        updates = json.loads(updates_json)
        self.db.update_node(node_id, updates)
        self.config_mgr.regenerate()
        return BridgeResult.success()

    @Slot(str, result=str)
    @bridge_method
    def deleteNode(self, node_id: str) -> str:
        self.db.delete_node(node_id)
        self.config_mgr.regenerate()
        return BridgeResult.success()

    @Slot(str, result=str)
    @bridge_method
    def testLatency(self, node_tags_json: str) -> str:
        """测试节点延迟，参数为 node tag 列表的 JSON（非数据库 id/UUID）

        使用 SpeedTester 门面进行分批测试和并发控制（如果可用），
        否则直接调用 SingboxManager.test_latency。
        结果通过 latencyResult 信号异步推送到前端。
        """
        node_tags = json.loads(node_tags_json)
        if self.speed_tester:
            self.speed_tester.test(node_tags)
        else:
            self.singbox_mgr.test_latency(node_tags)
        return BridgeResult.success({"status": "testing"})

    @Slot(str, str, result=str)
    @bridge_method
    def switchNode(self, group_tag: str, node_tag: str) -> str:
        """切换 selector 组选中的节点（通过 SingboxWorker 异步执行 Clash API PUT /proxies/:name）

        重要：与 switchMode 相同，Clash API 调用不能在主线程执行。
        委托给 SingboxWorker 异步执行（QueuedConnection），结果通过信号通知。
        """
        # 委托给 SingboxWorker 异步执行（不阻塞主线程）
        # 返回 success 仅表示"切换指令已发送"，实际结果通过 proxyStateChanged 信号通知
        self.singbox_mgr.switch_node_async(group_tag, node_tag)
        return BridgeResult.success({"status": "switching"})

    @Slot(str, result=str)
    @bridge_method
    def batchUpdateNodeLatency(self, updates_json: str) -> str:
        """批量更新节点延迟结果（单次调用替代 N 次 updateNode，减少 Bridge 开销）"""
        updates = json.loads(updates_json)
        # 使用批量数据库操作替代逐条 update_node，避免 N+1 查询问题
        # 原实现：for each update → get_node_by_tag (SELECT) + update_node (UPDATE) = 2N 次数据库操作
        # 优化后：1 次 SELECT 获取所有 tag→id 映射 + 1 次批量 UPDATE = 2 次数据库操作
        tag_to_id = self.db.get_node_ids_by_tags([u.get('tag') for u in updates if u.get('tag')])
        batch_updates = []
        for u in updates:
            tag = u.get('tag')
            if not tag or tag not in tag_to_id:
                continue
            batch_updates.append({
                'id': tag_to_id[tag],
                'latency': u.get('latency'),
                'last_test_at': u.get('lastTestAt'),
            })
        if batch_updates:
            self.db.batch_update_node_latency(batch_updates)
        return BridgeResult.success()

    # ---------- 分组管理 ----------
    @Slot(result=str)
    @bridge_method
    def listNodeGroups(self) -> str:
        groups = self.db.get_node_groups()
        return BridgeResult.success(groups)

    @Slot(str, result=str)
    @bridge_method
    def addNodeGroup(self, group_json: str) -> str:
        data = json.loads(group_json)
        group_id = self.db.add_node_group(data)
        return BridgeResult.success({"id": group_id})

    @Slot(str, str, result=str)
    @bridge_method
    def updateNodeGroup(self, group_id: str, updates_json: str) -> str:
        updates = json.loads(updates_json)
        self.db.update_node_group(group_id, updates)
        return BridgeResult.success()

    @Slot(str, result=str)
    @bridge_method
    def deleteNodeGroup(self, group_id: str) -> str:
        self.db.delete_node_group(group_id)
        return BridgeResult.success()

    # ---------- 订阅管理 ----------
    # 注意：订阅更新涉及网络请求，不能在 @Slot 中同步执行（会阻塞 GUI）
    # 改为先返回 ID，通过 subscriptionUpdated 信号通知更新结果

    @Slot(result=str)
    @bridge_method
    def listSubscriptions(self) -> str:
        subs = self.db.get_subscriptions()
        return BridgeResult.success(subs)

    @Slot(str, str, result=str)
    @bridge_method
    def addSubscription(self, name: str, url: str) -> str:
        # 验证 URL 格式（必须包含协议前缀 http:// 或 https://）
        from urllib.parse import urlparse
        parsed_url = urlparse(url)
        if parsed_url.scheme not in ('http', 'https'):
            return BridgeResult.fail("INVALID_URL", "Subscription URL must start with http:// or https://")
        if not parsed_url.netloc:
            return BridgeResult.fail("INVALID_URL", "Subscription URL is missing host")
        # 创建订阅
        sub_id = self.db.add_subscription(name, url)
        # ★ 自动为订阅创建同名分组 ★
        # 每个订阅的节点归入独立分组，便于管理和筛选
        group_id = self.db.add_node_group({'name': name})
        # 将 group_id 关联到订阅（用于后续节点导入时自动分配分组）
        self.db.update_subscription(sub_id, {'group_id': group_id})
        # 异步更新订阅内容，不阻塞 GUI
        # 注意：lambda 中的 .to_json() 是必需的，因为直接 emit 信号，不经过 bridge_method
        self.sub_mgr.update_async(sub_id, on_done=lambda result:
            self.subscriptionUpdated.emit(BridgeResult.success({"subId": sub_id, "result": result}).to_json())
        )
        return BridgeResult.success({"id": sub_id, "groupId": group_id, "status": "updating"})

    @Slot(str, result=str)
    @bridge_method
    def updateSubscription(self, sub_id: str) -> str:
        # 异步更新订阅内容，不阻塞 GUI
        # 注意：lambda 中的 .to_json() 是必需的，因为直接 emit 信号，不经过 bridge_method
        self.sub_mgr.update_async(sub_id, on_done=lambda result:
            self.subscriptionUpdated.emit(BridgeResult.success({"subId": sub_id, "result": result}).to_json())
        )
        return BridgeResult.success({"status": "updating"})

    @Slot(str, result=str)
    @bridge_method
    def deleteSubscription(self, sub_id: str) -> str:
        self.db.delete_subscription(sub_id)
        self.config_mgr.regenerate()
        return BridgeResult.success()

    @Slot(str, str, result=str)
    @bridge_method
    def updateSubscriptionMeta(self, sub_id: str, updates_json: str) -> str:
        """更新订阅元数据（名称、URL等），不触发节点刷新

        与 updateSubscription（触发节点拉取）不同，此方法仅更新数据库中的订阅元数据。
        适用于用户手动编辑订阅名称或URL的场景。
        """
        updates = json.loads(updates_json)
        self.db.update_subscription(sub_id, updates)
        return BridgeResult.success()

    # ---------- 路由规则 ----------
    @Slot(result=str)
    @bridge_method
    def listRules(self) -> str:
        rules = self.db.get_rules()
        return BridgeResult.success(rules)

    @Slot(str, result=str)
    @bridge_method
    def addRule(self, rule_json: str) -> str:
        rule = json.loads(rule_json)
        rule_id = self.db.add_rule(rule)
        self.config_mgr.regenerate()
        return BridgeResult.success({"id": rule_id})

    @Slot(str, str, result=str)
    @bridge_method
    def updateRule(self, rule_id: str, updates_json: str) -> str:
        updates = json.loads(updates_json)
        self.db.update_rule(rule_id, updates)
        self.config_mgr.regenerate()
        return BridgeResult.success()

    @Slot(str, result=str)
    @bridge_method
    def deleteRule(self, rule_id: str) -> str:
        self.db.delete_rule(rule_id)
        self.config_mgr.regenerate()
        return BridgeResult.success()

    @Slot(result=str)
    @bridge_method
    def listRuleSets(self) -> str:
        rule_sets = self.db.get_rule_sets()
        return BridgeResult.success(rule_sets)

    @Slot(str, result=str)
    @bridge_method
    def addRuleSet(self, ruleset_json: str) -> str:
        data = json.loads(ruleset_json)
        rs_id = self.db.add_rule_set(data)
        self.config_mgr.regenerate()
        return BridgeResult.success({"id": rs_id})

    @Slot(str, str, result=str)
    @bridge_method
    def updateRuleSet(self, ruleset_id: str, updates_json: str) -> str:
        updates = json.loads(updates_json)
        self.db.update_rule_set(ruleset_id, updates)
        self.config_mgr.regenerate()
        return BridgeResult.success()

    @Slot(str, result=str)
    @bridge_method
    def deleteRuleSet(self, ruleset_id: str) -> str:
        self.db.delete_rule_set(ruleset_id)
        self.config_mgr.regenerate()
        return BridgeResult.success()

    # ---------- 设置 ----------
    @Slot(result=str)
    @bridge_method
    def getSettings(self) -> str:
        settings = self.db.get_settings()
        # 注入加密降级状态，供前端显示安全警告
        try:
            from utils.crypto import is_key_derivation_degraded
            settings["encryption_degraded"] = is_key_derivation_degraded()
        except ImportError:
            settings["encryption_degraded"] = False
        # 安全：移除 clash_api_secret，防止敏感密钥泄漏到前端
        settings.pop("clash_api_secret", None)
        return BridgeResult.success(settings)

    @Slot(str, result=str)
    @bridge_method
    def setSettings(self, settings_json: str) -> str:
        settings = json.loads(settings_json)
        ignored_keys: list[str] = []  # 记录被忽略的设置键名，返回给前端
        # tun_enabled 必须通过 toggleTun() 设置（涉及 TUN 设备创建/销毁和 sing-box 重启），
        # 不能通过 setSettings 静默修改，否则 TUN 状态与数据库不一致
        if 'tun_enabled' in settings:
            logger.warning("tun_enabled should be set via toggleTun(), not setSettings(). Ignoring.")
            # 将被忽略的键名告知前端，避免用户误以为设置成功
            ignored_keys.append('tun_enabled')
            del settings['tun_enabled']
        self.db.update_settings(settings)
        if 'system_proxy_enabled' in settings:
            # system_proxy_enabled 推荐通过 toggleSystemProxy() 设置，
            # 但 setSettings() 也支持（用于批量设置等场景）。
            # 系统代理与 TUN 完全独立，无需检查 TUN 状态。
            # 仅在代理运行中时实际设置/恢复 OS 代理，否则仅保存设置。
            http_port = self.db.get_setting('http_port', 10809)
            if self.singbox_mgr.get_state().get("isRunning"):
                self.sys_proxy.set_enabled(settings['system_proxy_enabled'], port=http_port, socks_port=http_port)
        # 检测端口/DNS 相关字段变更，触发配置重生成 + 代理热重载
        # 设计 §4.7.4 验收标准："DNS 修改后代理配置热重载"
        # 即：代理运行中修改端口/DNS → 重写配置文件 → 重启 sing-box 使新配置生效
        # 代理未运行时仅重写配置文件，下次启动时自动使用新配置
        config_affecting_keys = {
            'socks_port', 'http_port', 'clash_api_port', 'clash_api_secret',
            'dns_server_1', 'dns_server_2', 'dns_strategy', 'outbound_domain_strategy',
            'utls_fingerprint', 'underlying_dns', 'fakeip_inet4_range', 'fakeip_inet6_range',
            'tun_stack', 'tun_mtu', 'tun_strict_route', 'tun_address', 'tun_address_6',
            'tun_route_exclude_address', 'tun_route_include_address',
            'enable_tun_routing', 'tun_split_proxy', 'tun_split_direct', 'tun_split_block',
            'dns_final_out_direct', 'ntp_enabled', 'ntp_server', 'ntp_server_port', 'ntp_interval',
            'rule_set_cdn', 'adblock_enabled', 'log_level',
        }
        if config_affecting_keys & set(settings.keys()):
            self.config_mgr.regenerate()
            # 代理运行中时热重载配置（类似 toggleTun 的处理模式）
            if self.singbox_mgr.get_state().get("isRunning"):
                self.singbox_mgr.restart()
        result_data = {}
        if ignored_keys:
            result_data["ignored_keys"] = ignored_keys  # 前端可据此提示用户哪些设置被忽略
        return BridgeResult.success(result_data if result_data else None)

    # ---------- 端口检测 ----------
    @Slot(result=str)
    @bridge_method
    def checkPortConflicts(self) -> str:
        ports = self.config_mgr.get_used_ports()
        conflicts = self.port_detector.check_all_ports(ports)
        return BridgeResult.success({"conflicts": conflicts})

    # ---------- 连接管理 ----------
    @Slot(str, result=str)
    @bridge_method
    def closeConnection(self, conn_id: str) -> str:
        """关闭指定连接（通过 Clash API DELETE /connections/:id）

        注意：conn_id 是 Clash API connections 列表中的 id 字段，
        不是数据库节点 id。此方法在工作线程中异步执行，
        不阻塞 GUI（通过 SingboxWorker 异步调用 Clash API）。
        结果通过 connectionClosed 信号推送，前端无需等待同步返回。
        """
        self.singbox_mgr.close_connection_async(conn_id)
        return BridgeResult.success({"status": "closing"})

    # ---------- 速度测试 ----------
    @Slot(str, result=str)
    @bridge_method
    def testSpeed(self, node_tags_json: str) -> str:
        """测试节点带宽速度（下载吞吐量，字节/秒）

        参数为 node tag 列表的 JSON（与 testLatency 格式一致）。
        测试通过 SOCKS5 代理下载测速文件，计算实际吞吐量。
        结果通过 speedResult 信号异步推送到前端（每节点完成后立即推送）。
        """
        node_tags = json.loads(node_tags_json)
        if self.speed_tester:
            self.speed_tester.test_speed(node_tags)
        return BridgeResult.success({"status": "testing"})

    # ---------- i18n ----------
    @Slot(result=str)
    @bridge_method
    def getSystemLanguage(self) -> str:
        from PySide6.QtCore import QLocale
        locale = QLocale.system().name()
        return BridgeResult.success({"language": locale})

    @Slot(result=str)
    @bridge_method
    def getAppVersion(self) -> str:
        """Return current app version and sing-box version for frontend display"""
        app_ver = self.updater.current_version
        core_ver = self.updater._get_current_singbox_version()
        return BridgeResult.success({"app_version": app_ver, "singbox_version": core_ver})

    @Slot(str, result=str)
    @bridge_method
    def setBackendLanguage(self, lang: str) -> str:
        """前端语言切换时同步后端语言（托盘菜单/提示/通知跟随切换）

        Args:
            lang: 语言代码，如 "zh", "en", "zh_CN", "en_US"
        """
        from utils.i18n import set_language
        set_language(lang)
        # 通知托盘重建菜单（使用新语言的文本）
        if hasattr(self, '_tray') and self._tray:
            self._tray.rebuild_menu()
        return BridgeResult.success()

    # ---------- 自动更新 ----------
    @Slot(result=str)
    @bridge_method
    def checkUpdate(self) -> str:
        update_info = self.updater.check_update()
        # 注意：AutoUpdater.check_update() 返回值有三种情况：
        # 1. dict（含 version/download_url 等）：有新版本
        # 2. dict（含 ok=False, error）：检查失败
        # 3. None：无新版本（包括 rate limit 时静默返回 None）
        if isinstance(update_info, dict) and not update_info.get("ok", True):
            return BridgeResult.fail(
                code="UPDATE_CHECK_FAILED",
                message=update_info.get("error", "Failed to check for updates")
            )
        # 存储检查结果供 downloadLatestUpdate 使用
        if update_info:
            self._pending_app_update = update_info
        return BridgeResult.success(update_info)

    @Slot(result=str)
    @bridge_method
    def downloadLatestUpdate(self) -> str:
        """下载最新应用更新（后台线程执行，通过 downloadProgress 信号推送进度）"""
        if not self._pending_app_update or not self._pending_app_update.get("download_url"):
            return BridgeResult.fail("NO_UPDATE_AVAILABLE", "No update available to download")
        url = self._pending_app_update["download_url"]
        sha256_url = self._pending_app_update.get("sha256_url", "")

        def _do_download():
            try:
                self.downloadProgress.emit(BridgeResult.success({"stage": "downloading", "type": "app"}).to_json())
                result = self.updater.download_and_verify(url, sha256_url)
                if result:
                    self.downloadProgress.emit(BridgeResult.success({"stage": "done", "type": "app", "path": str(result)}).to_json())
                else:
                    self.downloadProgress.emit(BridgeResult.fail("DOWNLOAD_FAILED", "Download or SHA256 verification failed").to_json())
            except Exception as e:
                logger.error(f"downloadLatestUpdate error: {e}")
                self.downloadProgress.emit(BridgeResult.fail("DOWNLOAD_ERROR", str(e)).to_json())

        threading.Thread(target=_do_download, daemon=True).start()
        return BridgeResult.success({"status": "downloading"})

    @Slot(result=str)
    @bridge_method
    def isSingboxInstalled(self) -> str:
        """检查 sing-box 核心是否已安装"""
        installed = self.updater.is_singbox_installed()
        return BridgeResult.success({"installed": installed})

    @Slot(result=str)
    @bridge_method
    def downloadSingboxCore(self) -> str:
        """下载 sing-box 核心（固定版本 1.13.13），后台线程执行，通过 downloadProgress 信号推送进度"""
        info = self.updater.get_singbox_download_info()
        if not info or not info.get("download_url"):
            return BridgeResult.fail("CORE_DOWNLOAD_UNAVAILABLE", "Failed to get sing-box download info")

        url = info["download_url"]
        expected_sha256 = info.get("expected_sha256", "")

        def _do_download():
            try:
                self.downloadProgress.emit(BridgeResult.success({"stage": "downloading", "type": "core"}).to_json())
                result = self.updater.download_and_verify(url, expected_sha256=expected_sha256)
                if result:
                    self.downloadProgress.emit(BridgeResult.success({"stage": "done", "type": "core", "path": str(result)}).to_json())
                else:
                    self.downloadProgress.emit(BridgeResult.fail("DOWNLOAD_FAILED", "Download or SHA256 verification failed").to_json())
            except Exception as e:
                logger.error(f"downloadSingboxCore error: {e}")
                self.downloadProgress.emit(BridgeResult.fail("DOWNLOAD_ERROR", str(e)).to_json())

        threading.Thread(target=_do_download, daemon=True).start()
        return BridgeResult.success({"status": "downloading"})

    @Slot(str, result=str)
    @bridge_method
    def installSingboxCore(self, archive_path: str) -> str:
        """安装下载的 sing-box 核心"""
        # Stop sing-box first if running
        if self.singbox_mgr.get_state().get("isRunning"):
            self.singbox_mgr.stop()
        result = self.updater.install_core_update(archive_path)
        if result.get("ok"):
            # 更新数据库中的 sing-box 版本号，避免前端显示旧值
            new_version = self.updater._get_current_singbox_version()
            self.db.update_setting('singbox_version', new_version)
            return BridgeResult.success({"message": "sing-box core installed successfully", "singbox_version": new_version})
        else:
            # Try to restart with old binary if install failed
            self.singbox_mgr.start()
            return BridgeResult.fail("CORE_INSTALL_FAILED", result.get("error", "Failed to install sing-box core"))

    @Slot(str, result=str)
    @bridge_method
    def installAppUpdate(self, archive_path: str) -> str:
        """Install downloaded app update (placeholder)"""
        result = self.updater.install_app_update(archive_path)
        if result.get("ok"):
            return BridgeResult.success({"message": "App updated successfully"})
        else:
            return BridgeResult.fail("APP_INSTALL_FAILED", result.get("error", "App auto-install not supported"))

    @Slot(result=str)
    @bridge_method
    def checkAndNotifyUpdates(self) -> str:
        """Check for app updates, return results for notification

        This is called on startup when auto_update_enabled is True.
        Results are returned synchronously so the frontend can show notifications.
        """
        result = {}
        try:
            app_update = self.updater.check_update()
            if app_update and isinstance(app_update, dict) and app_update.get("ok", True) and app_update.get("version"):
                result["app"] = app_update
                self._pending_app_update = app_update
        except Exception as e:
            logger.debug(f"Startup app update check failed: {e}")

        return BridgeResult.success(result if result else None)
