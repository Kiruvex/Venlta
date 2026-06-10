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

        # 存储上次检查更新的结果，供下载时获取 download_url/sha256_url
        self._pending_app_update = None
        self._pending_core_update = None

        # 连接内部信号到桥接信号
        # 注意：信号连接中的 .to_json() 是必需的，因为信号直接 emit 到前端，不经过 bridge_method
        self.singbox_mgr.stateChanged.connect(
            lambda d: self.proxyStateChanged.emit(BridgeResult.success(d).to_json())
        )
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
        # 启动代理时，若 TUN 未启用则自动设置系统代理，确保流量实际走代理。
        # TUN 模式下通过虚拟网卡路由流量，无需设置系统代理。
        # 非 TUN 模式下系统代理是流量路由的关键，不设置则代理虽启动但流量不经过代理。
        # 使用 mixed inbound（同时提供 HTTP+SOCKS5），端口均为 http_port
        tun_enabled = self.config_mgr.get_tun_enabled()
        if not tun_enabled:
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
        self.singbox_mgr.stop()
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
        """Toggle TUN mode

        Architecture (NekoBox-inspired): sing-box creates/destroys TUN devices natively,
        no external helper needed. TunElevator manages privilege elevation.

        Elevation strategies (platform-specific, auto-selected):
        Linux:
          1. setcap (preferred): Grant NET_ADMIN via pkexec setcap (one-time auth)
          2. pkexec (fallback): Start sing-box via pkexec (auth every time)
        Windows:
          1. admin (preferred): App already running as admin, sing-box inherits privileges
          2. uac (fallback): Launch sing-box via ShellExecuteExW("runas") UAC elevation
        macOS:
          1. root (preferred): App running as root
          2. osascript (fallback): Prompt for admin credentials via osascript

        Flow:
        1. Enabling TUN: check privileges, auto-grant if possible
        2. Auto-grant fails: return TUN_CAPABILITY_MISSING error
        3. Frontend can call grantTunCapability() for manual grant, then retry
        4. Privileges sufficient: save setting, restart proxy for new config
        5. SingboxManager.start_singbox selects correct launch method via TunElevator
        """
        import platform as _platform

        if enabled:
            # Check if we have TUN privileges
            if self.tun_elevator.needs_elevation():
                method = self.tun_elevator.get_elevation_method()

                if method == "setcap":
                    # Linux setcap: try auto-grant (one-time pkexec auth dialog)
                    result = self.tun_elevator.check_and_grant_capability()
                    if not result.get("ok"):
                        # setcap failed, but pkexec fallback may work at launch time
                        pass
                elif method == "pkexec":
                    # Linux pkexec: will prompt at sing-box launch time, OK to proceed
                    pass
                elif method == "uac":
                    # Windows UAC: will prompt at sing-box launch time via ShellExecuteExW
                    pass
                elif method == "osascript":
                    # macOS osascript: will prompt at sing-box launch time
                    pass
                elif method == "unavailable":
                    # No elevation method available on this platform
                    return BridgeResult.fail(
                        "TUN_CAPABILITY_MISSING",
                        self.tun_elevator.get_elevation_error_message()
                    )

        self.config_mgr.set_tun_enabled(enabled)
        # 立即重新生成配置文件，确保 TUN inbound 反映当前设置
        # 虽然 write_config() 已改为始终重新生成，这里显式调用确保
        # 配置文件在重启前就已更新（避免时序问题）
        try:
            self.config_mgr.regenerate()
        except RuntimeError as e:
            logger.warning(f"Config regeneration after TUN toggle failed: {e}")

        # System proxy management on TUN toggle (NekoBox-inspired):
        # TUN mode captures all traffic via virtual NIC, system proxy is redundant.
        # When switching between TUN and non-TUN modes, we must adjust system proxy:
        # - Enable TUN: disable system proxy (avoid double-routing and potential conflicts)
        # - Disable TUN: re-enable system proxy (traffic must route through proxy port)
        # This is critical: without re-enabling system proxy after disabling TUN,
        # the proxy runs but traffic doesn't go through it.
        if self.singbox_mgr.get_state().get("isRunning"):
            http_port = self.db.get_setting('http_port', 10809)
            if enabled:
                # TUN mode: disable system proxy (TUN handles routing)
                self.sys_proxy.set_enabled(False)
            else:
                # Non-TUN mode: re-enable system proxy
                self.sys_proxy.set_enabled(True, port=http_port, socks_port=http_port)
            # Restart proxy to apply new config (TUN inbound added/removed)
            # SingboxManager.start_singbox auto-selects launch method via TunElevator
            self.singbox_mgr.restart()
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
        # 注意：不在此处做 URL 可达性检查（同步 HTTP 请求会阻塞 GUI）。
        # 可达性由 _fetch_and_parse 在后台线程中异步验证，失败时通过 subscriptionUpdated 信号通知前端。
        sub_id = self.db.add_subscription(name, url)
        # 异步更新订阅内容，不阻塞 GUI
        # 注意：lambda 中的 .to_json() 是必需的，因为直接 emit 信号，不经过 bridge_method
        self.sub_mgr.update_async(sub_id, on_done=lambda result:
            self.subscriptionUpdated.emit(BridgeResult.success({"subId": sub_id, "result": result}).to_json())
        )
        return BridgeResult.success({"id": sub_id, "status": "updating"})

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
            # 获取当前端口配置
            # 使用 mixed inbound（同时提供 HTTP+SOCKS5），端口均为 http_port
            http_port = self.db.get_setting('http_port', 10809)
            self.sys_proxy.set_enabled(settings['system_proxy_enabled'], port=http_port, socks_port=http_port)
        # 检测端口/DNS 相关字段变更，触发配置重生成 + 代理热重载
        # 设计 §4.7.4 验收标准："DNS 修改后代理配置热重载"
        # 即：代理运行中修改端口/DNS → 重写配置文件 → 重启 sing-box 使新配置生效
        # 代理未运行时仅重写配置文件，下次启动时自动使用新配置
        config_affecting_keys = {
            'socks_port', 'http_port', 'clash_api_port', 'clash_api_secret',
            'dns_server_1', 'dns_server_2'
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
        # 3. None：无新版本
        # 情况2不能直接用 BridgeResult.success() 包装，否则前端误认为成功
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
    def checkCoreUpdate(self) -> str:
        """检查 sing-box 核心是否有新版本"""
        update_info = self.updater.check_singbox_update()
        # 与 checkUpdate 一致：区分错误和无新版本
        if isinstance(update_info, dict) and not update_info.get("ok", True):
            return BridgeResult.fail(
                code="CORE_UPDATE_CHECK_FAILED",
                message=update_info.get("error", "Failed to check for core updates")
            )
        # 存储检查结果供 downloadLatestCoreUpdate 使用
        if update_info:
            self._pending_core_update = update_info
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
    def downloadLatestCoreUpdate(self) -> str:
        """下载最新 sing-box 核心更新（后台线程执行，通过 downloadProgress 信号推送进度）

        支持 SHA256 校验：如果 check_singbox_update 返回了 sha256_url，下载后自动校验。
        """
        if not self._pending_core_update or not self._pending_core_update.get("download_url"):
            return BridgeResult.fail("NO_UPDATE_AVAILABLE", "No core update available to download")
        url = self._pending_core_update["download_url"]
        sha256_url = self._pending_core_update.get("sha256_url", "")

        def _do_download():
            try:
                self.downloadProgress.emit(BridgeResult.success({"stage": "downloading", "type": "core"}).to_json())
                import tempfile
                import hashlib
                import httpx
                with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as tmp:
                    with httpx.stream("GET", url, timeout=120) as resp:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            tmp.write(chunk)
                    tmp_path = tmp.name

                # SHA256 verification (if sha256_url is available)
                if sha256_url:
                    try:
                        sha256_resp = httpx.get(sha256_url, timeout=10)
                        if sha256_resp.status_code == 200:
                            expected_hash = sha256_resp.text.strip().split()[0]
                            actual_hash = hashlib.sha256(Path(tmp_path).read_bytes()).hexdigest()
                            if actual_hash != expected_hash:
                                logger.error(f"Core download SHA256 mismatch: expected {expected_hash}, got {actual_hash}")
                                Path(tmp_path).unlink(missing_ok=True)
                                self.downloadProgress.emit(BridgeResult.fail("SHA256_MISMATCH", "Download SHA256 verification failed").to_json())
                                return
                            logger.info(f"Core download SHA256 verified: {actual_hash[:16]}...")
                    except Exception as sha_err:
                        logger.warning(f"SHA256 verification skipped (non-critical): {sha_err}")

                self.downloadProgress.emit(BridgeResult.success({"stage": "done", "type": "core", "path": tmp_path}).to_json())
            except Exception as e:
                logger.error(f"downloadLatestCoreUpdate error: {e}")
                self.downloadProgress.emit(BridgeResult.fail("DOWNLOAD_ERROR", str(e)).to_json())

        threading.Thread(target=_do_download, daemon=True).start()
        return BridgeResult.success({"status": "downloading"})

    @Slot(str, result=str)
    @bridge_method
    def installCoreUpdate(self, archive_path: str) -> str:
        """Install downloaded sing-box core update and restart the core"""
        # Stop sing-box first
        if self.singbox_mgr.get_state().get("isRunning"):
            self.singbox_mgr.stop()
        result = self.updater.install_core_update(archive_path)
        if result.get("ok"):
            # Restart sing-box with the new binary
            self.singbox_mgr.start()
            return BridgeResult.success({"message": "Core updated successfully"})
        else:
            # Try to restart with old binary if update failed
            self.singbox_mgr.start()
            return BridgeResult.fail("CORE_INSTALL_FAILED", result.get("error", "Failed to install core update"))

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
        """Check for both app and core updates, return results for notification

        This is called on startup when auto_update_enabled is True.
        Results are returned synchronously so the frontend can show notifications.
        """
        app_update = None
        core_update = None
        try:
            app_update = self.updater.check_update()
        except Exception as e:
            logger.debug(f"Startup app update check failed: {e}")
        try:
            core_update = self.updater.check_singbox_update()
        except Exception as e:
            logger.debug(f"Startup core update check failed: {e}")

        result = {}
        # App update: filter out error results
        if app_update and isinstance(app_update, dict) and app_update.get("ok", True) and app_update.get("version"):
            result["app"] = app_update
            self._pending_app_update = app_update
        # Core update: filter out error results
        if core_update and isinstance(core_update, dict) and core_update.get("ok", True) and core_update.get("version"):
            result["core"] = core_update
            self._pending_core_update = core_update

        return BridgeResult.success(result if result else None)
