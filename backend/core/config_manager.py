import json
import shutil
import subprocess
import platform
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from utils.constants import get_data_dir

logger = logging.getLogger(__name__)

# 默认端口
DEFAULT_SOCKS_PORT = 10808
DEFAULT_HTTP_PORT = 10809
DEFAULT_CLASH_API_PORT = 9090

# 规则中的数组字段（需要 JSON 序列化/反序列化）
# 此常量被 ConfigManager._build_route 和 DatabaseManager 共同使用，定义在模块级别避免跨类引用
RULE_ARRAY_FIELDS = frozenset({
    'domain', 'domain_suffix', 'domain_keyword', 'domain_regex', 'geosite',
    'ip_cidr', 'source_ip_cidr', 'source_geoip', 'port_range',
    'source_port_range', 'process_name', 'process_path', 'package_name',
    'user_id',
    # 注意：rule_set 不在此列表中，因为数据库无 rule_set 列（只有 rule_set_id）。
    # rule_set 字段由 _build_route 从 rule_set_id 动态构建，不走 JSON 解析路径。
})

# 代理选择器组 tag 名称（_build_outbounds 和 _get_current_node 共同引用）
PROXY_SELECTOR_TAG = "proxy"

def get_singbox_dir() -> Path:
    """获取 sing-box 核心安装目录（持久化路径，Nuitka 和开发环境均可用）

    路径推导优先级：
    1. Nuitka 打包后：可执行文件同级目录下的 sing-box/ 子目录
       （sys.executable 始终指向实际可执行文件，而 __file__ 在 Nuitka 中可能不可靠）
    2. 开发环境：backend/sing-box/ 目录（基于 __file__ 推导）
    """
    import sys
    # Nuitka 打包后：使用 sys.executable 定位（与 main.py 加载 frontend 的逻辑一致）
    # 判断是否为 Nuitka 打包环境：sys.executable 不含 "python" 且 __file__ 不可靠
    exe_dir = Path(sys.executable).parent
    is_nuitka = not any("python" in part.lower() for part in Path(sys.executable).parts)
    if is_nuitka:
        # Nuitka 环境：始终使用可执行文件同级目录（无论是否已存在）
        return exe_dir / "sing-box"
    # 开发环境：基于 __file__ 推导
    dev_path = Path(__file__).parent.parent / "sing-box"
    return dev_path


def find_singbox_binary() -> str:
    """统一解析 sing-box 二进制路径（只查找自身目录，不查系统 PATH）

    优先级：
    1. Nuitka 打包后的 resources/sing-box/ 目录（预装核心）
    2. Nuitka 打包后的可执行文件同级 sing-box/ 目录（用户安装的核心）
    3. __file__ 推导路径（开发环境或旧版 Nuitka 兼容）
    4. 未找到则返回空字符串
    """
    import sys
    binary_name = "sing-box.exe" if platform.system() == "Windows" else "sing-box"
    exe_dir = Path(sys.executable).parent

    # 1. Nuitka 打包后的 resources/sing-box/ 目录（预装核心）
    bundled = exe_dir / "resources" / "sing-box" / binary_name
    if bundled.exists():
        return str(bundled)

    # 2. Nuitka 打包后的可执行文件同级 sing-box/ 目录（用户安装的核心）
    nuitka_installed = exe_dir / "sing-box" / binary_name
    if nuitka_installed.exists():
        return str(nuitka_installed)

    # 3. __file__ 推导路径（开发环境或旧版 Nuitka 路径解析）
    bundled_legacy = Path(__file__).parent.parent / "resources" / "sing-box" / binary_name
    if bundled_legacy.exists():
        return str(bundled_legacy)

    dev_path = get_singbox_dir() / binary_name
    if dev_path.exists():
        return str(dev_path)

    # 4. 未找到
    logger.warning("sing-box binary not found")
    return ""

# 注意：不再在模块级别缓存 SINGBOX_BIN，因为 find_singbox_binary() 可能因环境变化返回不同结果
# 每次调用 find_singbox_binary() 即可，开销可忽略（Path.exists() 为轻量系统调用，且调用频率低）
class ConfigManager:
    def __init__(self, db):
        self.db = db
        # Backward compat: if legacy ~/.venlta exists, prefer it; otherwise use platform dir
        legacy_dir = Path.home() / ".venlta"
        self.config_dir = legacy_dir if legacy_dir.exists() else Path(get_data_dir())
        self.config_dir.mkdir(exist_ok=True)
        self.config_path = self.config_dir / "config.json"
        self.backup_dir = self.config_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        # 线程锁：防止多线程并发调用 regenerate() 导致配置文件写入竞争
        # 典型场景：主线程 toggleTun→regenerate() 与工作线程 start_singbox→write_config→regenerate() 并发
        self._regenerate_lock = threading.Lock()
        # 上次成功 regenerate 的时间戳，用于 write_config() 判断是否需要重新生成
        self._last_regenerate_time: float = 0

    def regenerate(self, skip_validate: bool = False):
        """重新生成 sing-box 配置文件

        Args:
            skip_validate: 如果为 True，跳过 sing-box check 验证步骤。
                用于刚由其他调用方完成验证的场景（如 toggleTun 已调用 regenerate 并验证通过），
                避免在多线程环境中重复 fork 子进程导致 glibc 堆损坏崩溃。
        """
        # 线程安全：防止并发 regenerate() 导致配置文件写入竞争
        with self._regenerate_lock:
            return self._regenerate_inner(skip_validate=skip_validate)

    def _regenerate_inner(self, skip_validate: bool = False):
        # TODO: 当节点/规则数量大时，可优化为增量更新而非全量重写
        # 先备份，备份失败则中止重生成（避免无备份时回滚到更旧的版本）
        if not self._backup():
            raise RuntimeError("Config backup failed, aborting regeneration to prevent data loss")
        config = self._build_config()
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        # 输出关键配置摘要，便于排查代理不工作等问题
        n_outbounds = len(config.get("outbounds", []))
        n_inbounds = len(config.get("inbounds", []))
        n_route_rules = len(config.get("route", {}).get("rules", []))
        n_dns_servers = len(config.get("dns", {}).get("servers", []))
        n_dns_rules = len(config.get("dns", {}).get("rules", []))
        outbound_tags = [o.get("tag", "?") for o in config.get("outbounds", [])]
        inbound_tags = [i.get("tag", "?") for i in config.get("inbounds", [])]
        route_final = config.get("route", {}).get("final", "?")
        dns_final = config.get("dns", {}).get("final", "?")
        # 记录调用栈，便于排查"谁生成了错误配置"的问题
        import traceback
        caller = ''.join(traceback.format_stack()[-4:-1]).strip().replace('\n', ' <- ')
        logger.info(
            f"Config generated: {n_inbounds} inbounds {inbound_tags}, "
            f"{n_outbounds} outbounds {outbound_tags}, "
            f"{n_route_rules} route rules (final={route_final}), "
            f"{n_dns_servers} DNS servers, {n_dns_rules} DNS rules (final={dns_final}) "
            f"[caller: {caller}]"
        )
        # 诊断信息：输出 TUN route_exclude_address 和代理服务器直连规则
        for ib in config.get("inbounds", []):
            if ib.get("type") == "tun":
                exclude = ib.get("route_exclude_address", [])
                logger.info(f"TUN route_exclude_address: {exclude}")
        for rule in config.get("route", {}).get("rules", []):
            if rule.get("outbound") == "direct" and rule.get("domain"):
                logger.info(f"Route direct rule for domains: {rule.get('domain')}")
        # ★ 输出完整路由规则列表（调试用）★
        # 当代理不工作时，路由规则是最关键的诊断信息
        for i, rule in enumerate(config.get("route", {}).get("rules", [])):
            action = rule.get("action", rule.get("outbound", "?"))
            summary_parts = []
            for key in ("domain", "domain_suffix", "ip_cidr", "ip_is_private",
                        "protocol", "process_path", "rule_set", "inbound"):
                val = rule.get(key)
                if val is not None:
                    if isinstance(val, list):
                        summary_parts.append(f"{key}={val}")
                    else:
                        summary_parts.append(f"{key}={val}")
            summary = ", ".join(summary_parts) if summary_parts else "(no match fields)"
            logger.debug(f"  Route rule [{i}]: action={action}, {summary}")
        # 输出 DNS 规则摘要
        for i, rule in enumerate(config.get("dns", {}).get("rules", [])):
            action = rule.get("action", "?")
            server = rule.get("server", "")
            logger.debug(f"  DNS rule [{i}]: action={action}, server={server}, fields={list(rule.keys())}")
        if not skip_validate:
            if not self._validate():
                self._rollback()
                raise RuntimeError("Invalid config generated")
        self._last_regenerate_time = time.time()
        return self.config_path

    _regenerate_timer: float = 0  # 上次 regenerate 时间戳（防抖）
    _DEBOUNCE_MS = 0.1  # 100ms 防抖间隔

    def regenerate_deferred(self):
        """防抖的配置重生成：100ms 内多次调用仅执行一次，避免批量操作时频繁重写
        
        适用场景：订阅更新（循环 add_node/delete_node）、批量规则变更等。
        单次操作（如用户手动编辑节点）应直接调用 regenerate()。
        
        线程安全：从非主线程调用时直接执行 regenerate()（跳过防抖），
        因为 QTimer.singleShot 只能在有 Qt 事件循环的线程中使用，
        从 Python threading.Thread 调用会导致 "event dispatcher destroyed" 错误。
        """
        import time as _time
        import threading
        self._regenerate_timer = _time.time()
        if threading.current_thread() is threading.main_thread():
            # 主线程：使用 QTimer 延迟执行（Qt 事件循环安全，支持防抖）
            from PySide6.QtCore import QTimer
            QTimer.singleShot(int(self._DEBOUNCE_MS * 1000), self._do_deferred_regenerate)
        else:
            # 工作线程：直接执行（无 QTimer，跳过防抖）
            # 订阅更新等后台线程已经是批量操作，不需要防抖
            self.regenerate()

    def _do_deferred_regenerate(self):
        """延迟 regenerate 的实际执行：检查是否仍有更新的请求"""
        import time as _time
        # 如果距离上次调用已超过防抖间隔，执行 regenerate
        if _time.time() - self._regenerate_timer >= self._DEBOUNCE_MS:
            self.regenerate()

    def write_config(self, skip_validate: bool = False) -> Path:
        """重新生成并写入 sing-box 配置文件

        Args:
            skip_validate: 如果为 True，跳过 sing-box check 验证。
                用于 toggleTun 等场景：主线程已调用 regenerate() 并验证通过，
                工作线程的 start_singbox() 再调用 write_config() 时无需重复验证，
                避免在多线程环境中 fork 子进程（sing-box check）导致 glibc 堆损坏。
                如果距上次成功 regenerate 超过 5 秒，则忽略此参数强制验证。
        """
        # 如果配置在最近 5 秒内已重新生成且验证通过，跳过重复生成
        # 避免工作线程的 start_singbox() 与主线程的 toggleTun→regenerate() 竞争
        elapsed = time.time() - self._last_regenerate_time
        if elapsed < 5.0 and self.config_path.exists():
            logger.info(f"Config was regenerated {elapsed:.1f}s ago, skipping redundant regeneration")
            return self.config_path

        try:
            return self.regenerate(skip_validate=skip_validate)
        except RuntimeError:
            # 重新生成失败（如配置无效），尝试使用现有配置作为回退
            if self.config_path.exists() and self._validate():
                logger.warning("Config regeneration failed, using existing config as fallback")
                return self.config_path
            raise

    def get_used_ports(self) -> list[int]:
        """获取当前配置使用的所有端口列表"""
        # 使用 mixed inbound（同时提供 HTTP+SOCKS5），仅占用 http_port
        http_port = self.db.get_setting("http_port", DEFAULT_HTTP_PORT)
        clash_api_port = self.db.get_setting("clash_api_port", DEFAULT_CLASH_API_PORT)
        return [http_port, clash_api_port]

    def _build_config(self) -> dict:
        # 注意：必须使用 raw 方法获取 snake_case 键名的数据，
        # 因为 get_all_nodes()/get_all_rules() 已转换为 camelCase（供前端使用），
        # 而后端内部需要 snake_case 键名来匹配数据库列名
        nodes = self.db.get_all_nodes_raw()
        rules = self.db.get_all_rules_raw()
        rule_sets = self.db.get_all_rule_sets_raw()
        tun_enabled = self.db.get_setting("tun_enabled", False)
        
        # 端口可配置（mixed inbound 仅使用 http_port）
        http_port = self.db.get_setting("http_port", DEFAULT_HTTP_PORT)
        clash_api_port = self.db.get_setting("clash_api_port", DEFAULT_CLASH_API_PORT)

        # 先构建 outbounds 以获知 enabled_tags 和代理服务器域名/IP，
        # 因为构建 TUN inbound 需要 proxy_server_ips 来添加 route_exclude_address（防环路），
        # 构建 DNS 需要 proxy_server_domains 来添加直连 DNS 规则（防 DNS 循环依赖），
        # 构建 route 需要 proxy_server_domains 来添加直连路由规则（防 TUN 路由环路）
        outbounds, enabled_tags, proxy_server_domains, proxy_server_ips = self._build_outbounds(nodes)

        # sing-box 1.13.0 移除了 inbound 上的 sniff / sniff_override_destination 字段，
        # 改用 route rule action（sniff）替代，见 _build_route 中的 sniff 规则。
        #
        # 参考 NekoBox：使用 mixed inbound（同时提供 HTTP+SOCKS 代理）替代分开的
        # socks+http inbound。mixed inbound 是 sing-box 推荐方式，减少 inbound 数量，
        # 同时兼容 HTTP CONNECT 和 SOCKS5 代理协议。
        inbounds = [
            {"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": http_port},
        ]
        if tun_enabled:
            # TUN inbound 配置（参考 NekoBox BuildTunInbound）
            # sing-box 自身负责创建 TUN 设备、设置路由、清理资源
            #
            # 权限要求（由 TunElevator 处理）：
            # - Linux: NET_ADMIN capability 或 root 权限
            # - Windows: 管理员权限
            # - macOS: 特权帮助工具
            #
            # sing-box 1.11+ 将 inet4_address/inet6_address 合并为 address 数组字段
            # 旧版 inet4_address/inet6_address 已废弃，使用会导致配置验证失败
            # 参考 sing-box 官方文档 TUN 示例：address 使用 172.19.0.1/30（仅 2 个可用地址，TUN 最小需求）
            # /28 给 14 个地址过多，/30 最精简
            tun_stack = self.db.get_setting("tun_stack", "gvisor")
            tun_address = self.db.get_setting("tun_address", "172.19.0.1/30")
            tun_address_list = [tun_address]
            # IPv6 支持（可选）
            tun_address_6 = self.db.get_setting("tun_address_6")
            if tun_address_6:
                tun_address_list.append(tun_address_6)
            # 持久化接口名：仅在首次使用时生成，后续复用同一名称
            # 每次随机生成会导致 sing-box 每次重启创建新 TUN 设备，
            # 旧设备可能未正确清理，路由表不一致
            import random, string
            tun_ifname = self.db.get_setting("tun_interface_name")
            if not tun_ifname:
                tun_ifname = "tun_" + ''.join(random.choices(string.ascii_lowercase, k=9))
                self.db.update_setting("tun_interface_name", tun_ifname)
            # 参考 sing-box 官方文档 TUN 示例：
            # 官方示例只有 type, address, auto_route, strict_route 四个字段
            # 其他字段按需添加，避免不兼容参数导致崩溃
            # strict_route 默认 False（NekoBox 默认值），因为默认 stack=gvisor 不支持 strict_route
            tun_inbound = {
                "type": "tun",
                "tag": "tun-in",
                "address": tun_address_list,
                "auto_route": True,
                "strict_route": self.db.get_setting("tun_strict_route", False),
            }
            # interface_name: Linux 上指定 TUN 接口名，便于管理和清理
            # 官方示例不指定（自动生成），但持久化接口名可避免重启后路由表不一致
            if platform.system() == "Linux":
                tun_inbound["interface_name"] = tun_ifname
            # ★ 始终显式设置 stack（NekoBox 关键设计）★
            # 不指定 stack 时 sing-box 使用内置默认值，不同版本可能不同。
            # gvisor 是最稳定的用户态网络栈，之前 system stack 导致堆损坏崩溃。
            # 始终显式设置，确保行为一致。
            # 参考 NekoBox BuildTunInbound：始终设置 stack 字段
            tun_inbound["stack"] = tun_stack
            # route_exclude_address: 排除特定 IP 段不走 TUN
            # 官方文档使用 ip_is_private 路由规则替代（已在 _build_route 中添加），
            # 但仍保留 route_exclude_address 作为额外保障，并排除代理服务器 IP。
            # 默认只排除多播/保留地址（私有 IP 由 ip_is_private 路由规则处理）
            default_exclude = [
                "224.0.0.0/4",
                "255.255.255.255/32",
            ]
            custom_exclude = self.db.get_setting("tun_route_exclude_address", "")
            if isinstance(custom_exclude, str) and custom_exclude.strip():
                tun_inbound["route_exclude_address"] = [a.strip() for a in custom_exclude.split(",") if a.strip()]
            else:
                tun_inbound["route_exclude_address"] = list(default_exclude)

            # ★ 关键修复：将代理服务器 IP 排除出 TUN 路由 ★
            # 当 TUN 启用时，所有流量被 TUN 网卡捕获。如果代理服务器 IP 不排除，
            # 代理出站连接代理服务器的流量会被 TUN 重新捕获 → 又走代理 → 死循环！
            # 这就是"国内网站能访问（走 direct），国外网站不能访问（走 proxy 但环路）"的根因。
            # 参考：NekoBox 通过 route_exclude_address 排除代理服务器 IP；
            # sing-box 官方文档也建议将代理服务器地址加入排除列表。
            if proxy_server_ips:
                proxy_exclude = [f"{ip}/32" for ip in proxy_server_ips]
                tun_inbound["route_exclude_address"] = tun_inbound.get("route_exclude_address", default_exclude) + proxy_exclude
                logger.info(f"TUN route_exclude_address: added proxy server IPs {proxy_exclude}")

            # TUN split routing: 指定走代理的地址列表（参考 NekoBox TunSplit）
            # route_include_address: 仅这些地址走 TUN（代理），其他不走
            # 如果设置了此选项，route_exclude_address 可能不再需要（两者互斥使用）
            custom_include = self.db.get_setting("tun_route_include_address", "")
            if isinstance(custom_include, str) and custom_include.strip():
                tun_inbound["route_include_address"] = [a.strip() for a in custom_include.split(",") if a.strip()]
            # gvisor stack 不支持 strict_route，自动关闭避免报错
            # NekoBox 也默认 strict_route=false，与 gvisor 兼容
            if tun_stack == "gvisor" and tun_inbound.get("strict_route"):
                tun_inbound["strict_route"] = False
                logger.info("TUN: gvisor stack does not support strict_route, auto-disabled")

            # enable_tun_routing: 自动将路由规则中直连目标的 IP CIDR 和 rule_set
            # 排除出 TUN（参考 NekoBox BuildTunInbound + enable_tun_routing）
            # 当启用时，直连流量在 OS 层面就不走 TUN 设备，直接通过物理网卡发出，
            # 提高直连性能并避免路由环路
            enable_tun_routing = self.db.get_setting("enable_tun_routing", False)
            if enable_tun_routing:
                direct_ip_cidrs, direct_rule_set_tags = self._collect_direct_ips_from_rules(rules, rule_sets)
                if direct_ip_cidrs:
                    tun_inbound["route_exclude_address"] = tun_inbound.get("route_exclude_address", default_exclude) + direct_ip_cidrs
                if direct_rule_set_tags:
                    tun_inbound["route_exclude_address_set"] = direct_rule_set_tags

            inbounds.append(tun_inbound)
            # 注意：NekoBox 中 dns-in 入站仅在 enable_dns_server 时才添加，
            # TUN 模式本身不需要 dns-in。TUN 通过虚拟网卡捕获所有流量，
            # DNS 查询由 DNS 规则 {"inbound": "tun-in", "action": "route", "server": "dns-remote"} 处理。
            # 不再自动添加 dns-in + hijack-dns，与 NekoBox 保持一致。

        # NTP 出站（参考 NekoBox ConfigBuilder.cpp:888-896）
        # sing-box NTP 配置为顶层对象，而非 outbound 类型
        # NekoBox: status->result->coreConfig["ntp"] = ntpObj;
        ntp_config = None
        ntp_enabled = self.db.get_setting("ntp_enabled", False)
        if ntp_enabled:
            ntp_config = {
                "enabled": True,
                "server": self.db.get_setting("ntp_server", "time.google.com"),
                "server_port": self.db.get_setting("ntp_server_port", 123),
                "interval": self.db.get_setting("ntp_interval", "30m"),
            }

        dns_config, dns_direct_server_ips = self._build_dns(tun_enabled, has_proxy=bool(enabled_tags), proxy_server_domains=proxy_server_domains)
        route = self._build_route(rules, rule_sets, enabled_tags, dns_direct_server_ips, tun_enabled, proxy_server_domains)

        log_config = {"level": self.db.get_setting("log_level", "info")}

        # On Windows with TUN mode, sing-box runs elevated via ShellExecuteExW
        # which doesn't capture stdout/stderr. Configure log output to file
        # so SingboxWorker can tail it for log capture.
        if tun_enabled and platform.system() == "Windows":
            log_file = self.config_dir / "sing-box.log"
            log_config["output"] = str(log_file)

        config = {
            "log": log_config,
            "dns": dns_config,
            "inbounds": inbounds,
            "outbounds": outbounds,
            "route": route,
            # ★ clash_api 和 cache_file 必须放在 experimental 下面 ★
            # sing-box 官方文档配置结构：experimental 包含 cache_file、clash_api、v2ray_api
            # 参考：https://sing-box.sagernet.org/configuration/experimental/
            # sing-box 1.8.0 将 cache_file 从 clash_api 内部提升为 experimental 的独立子项
            # 但 experimental 包装器本身始终存在，clash_api 和 cache_file 不在配置顶层
            # 之前错误地将它们放在顶层，导致 FATAL: unknown field "clash_api"
            "experimental": {
                "clash_api": {
                    "external_controller": f"127.0.0.1:{clash_api_port}",
                    "secret": self.get_clash_api_secret(),
                },
                # cache_file 持久化 selector 选择、fakeip 映射、RDRC 缓存
                # 不配置此项会导致重启后节点选择重置、fakeip 映射丢失
                "cache_file": {
                    "enabled": True,
                    "store_fakeip": True,
                    # store_rdrc: 缓存拒绝的 DNS 响应（since 1.9.0，1.14.0 废弃但仍可用）
                    # store_dns: 缓存完整 DNS（since 1.14.0，含 RDRC 功能），旧版本不支持
                    # 使用 store_rdrc 兼容所有版本；1.14.0+ 可同时设置 store_dns
                    "store_rdrc": True,
                },
            },
        }
        # certificate 配置：仅在使用 system 证书库时添加
        # 默认情况下 sing-box 使用 Go 内置的 Mozilla 证书库，无需此字段。
        # 某些系统可能不支持 store: "system"，导致 sing-box 启动失败（exit code 1），
        # 而 sing-box check 不会检查运行时的证书库可用性。
        # 因此仅在用户明确启用时才添加此配置。
        cert_store = self.db.get_setting("certificate_store", "")
        if cert_store:
            config["certificate"] = {"store": cert_store}
        # NTP 顶层配置（参考 NekoBox ConfigBuilder.cpp:895）
        # sing-box NTP 为顶层 section，非 outbound 类型
        if ntp_config:
            config["ntp"] = ntp_config
        return config

    @staticmethod
    def _parse_dns_address(address: str) -> dict:
        """将旧版 DNS address URL 解析为 sing-box 1.12+ 新格式字段

        旧格式: "tls://8.8.8.8", "https://1.1.1.1/dns-query", "8.8.8.8"
        新格式: {"type": "tls", "server": "8.8.8.8"}, {"type": "https", "server": "1.1.1.1"}, ...
        """
        from urllib.parse import urlparse
        address = address.strip()
        if not address or address == "local":
            return {"type": "local"}
        if address.startswith("fakeip"):
            return {"type": "fakeip"}
        if address.startswith("dhcp://"):
            iface = address[7:]  # 去掉 "dhcp://" 前缀
            result = {"type": "dhcp"}
            if iface and iface != "auto":
                result["interface"] = iface
            return result
        if address.startswith("rcode://"):
            # rcode 不再作为 DNS server，改用 DNS rule action
            # 此处返回标记，调用方应将其转为 rule action
            code = address[8:].upper()
            return {"type": "rcode", "rcode": code}
        # 标准 URL 格式：protocol://host/path
        parsed = urlparse(address)
        if parsed.scheme:
            dns_type = parsed.scheme
            server = parsed.hostname or parsed.netloc
            result = {"type": dns_type, "server": server}
            # 保留非默认路径（如 /dns-query），sing-box 1.12+ 新格式要求
            # 默认路径（/ 或空）不需要显式指定
            if parsed.path and parsed.path != '/' and parsed.path != '':
                result["path"] = parsed.path
            # 保留非标准端口（sing-box 1.12+ 需要 server_port 字段）
            if parsed.port and parsed.port != {
                'https': 443, 'http': 80, 'tls': 853, 'quic': 853, 'h3': 853
            }.get(dns_type):
                result["server_port"] = parsed.port
            return result
        else:
            # 无协议前缀的裸 IP（如 "8.8.8.8"），默认为 UDP
            return {"type": "udp", "server": address}

    def _build_dns(self, tun_enabled: bool = False, has_proxy: bool = False, proxy_server_domains: list[str] | None = None) -> tuple[dict, list[str]]:
        # DNS 三服务器架构（参考 NekoBox BuildConfigSingBox DNS 部分）
        # NekoBox 使用三个 DNS 服务器，避免 domain_resolver 循环依赖：
        #   dns-remote: 远程 DNS，通过代理连接（detour: "proxy"），domain_resolver: "dns-local"
        #   dns-direct: 直连 DNS，用于解析代理服务器域名等，domain_resolver: "dns-local"
        #   dns-local: 基础 DNS，仅用于域名解析（domain_resolver 目标），不参与业务流量
        #
        # 旧版双服务器架构的问题：当 remote 和 local DNS 都使用域名地址时，
        #   remote.domain_resolver = "local"，local.domain_resolver = "remote"
        #   形成循环依赖，导致 DNS 解析永远无法完成
        # 三服务器架构通过独立的 dns-local（通常是 IP 或 local 类型）打破循环
        #
        # sing-box 1.12+ 新 DNS 格式：使用 type + server 替代旧版 address URL
        # 旧版 address_resolver 重命名为 domain_resolver
        # sing-box 1.12+ DNS 规则必须包含 action 字段
        #
        # 返回值：(dns_config, dns_direct_server_ips) - dns_direct_server_ips 仅包含直连 DNS 的 IP，用于添加直连路由规则
        dns_server_1 = self.db.get_setting("dns_server_1", "tls://8.8.8.8")
        dns_server_2 = self.db.get_setting("dns_server_2", "https://223.5.5.5/dns-query")

        # --- 远程 DNS（通过代理连接）---
        remote_fields = self._parse_dns_address(dns_server_1)
        # ★ 关键：显式设置 detour: "proxy"（NekoBox 关键设计）★
        # 当 TUN 启用时，所有网络连接（包括 DNS 模块的连接）可能被 TUN 设备捕获。
        # 如果不设置 detour，DNS 模块到 8.8.8.8 的连接会被 TUN 捕获 → 又走 sing-box
        # → DNS 模块 → 8.8.8.8 → TUN 捕获 → 无限循环！
        # 设置 detour: "proxy" 确保 DNS 连接直接走 proxy 出站，绕过 TUN 设备。
        # 参考 NekoBox ConfigBuilder.cpp：dns-remote detour = proxy
        remote_server = {"tag": "dns-remote", "detour": "proxy", **remote_fields}
        # 如果远程 DNS 服务器使用域名地址，需要 domain_resolver 解析该域名
        # domain_resolver 指向 dns-local（基础 DNS），避免循环依赖
        remote_server_addr = remote_fields.get("server", "")
        if remote_server_addr and not self._is_ip_address(remote_server_addr):
            remote_server["domain_resolver"] = "dns-local"

        # --- 直连 DNS ---
        local_fields = self._parse_dns_address(dns_server_2)
        # ★ sing-box 1.12+ 新 DNS 格式：不能设置 detour: "direct" ★
        # sing-box 1.12+ 的 DNS 服务器默认使用 dialer（等效于空的 direct outbound），
        # 设置 detour: "direct" 会被拒绝：FATAL "detour to an empty direct outbound makes no sense"
        # 参考 sing-box 作者 nekohasekai 的回复：
        # https://github.com/SagerNet/sing-box/issues/3585
        # "the new one uses dialer just like outbound, which is equivalent to using an empty
        # direct outbound by default. So just simply remove detour: direct"
        # dns-direct 的直连由路由规则保证（route rule 将 dns-direct IP 路由到 direct 出站）
        local_server = {"tag": "dns-direct", **local_fields}
        # 如果直连 DNS 服务器使用域名地址，需要 domain_resolver 解析该域名
        local_server_addr = local_fields.get("server", "")
        if local_server_addr and not self._is_ip_address(local_server_addr):
            local_server["domain_resolver"] = "dns-local"

        # --- 基础 DNS（underlying DNS，仅用于域名解析）---
        # NekoBox: dnsLocalAddress 默认为 "local"（即 type: "local"），
        # 用户可配置为 IP 地址（如 8.8.8.8）。
        # 此服务器不参与业务 DNS 查询，仅作为 domain_resolver 目标。
        underlying_dns = self.db.get_setting("underlying_dns", "local")
        local_dns_fields = self._parse_dns_address(underlying_dns)
        underlying_server = {"tag": "dns-local", **local_dns_fields}

        # 收集直连 DNS 服务器的 IP 地址（用于添加直连路由规则）
        # ★ 重要：只收集直连 DNS（dns-direct）的 IP，不收集远程 DNS（dns-remote）的 IP ★
        # 远程 DNS 应走代理出站（route final = "proxy"），如果添加直连路由规则
        # 会导致远程 DNS（如 8.8.8.8）直连，在中国大陆可能被污染或不可达
        dns_direct_server_ips = []
        direct_dns_ip = local_fields.get("server", "")
        if direct_dns_ip and self._is_ip_address(direct_dns_ip):
            dns_direct_server_ips.append(direct_dns_ip)

        dns_rules = []

        # 代理服务器域名的 DNS 直连规则（NekoBox 关键设计）
        # 当代理服务器使用域名地址（如 server.example.com）时，
        # 其 DNS 解析必须走直连（dns-direct），否则会形成循环依赖：
        # 代理需要 DNS → DNS 走代理(detour: proxy) → 代理需要 DNS → ...
        # 这会导致代理连接永远无法建立（DNS 解析超时）
        # 注意：此规则必须放在 TUN DNS 规则之前，确保代理服务器域名走直连 DNS，
        # 而不是被 TUN 规则拦截后走远程 DNS（通过代理），形成循环。
        # 参考 NekoBox ConfigBuilder.cpp:327-330
        #
        # 注意：NekoBox 还使用 {"outbound": "any", "server": "dns-direct"} 规则
        # 来确保所有出站触发的 DNS 查询走直连 DNS，但 sing-box 1.12+ 已废弃
        # DNS 规则中的 outbound 匹配字段，改用 route.default_domain_resolver 替代。
        # 我们设置 default_domain_resolver: "dns-direct" 来达到相同效果。
        if proxy_server_domains:
            dns_rules.append({
                "domain": proxy_server_domains,
                "action": "route",
                "server": "dns-direct",
            })

        # TUN 模式下 DNS 查询路由
        # 参考 NekoBox：NekoBox 不添加 inbound: tun-in 的 DNS 规则，
        # 而是依赖 DNS final 服务器（默认 dns-remote）处理 TUN 的 DNS 查询。
        # 之前版本添加了 {"inbound": "tun-in", "server": "dns-remote"} 规则，
        # 但这会导致所有 TUN DNS 查询都走 dns-remote（通过代理），
        # 而代理服务器域名的直连 DNS 规则被跳过（inbound 规则优先匹配），
        # 造成循环依赖。移除此规则后，DNS 查询按顺序匹配：
        # 1. 代理服务器域名 → dns-direct（直连）
        # 2. 其他查询 → dns final（dns-remote，通过代理）
        # 这与 NekoBox 行为一致，且避免了循环依赖问题。
        # TUN 的 DNS 劫持由路由规则 {"protocol": "dns", "action": "hijack-dns"} 处理，
        # 不需要在 DNS 规则层额外指定 inbound。

        # localhost 解析规则（参考 NekoBox ConfigBuilder.cpp:1131-1154）
        # TUN 模式下系统 DNS 不可用，必须提供 localhost 解析
        # 否则本地服务（如数据库、缓存）可能无法解析 localhost
        # NekoBox 使用 action: "predefined" 返回预定义 DNS 响应
        # ★ 注意：domain 字段必须使用数组格式 ★
        # 虽然 sing-box 的 Listable 类型接受单字符串和数组，
        # 但使用数组格式更规范，避免某些版本的解析问题
        dns_rules.append({
            "domain": ["localhost"],
            "action": "predefined",
            "query_type": "A",
            "rcode": "NOERROR",
            "answer": "localhost. IN A 127.0.0.1",
        })
        dns_rules.append({
            "domain": ["localhost"],
            "action": "predefined",
            "query_type": "AAAA",
            "rcode": "NOERROR",
            "answer": "localhost. IN AAAA ::1",
        })

        # DNS 服务器排列顺序（参考 NekoBox）
        # NekoBox: 如果 dns_final_out_direct，dns-direct 放在前面；否则 dns-remote 在前面
        # 默认 dns-remote 在前，作为主要 DNS
        dns_final_out_direct = self.db.get_setting("dns_final_out_direct", False)
        dns_final = "dns-direct" if dns_final_out_direct else "dns-remote"
        if dns_final_out_direct:
            servers = [local_server, remote_server, underlying_server]
        else:
            servers = [remote_server, local_server, underlying_server]

        dns_config = {
            "servers": servers,
            "rules": dns_rules,
            "final": dns_final,
            "strategy": self.db.get_setting("dns_strategy", "prefer_ipv4"),
        }

        # fakeip DNS 支持（参考 NekoBox BuildConfigSingBox DNS fakeip 部分）
        # 当任一 DNS 服务器类型为 fakeip 时，需要在 dns_config 中添加 fakeip IP 池配置。
        # fakeip 模式下，DNS 查询返回假 IP 地址，实际连接时再通过 sniff 解析真实域名，
        # 适用于游戏/流媒体等需要低延迟 DNS 解析的场景。
        has_fakeip = any(
            s.get('type') == 'fakeip' for s in servers
        )
        if has_fakeip:
            inet4_range = self.db.get_setting("fakeip_inet4_range", "198.18.0.1/15")
            inet6_range = self.db.get_setting("fakeip_inet6_range", "fc00::/18")
            dns_config["fakeip"] = {
                "enabled": True,
                "inet4_range": inet4_range,
                "inet6_range": inet6_range,
            }

        return dns_config, dns_direct_server_ips

    @staticmethod
    def _convert_to_jsdelivr(url: str, cdn_type: str = 'testingcf') -> str:
        """将 GitHub rule_set URL 转换为 jsdelivr CDN 镜像（参考 NekoBox）

        支持的 CDN 类型：
        - testingcf: https://testingcf.jsdelivr.net/gh/user/repo@branch/path
        - gcore: https://gcore.jsdelivr.net/gh/user/repo@branch/path
        - quantil: https://quantil.jsdelivr.net/gh/user/repo@branch/path
        - fastly: https://fastly.jsdelivr.net/gh/user/repo@branch/path
        - cdn: https://cdn.jsdelivr.net/gh/user/repo@branch/path

        仅转换 raw.githubusercontent.com 和 github.com URLs
        """
        import re
        # Match: https://raw.githubusercontent.com/user/repo/branch/path
        m = re.match(r'https?://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)', url)
        if m:
            user, repo, branch, path = m.groups()
            return f"https://{cdn_type}.jsdelivr.net/gh/{user}/{repo}@{branch}/{path}"
        # Match: https://github.com/user/repo/releases/download/tag/file
        m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/(.+)', url)
        if m:
            user, repo, tag, file = m.groups()
            return f"https://{cdn_type}.jsdelivr.net/gh/{user}/{repo}@{tag}/{file}"
        return url  # 非 GitHub URL，原样返回

    @staticmethod
    def _is_ip_address(s: str) -> bool:
        """检查字符串是否为 IP 地址（而非域名）"""
        import re as _re
        # IPv4
        if _re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', s):
            return True
        # IPv6（简化检测：包含冒号）
        if ':' in s and _re.match(r'^[0-9a-fA-F:]+$', s):
            return True
        return False

    def _build_outbounds(self, nodes) -> tuple[list, list, list[str], list[str]]:
        """构建 outbounds 列表，同时返回 enabled_tags、代理服务器域名和 IP 列表

        注意：sing-box 1.11.0 弃用、1.13.0 移除了 dns 和 block outbound 类型，
        改用 route rule action "hijack-dns" 和 "reject" 替代（见 _build_route）。
        参考 NekoBox RouteEntity.cpp:183 - outboundID == -4 时 action = "hijack-dns"。
        参考 sing-box 官方文档：Legacy special outbounds (block / dns) are deprecated
        and can be replaced by rule actions.

        返回值：(outbounds, enabled_tags, proxy_server_domains, proxy_server_ips)
        - proxy_server_domains: 使用域名地址的代理服务器域名列表，
          用于添加 DNS 直连规则和路由直连规则，避免循环依赖（DNS→proxy→DNS→...）
        - proxy_server_ips: 使用 IP 地址的代理服务器 IP 列表，
          用于添加 TUN route_exclude_address，在 OS 层面避免路由环路
        """
        outbounds = [
            {"type": "direct", "tag": "direct"},
        ]
        enabled_tags = []
        proxy_server_domains = []  # 收集使用域名地址的代理服务器
        proxy_server_ips = []  # 收集使用 IP 地址的代理服务器
        for node in nodes:
            # 防御性检查：get_all_nodes_raw() 应返回 snake_case 键名，
            # 如果误返回 camelCase 数据（如 isEnabled），此处 fallback 避免将所有节点视为禁用。
            # 但这也意味着数据格式错误会被静默容忍，应在 _build_config 入口处验证数据格式。
            is_enabled = node.get('is_enabled', node.get('isEnabled', 0))
            if not is_enabled:
                continue
            outbound = self._node_to_outbound(node)
            if outbound is None:
                # 配置校验失败（缺失必需字段），跳过该节点
                continue
            outbounds.append(outbound)
            tag = node.get('tag') or node.get('id', '')  # 防御性：tag 为空时 fallback 到 id
            enabled_tags.append(tag)
            # 收集代理服务器域名和 IP（NekoBox 关键设计 + TUN 防环路）
            # 当代理服务器使用域名地址时，其 DNS 解析必须走直连，
            # 否则与 detour: "proxy" 形成循环依赖
            # 同时需要路由直连规则和 TUN 排除，避免 TUN 路由环路
            server_addr = node.get('address', '')
            if server_addr:
                if self._is_ip_address(server_addr):
                    proxy_server_ips.append(server_addr)
                else:
                    proxy_server_domains.append(server_addr)
        # ★ proxy selector 必须包含 "auto" 选项 ★
        # 用户可以在前端选择 "auto"（urltest 自动选择延迟最低的节点），
        # 如果 selector 的 outbounds 不包含 "auto"，Clash API PUT /proxies/proxy
        # 会拒绝切换（返回 400 Bad Request），导致节点切换失败。
        # 参考 NekoBox：selector 包含所有可用节点 + "auto" + "direct"
        auto_tag_list = enabled_tags + ["auto", "direct"] if enabled_tags else ["direct"]
        outbounds.append({
            "type": "selector",
            "tag": "proxy",
            "outbounds": auto_tag_list,
            "default": "auto" if enabled_tags else "direct"
        })
        if enabled_tags:
            outbounds.append({
                "type": "urltest",
                "tag": "auto",
                "outbounds": enabled_tags,
                "url": "https://www.gstatic.com/generate_204",
                "interval": "5m"
            })
        return outbounds, enabled_tags, proxy_server_domains, proxy_server_ips

    def _node_to_outbound(self, node):
        outbound_map = {
            "vmess": self._vmess_outbound,
            "vless": self._vless_outbound,
            "trojan": self._trojan_outbound,
            "shadowsocks": self._shadowsocks_outbound,
            "hysteria2": self._hysteria2_outbound,
            "wireguard": self._wireguard_outbound,
            "tuic": self._tuic_outbound,
        }
        builder = outbound_map.get(node.get('protocol'))
        if builder:
            # 配置完整性校验：缺失关键字段时记录警告并跳过该节点
            # 避免生成无效配置导致 sing-box 启动失败
            config = node.get('config', {})
            protocol = node.get('protocol', '')
            required_fields = {
                'vmess': ['uuid'],
                'vless': ['uuid'],
                'trojan': ['password'],
                'shadowsocks': ['method', 'password'],
                'hysteria2': ['password'],
                'wireguard': ['private_key', 'peer_public_key'],
                'tuic': ['uuid', 'password'],
            }
            # 兼容 camelCase 和 snake_case 键名（前端传入 camelCase，后端内部使用 snake_case）
            field_aliases = {
                'private_key': ['privateKey', 'private_key'],
                'peer_public_key': ['peerPublicKey', 'peer_public_key'],
            }
            missing = []
            for field in required_fields.get(protocol, []):
                aliases = field_aliases.get(field, [field])
                if not any(config.get(a) for a in aliases):
                    missing.append(field)
            if missing:
                logger.warning(f"Node '{node.get('tag', 'unknown')}' ({protocol}) missing required config fields: {missing}, skipping")
                return None
            return builder(node)
        raise ValueError(f"Unsupported protocol: {node.get('protocol')}")

    def _add_utls_fingerprint(self, tls_obj: dict, node_config: dict):
        """为 TLS 配置添加 uTLS 指纹（参考 NekoBox Bean2CoreObj_box.cpp:254-267）

        优先级：节点级 utlsFingerprint > 全局默认 utls_fingerprint
        Reality 协议自动使用 "random" 指纹（NekoBox 行为）
        sing-box 格式：{"utls": {"enabled": true, "fingerprint": "chrome"}}
        """
        fp = node_config.get('utlsFingerprint', '')
        if not fp:
            # 使用全局默认 uTLS 指纹
            fp = self.db.get_setting('utls_fingerprint', '')
        # Reality 协议默认使用 random 指纹（NekoBox: if (fp.isEmpty()) fp = "random";）
        if not fp and node_config.get('reality'):
            fp = 'random'
        if fp:
            tls_obj["utls"] = {"enabled": True, "fingerprint": fp}

    def _vmess_outbound(self, node):
        config = node.get('config', {})
        out = {
            "type": "vmess", "tag": node.get('tag'), "server": node.get('address'),
            "server_port": node.get('port'), "uuid": config.get('uuid', ''),
            "security": config.get('security', 'auto'),
            # alter_id 已移除：sing-box 1.8+ 强制使用 VMess AEAD（等价于 alter_id=0），配置中不再接受此字段
        }
        transport = config.get('network', 'tcp')
        if transport == 'ws':
            out["transport"] = {"type": "ws", "path": config.get('wsPath', '/'), "headers": config.get('wsHeaders', {})}
        elif transport == 'grpc':
            out["transport"] = {"type": "grpc", "service_name": config.get('grpcServiceName', '')}
        if config.get('tls'):
            out["tls"] = {"enabled": True, "server_name": config.get('sni', node.get('address')), "insecure": config.get('allowInsecure', False)}
            self._add_utls_fingerprint(out["tls"], config)
        self._add_multiplex(out, config)
        return out

    def _vless_outbound(self, node):
        config = node.get('config', {})
        out = {"type": "vless", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "uuid": config.get('uuid', '')}
        flow = config.get('flow', '')
        if flow:  # 仅为非空 flow 添加字段，避免 sing-box 验证错误
            out["flow"] = flow
        if config.get('tls'):
            out["tls"] = {"enabled": True, "server_name": config.get('sni', node.get('address')), "insecure": config.get('allowInsecure', False)}
            if config.get('reality'):
                out["tls"]["reality"] = {"enabled": True, "public_key": config.get('realityPublicKey', ''), "short_id": config.get('realityShortId', '')}
            self._add_utls_fingerprint(out["tls"], config)
        transport = config.get('network', 'tcp')
        if transport == 'ws':
            out["transport"] = {"type": "ws", "path": config.get('wsPath', '/'), "headers": config.get('wsHeaders', {})}
        elif transport == 'grpc':
            out["transport"] = {"type": "grpc", "service_name": config.get('grpcServiceName', '')}
        self._add_multiplex(out, config)
        return out

    def _trojan_outbound(self, node):
        config = node.get('config', {})
        out = {"type": "trojan", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "password": config.get('password', ''), "tls": {"enabled": True, "server_name": config.get('sni', node.get('address')), "insecure": config.get('allowInsecure', False)}}
        self._add_utls_fingerprint(out["tls"], config)
        transport = config.get('network', 'tcp')
        if transport == 'ws':
            out["transport"] = {"type": "ws", "path": config.get('wsPath', '/'), "headers": config.get('wsHeaders', {})}
        elif transport == 'grpc':
            out["transport"] = {"type": "grpc", "service_name": config.get('grpcServiceName', '')}
        self._add_multiplex(out, config)
        return out

    def _shadowsocks_outbound(self, node):
        config = node.get('config', {})
        out = {"type": "shadowsocks", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "method": config.get('method', ''), "password": config.get('password', '')}
        self._add_multiplex(out, config)
        return out

    def _hysteria2_outbound(self, node):
        config = node.get('config', {})
        out = {"type": "hysteria2", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "password": config.get('password', '')}
        # hysteria2 始终使用 TLS，不依赖 sni 判断
        out["tls"] = {"enabled": True, "server_name": config.get('sni', node.get('address')), "insecure": config.get('allowInsecure', False)}
        self._add_utls_fingerprint(out["tls"], config)
        if config.get('obfs'):
            obfs_type = config.get('obfs_type', '').strip()
            if obfs_type:  # 仅在 obfs_type 非空时添加，避免生成无效的 "type": "" 配置
                out["obfs"] = {"type": obfs_type, "password": config.get('obfs_password', '')}
        return out

    def _wireguard_outbound(self, node):
        config = node.get('config', {})
        # reserved 和 localAddress 可能为空列表或缺失，统一处理为列表
        # 注意：前端使用 camelCase 键名（privateKey/peerPublicKey/localAddress），
        # 与其他协议 config 键名风格一致
        reserved = config.get('reserved', []) or []
        local_address = config.get('localAddress', config.get('local_address', [])) or []
        private_key = config.get('privateKey', config.get('private_key', ''))
        peer_public_key = config.get('peerPublicKey', config.get('peer_public_key', ''))
        # 校验 local_address CIDR 格式：无效 CIDR 会导致 sing-box 启动失败
        # 注意：与其它协议 builder 一致，返回 None 跳过该节点（而非 raise ValueError 终止整个配置生成）
        import re as _re
        _cidr_pattern = _re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,3}$')
        for addr in local_address:
            if not _cidr_pattern.match(addr):
                logger.error(f"WireGuard 节点 '{node.get('tag')}' 的 local_address '{addr}' 不是有效的 CIDR 格式（如 172.19.0.1/30），跳过该节点")
                return None  # 返回 None 跳过该节点，而非 raise ValueError
        return {"type": "wireguard", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "private_key": private_key, "peer_public_key": peer_public_key, "reserved": reserved, "local_address": local_address}

    def _tuic_outbound(self, node):
        config = node.get('config', {})
        tls_obj = {
            "enabled": True,
            "server_name": config.get('sni', node.get('address')),
            "insecure": config.get('allowInsecure', config.get('allow_insecure', False)),
        }
        # TUIC TLS: alpn 和 disable_sni 支持（参考 NekoBox QUICBean.hpp/QUICBean.cpp）
        alpn = config.get('alpn', '')
        if alpn:
            # 支持逗号分隔的多个 alpn 值
            tls_obj["alpn"] = [a.strip() for a in alpn.split(',') if a.strip()]
        if config.get('disableSni', config.get('disable_sni', False)):
            tls_obj["disable_sni"] = True
        out = {
            "type": "tuic",
            "tag": node.get('tag'),
            "server": node.get('address'),
            "server_port": node.get('port'),
            "uuid": config.get('uuid', ''),
            "password": config.get('password', ''),
            "tls": tls_obj,
        }
        self._add_utls_fingerprint(out["tls"], config)
        # TUIC 特有选项（参考 NekoBox QUICBean.hpp:49-55, QUICBean.cpp:172-183）
        # 默认 congestion_control 为 "bbr"（与 NekoBox 一致，QUICBean.hpp:51）
        congestion_control = config.get('congestionControl', config.get('congestion_control', 'bbr'))
        if congestion_control:
            out["congestion_control"] = congestion_control
        # udp_over_stream 与 udp_relay_mode 互斥（参考 NekoBox QUICBean.cpp:176-179）
        # 当 uos=true 时使用 udp_over_stream，否则使用 udp_relay_mode
        uos = config.get('uos', config.get('udp_over_stream', False))
        if uos:
            out["udp_over_stream"] = True
        else:
            udp_relay_mode = config.get('udpRelayMode', config.get('udp_relay_mode', 'native'))
            if udp_relay_mode:
                out["udp_relay_mode"] = udp_relay_mode
        if config.get('zeroRttHandshake', config.get('zero_rtt_handshake', False)):
            out["zero_rtt_handshake"] = True
        # heartbeat（参考 NekoBox QUICBean.hpp:54, QUICBean.cpp:182）
        heartbeat = config.get('heartbeat', '')
        if heartbeat and heartbeat.strip():
            out["heartbeat"] = heartbeat.strip()
        return out

    def _add_multiplex(self, out: dict, node_config: dict):
        """为出站配置添加 multiplex (mux) 字段（参考 NekoBox ConfigBuilder.cpp:558-604）

        sing-box multiplex 格式：
        {
            "multiplex": {
                "enabled": true,
                "protocol": "h2mux|smux|yamux",
                "max_streams": 8,
                "padding": false,
                "brutal": {"enabled": true, "up_mbps": 100, "down_mbps": 100}
            }
        }

        节点 config 字段（camelCase，与前端一致）：
        - muxEnabled: boolean - 是否启用 mux
        - muxProtocol: string - 协议 (h2mux/smux/yamux)
        - muxMaxStreams: number - 最大并发流
        - muxPadding: boolean - 是否启用填充
        - brutalEnabled: boolean - 是否启用 Brutal 拥塞控制
        - brutalSpeed: number - Brutal 速度 (Mbps)

        限制：
        - VLESS + flow (XTLS) 不支持 mux（NekoBox: vless with flow → needMux = false）
        - grpc/quic 传输不支持 mux
        """
        # 检查是否启用 mux
        mux_enabled = node_config.get('muxEnabled', False)
        if not mux_enabled:
            return

        # VLESS + flow 不支持 mux
        if out.get('flow'):
            return

        # grpc/quic/http+tls 传输不支持 mux（参考 NekoBox ConfigBuilder.cpp:564-568）
        transport = out.get('transport', {})
        if isinstance(transport, dict):
            ttype = transport.get('type', '')
            if ttype in ('grpc', 'quic'):
                return

        mux_obj = {
            "enabled": True,
            "protocol": node_config.get('muxProtocol', 'h2mux'),
            "max_streams": node_config.get('muxMaxStreams', 8),
            "padding": node_config.get('muxPadding', False),
        }

        # Brutal 拥塞控制（参考 NekoBox ConfigBuilder.cpp:594-601）
        if node_config.get('brutalEnabled'):
            brutal_speed = node_config.get('brutalSpeed', 100)
            mux_obj["brutal"] = {
                "enabled": True,
                "up_mbps": brutal_speed,
                "down_mbps": brutal_speed,
            }
            # Brutal 模式下 max_connections=1（NekoBox 行为）
            mux_obj["max_connections"] = 1

        out["multiplex"] = mux_obj

    def _build_route(self, rules, rule_sets, enabled_tags: list, dns_direct_server_ips: list[str] | None = None, tun_enabled: bool = False, proxy_server_domains: list[str] | None = None) -> dict:
        route_rules = []
        # ★ 路由规则顺序必须与 sing-box 官方文档示例一致 ★
        # 官方文档 TUN Client 示例：sniff → hijack-dns → ip_is_private → ...
        # 参考：https://sing-box.sagernet.org/zh/manual/proxy/client/

        # 1. sniff：协议嗅探，提取连接中的域名信息（TLS SNI / HTTP Host）
        # sing-box 1.13.0 移除了 inbound 上的 sniff 字段，改用 route rule action。
        # sniff 不指定 inbound，默认应用于所有入站。
        # 参考 sing-box 迁移指南：旧的入站字段 → 规则动作
        route_rules.append({"action": "sniff"})

        # 2. hijack-dns：DNS 协议流量劫持到 sing-box DNS 模块处理
        # DNS 协议流量必须通过 sing-box DNS 模块处理（而非 direct outbound），
        # 否则 DNS 查询会绕过 DNS 分流、缓存等逻辑，导致：
        # 1. TUN 模式下 DNS 劫持失效
        # 2. DNS 分流规则不生效
        # 3. 域名解析可能失败（TUN 网络环境下 DNS 服务器不可达）
        # sing-box 1.11.0+ 弃用 dns outbound，1.13.0 完全移除。
        # 参考 sing-box 迁移指南：旧的特殊出站 → 规则动作
        route_rules.append({"protocol": "dns", "action": "hijack-dns"})

        # 3. ip_is_private：私有 IP 直连
        # 官方文档所有 TUN 示例都包含此规则。
        # 私有 IP 地址（局域网、回环等）直连，避免 TUN 劫持局域网流量。
        route_rules.append({"ip_is_private": True, "outbound": "direct"})

        # 4. 直连 DNS 服务器 IP 直连路由规则
        # ★ 重要：只将直连 DNS（dns-direct）的 IP 添加到直连路由，
        # 远程 DNS（dns-remote）的 IP 应走代理（route final = "proxy"），
        # 否则在中国大陆 8.8.8.8 等远程 DNS 直连会失败或被污染 ★
        # dns-direct_server_ips 仅包含直连 DNS 服务器的 IP（如 223.5.5.5）
        if enabled_tags and dns_direct_server_ips:
            route_rules.append({"ip_cidr": [f"{ip}/32" for ip in dns_direct_server_ips], "outbound": "direct"})

        # ★ 关键修复：代理服务器域名直连路由规则 ★
        # TUN 模式下，当代理服务器使用域名地址时，即使 DNS 解析走直连（dns-direct），
        # 解析出 IP 后的 TCP/UDP 连接仍会被 TUN 捕获并路由到 proxy 出站，形成环路：
        #   用户访问 Google → 路由到 proxy 出站 → proxy 连接代理服务器域名
        #   → TUN 捕获 → 路由到 proxy 出站 → 再次连接代理服务器 → 死循环
        # 解决方案：在路由规则中将代理服务器域名的连接直连（direct），
        # 确保代理出站连接代理服务器的流量不经过 TUN 代理环路。
        # 此规则必须在用户规则之前，优先匹配。
        # 参考 NekoBox ConfigBuilder.cpp:811-815（directDomains 收集代理服务器域名），
        # 以及 sing-box 官方文档关于 TUN 路由环路的说明。
        if proxy_server_domains:
            route_rules.append({"domain": proxy_server_domains, "outbound": "direct"})
            logger.info(f"Route: added proxy server domains direct rule for {proxy_server_domains}")

        # 构建 rule_set id → tag 映射（sing-box 配置中 rule_set 引用 tag 而非 id）
        rs_id_to_tag = {rs.get('id'): rs.get('tag') for rs in rule_sets}
        # 合法的 outbound tag 集合（用于验证规则引用的 outbound 是否存在）
        # 注意："auto" 仅在 enabled_tags 非空时有效（urltest outbound 仅在有可用节点时创建）
        # 如果所有节点被禁用，"auto" outbound 不存在，引用它的规则应 fallback 到 direct
        valid_outbound_tags = set(enabled_tags) | {"direct", "proxy"}
        if enabled_tags:
            valid_outbound_tags.add("auto")
        # 数组类型字段，需要从 JSON 字符串解析（使用类常量 RULE_ARRAY_FIELDS 避免重复定义）
        # 注意：rule_field_map 和 RULE_ARRAY_FIELDS 有部分重叠（如 domain, domain_suffix 等），
        # 这是设计意图：rule_field_map 负责将数据库列名映射到 sing-box 配置键名，
        # RULE_ARRAY_FIELDS 标识哪些字段需要 JSON 解析，两者职责不同
        # 移到循环外部，避免每次迭代重复创建字典
        rule_field_map = {
            "domain": "domain", "domain_suffix": "domain_suffix", "domain_keyword": "domain_keyword",
            "domain_regex": "domain_regex", "geosite": "geosite", "ip_cidr": "ip_cidr",
            "ip_is_private": "ip_is_private", "geoip": "geoip", "source_ip_cidr": "source_ip_cidr",
            "source_geoip": "source_geoip", "port": "port", "port_range": "port_range",
            "source_port": "source_port", "source_port_range": "source_port_range",
            "process_name": "process_name", "process_path": "process_path",
            "package_name": "package_name", "network": "network", "protocol": "protocol",
            "user_id": "user_id", "clash_mode": "clash_mode", "invert": "invert",
        }
        for rule in rules:
            if not rule.get('is_enabled', 0):
                continue

            # 参考 NekoBox RouteRule::get_rule_json()：根据 action 类型生成不同的 sing-box 规则
            # action 字段（默认 'route'）：route / reject / sniff / resolve / hijack-dns
            action = rule.get('action', 'route') or 'route'

            # --- route action: 路由到指定出站（默认行为，向后兼容旧规则）---
            if action == 'route':
                outbound = rule.get('outbound_tag')
                # 兼容旧数据：outbound_tag 为 "block" 时转为 reject action
                # （sing-box 1.13.0 移除了 block outbound，改用 reject rule action）
                # 必须在 valid_outbound_tags 检查之前处理，避免产生误导性警告日志
                if outbound == 'block':
                    action = 'reject'
                    route_rule = {"action": "reject"}
                else:
                    # 验证 outbound_tag 是否存在于 outbounds 中，无效则 fallback 到 direct
                    if outbound not in valid_outbound_tags:
                        logger.warning(f"Rule '{rule.get('name', '')}' references unknown outbound '{outbound}', falling back to 'direct'")
                        outbound = "direct"
                    route_rule = {"outbound": outbound}

            # --- reject action: 拒绝连接（广告拦截、访问控制等）---
            # 参考 NekoBox：当 outbound_tag 为 "block" 时自动转为 reject action
            # sing-box reject action 格式：{"action": "reject", "method": "default"}
            # method 可选值：default（RST/ICMP 不可达）、conn-reset（TCP RST）
            elif action == 'reject':
                route_rule = {"action": "reject"}
                reject_method = rule.get('reject_method', 'default') or 'default'
                if reject_method and reject_method != 'default':
                    route_rule["method"] = reject_method

            # --- sniff action: 协议嗅探 ---
            # 参考 sing-box 官方文档：sniff 规则不指定 inbound，应用于所有入站
            elif action == 'sniff':
                route_rule = {"action": "sniff"}

            # --- resolve action: DNS 解析（将域名解析请求路由到指定 DNS 服务器）---
            # 参考 NekoBox：resolve 规则用于在路由层面触发域名解析
            # 格式：{"action": "resolve", "server": "dns-direct"}
            elif action == 'resolve':
                route_rule = {"action": "resolve"}
                resolve_server = rule.get('resolve_server', 'dns-direct') or 'dns-direct'
                route_rule["server"] = resolve_server

            # --- hijack-dns action: DNS 劫持（将 DNS 查询劫持到 sing-box DNS 模块）---
            # 参考 NekoBox：hijack-dns 仅在 enable_dns_server 启用时添加
            # dns-in 入站不再自动存在于 TUN 模式，因此 hijack-dns 使用 mixed-in
            # 格式：{"action": "hijack-dns", "inbound": ["mixed-in"]}
            elif action == 'hijack-dns':
                route_rule = {"action": "hijack-dns", "inbound": ["mixed-in"]}

            else:
                logger.warning(f"Rule '{rule.get('name', '')}' has unknown action '{action}', skipping")
                continue

            # 为 route/reject/resolve action 添加匹配条件字段（sniff/hijack-dns 无需匹配条件）
            if action in ('route', 'reject', 'resolve'):
                for internal_key, singbox_key in rule_field_map.items():
                    value = rule.get(internal_key)
                    if value is None or value == "" or value == []:
                        continue
                    # 数组字段：从 JSON 字符串解析（使用模块级常量 RULE_ARRAY_FIELDS）
                    if singbox_key in RULE_ARRAY_FIELDS and isinstance(value, str):
                        try:
                            parsed = json.loads(value)
                            if parsed:  # 非空数组
                                route_rule[singbox_key] = parsed
                        except (json.JSONDecodeError, TypeError):
                            # 非法 JSON，作为单值处理
                            route_rule[singbox_key] = [value]
                    else:
                        # sing-box 要求 port/source_port 为整数（而非字符串），需类型转换
                        if singbox_key in ('port', 'source_port') and isinstance(value, str):
                            try:
                                value = int(value)
                            except (ValueError, TypeError):
                                logger.warning(f"Rule '{rule.get('name', '')}' has non-numeric {singbox_key}='{value}', skipping")
                                continue
                        # sing-box 要求 invert/ip_is_private 为布尔值（而非整数），需类型转换
                        # SQLite 存储为 INTEGER (0/1)，sing-box 期望 true/false
                        if singbox_key in ('invert', 'ip_is_private'):
                            value = bool(value)
                            if not value:
                                continue  # False 值省略，减少配置体积
                        route_rule[singbox_key] = value
                if rule.get('rule_set_id'):
                    tag = rs_id_to_tag.get(rule.get('rule_set_id'))
                    if tag:
                        rs_enabled = any(
                            rs.get('id') == rule.get('rule_set_id') and rs.get('is_enabled', 1)
                            for rs in rule_sets
                        )
                        if rs_enabled:
                            route_rule["rule_set"] = [tag]
                        else:
                            logger.warning(f"Rule '{rule.get('name', '')}' references disabled rule_set '{tag}', skipping rule_set field")

            route_rules.append(route_rule)

        # TunSplit: TUN 模式下按进程路径分流（参考 NekoBox BuildConfigSingBox L1069-1084）
        # NekoBox 的 TunSplit 支持 proxy/direct/block 三个进程路径列表，
        # 将指定进程的流量路由到对应出站。这是 TUN 模式特有的功能，
        # 因为只有 TUN 才能捕获所有进程的网络流量（系统代理只能拦截支持代理设置的应用）。
        if tun_enabled:
            tun_split_proxy = self.db.get_setting("tun_split_proxy", "")
            tun_split_direct = self.db.get_setting("tun_split_direct", "")
            tun_split_block = self.db.get_setting("tun_split_block", "")
            if isinstance(tun_split_proxy, str) and tun_split_proxy.strip():
                paths = [p.strip() for p in tun_split_proxy.split("\n") if p.strip()]
                if paths:
                    route_rules.append({"action": "route", "outbound": "proxy", "process_path": paths})
            if isinstance(tun_split_direct, str) and tun_split_direct.strip():
                paths = [p.strip() for p in tun_split_direct.split("\n") if p.strip()]
                if paths:
                    route_rules.append({"action": "route", "outbound": "direct", "process_path": paths})
            if isinstance(tun_split_block, str) and tun_split_block.strip():
                paths = [p.strip() for p in tun_split_block.split("\n") if p.strip()]
                if paths:
                    route_rules.append({"action": "reject", "process_path": paths})

        # 仅包含已启用的 rule_set
        rule_set_cdn = self.db.get_setting("rule_set_cdn", "")
        rule_set_defs = []
        for rs in rule_sets:
            if not rs.get('is_enabled', 1):
                continue
            rs_url = rs.get('url', '')
            rs_type = rs.get('type', 'remote')
            # jsdelivr CDN 镜像转换（参考 NekoBox）
            if rule_set_cdn and rs_type == 'remote' and rs_url:
                rs_url = self._convert_to_jsdelivr(rs_url, rule_set_cdn)
            rs_def = {
                "type": rs_type,
                "tag": rs.get('tag'),
                "format": rs.get('format'),
            }
            if rs_type == 'remote':
                rs_def["url"] = rs_url
                rs_def["download_detour"] = "proxy"
            elif rs_type == 'local':
                # 本地 rule_set: 使用 path 字段指定本地文件路径
                rs_def["path"] = rs.get('url', '')  # 对于 local 类型，url 字段存储本地路径
            rule_set_defs.append(rs_def)

        # Adblock 注入（参考 NekoBox：自动注入广告拦截规则）
        adblock_enabled = self.db.get_setting("adblock_enabled", False)
        if adblock_enabled:
            # 注入广告拦截规则集（使用 SagerNet 的 adblock rule_set）
            adblock_tag = "adblock"
            adblock_rs_def = {
                "type": "remote",
                "tag": adblock_tag,
                "format": "binary",
                # 与 NekoBox 一致：使用 217heidai/adblockfilters 规则集
                # NekoBox: get_rule_set_json("nekobox-adblocksingbox") → https://raw.githubusercontent.com/217heidai/adblockfilters/main/rules/adblocksingbox.srs
                "url": "https://raw.githubusercontent.com/217heidai/adblockfilters/main/rules/adblocksingbox.srs",
                "download_detour": "proxy",
            }
            # 如果配置了 CDN 镜像则应用
            if rule_set_cdn:
                adblock_rs_def["url"] = self._convert_to_jsdelivr(adblock_rs_def["url"], rule_set_cdn)
            rule_set_defs.append(adblock_rs_def)
            # 在用户规则之前插入 reject 规则（在 sniff/hijack-dns 之后）
            route_rules.append({"rule_set": [adblock_tag], "action": "reject"})
        # auto_detect_interface: sing-box 自动检测默认网络接口，用于直连出站流量。
        # TUN 模式下此选项尤其重要，确保直连流量通过正确的物理网卡发出。
        # 非 TUN 模式下也能正确处理多网卡环境。
        # ★ 参考 sing-box 官方文档：default_domain_resolver 使用字符串格式（DNS 服务器 tag）★
        # 官方示例：default_domain_resolver: "local"（指向直连 DNS 服务器 tag）
        # 对象格式 {"server": "...", "strategy": "..."} 是旧格式，已不支持。
        # dns-direct 是直连 DNS（223.5.5.5），用作域名解析器。
        result = {
            "rules": route_rules,
            "final": "proxy" if enabled_tags else "direct",
            "auto_detect_interface": True,
            "default_domain_resolver": "dns-direct",
        }
        # 注意：route 顶层不再设置 strategy 字段。
        # sing-box 不支持 route.strategy（验证报错 "unknown field strategy"）。
        # 域名解析策略通过两种方式实现（与 NekoBox 一致）：
        # 1. default_domain_resolver.strategy: 出站域名解析器的策略（上面已设置）
        # 2. route rule {"action": "resolve", "strategy": ...}: 路由层面的域名解析（上面已添加）
        # TUN 模式下启用 find_process，使连接列表显示进程信息
        # 参考 NekoBox: connection_statistics 时设置 find_process: true
        if tun_enabled:
            result["find_process"] = True
        if rule_set_defs:
            result["rule_set"] = rule_set_defs
        return result

    def get_clash_api_secret(self) -> str:
        """获取 Clash API secret（如不存在则自动生成并持久化）
        
        注意：此方法虽涉及内部配置，但被 main.py 和 VenltaBridge 外部调用，
        因此命名不带下划线前缀，表示为公开 API。
        """
        secret = self.db.get_setting("clash_api_secret")
        if not secret:
            import secrets as sec
            secret = sec.token_hex(16)
            self.db.update_setting("clash_api_secret", secret)
        return secret

    def _validate(self) -> bool:
        """验证 sing-box 配置，带超时保护

        返回值：
        - True: 配置有效，或验证被跳过（sing-box 未找到）
        - False: 配置无效

        失败原因通过日志记录区分：
        - sing-box 未找到 → 跳过验证，返回 True（非配置问题，不应触发回滚）
        - 配置语法/逻辑错误 → 记录 error（含 stderr 输出），返回 False
        - 验证超时 → 记录 warning

        重要：此方法使用 os.posix_spawn() 替代 subprocess.run() 来执行 sing-box check，
        避免 fork() 从多线程 Python/Qt 进程导致 glibc 堆损坏崩溃。
        参考：NekoBox 使用 QProcess::start()（内部也是 posix_spawn），不存在此问题。
        """
        try:
            singbox_bin = find_singbox_binary()
            if not singbox_bin:
                logger.warning("sing-box binary not found, skipping config validation")
                return True
            cmd = [singbox_bin, "check", "-c", str(self.config_path)]
            logger.info(f"Validating config: {' '.join(cmd[:3])}...")

            # 使用 posix_spawn 替代 fork+exec，避免多线程环境下的 glibc 堆损坏
            # Python 3.12+ subprocess.run() 默认使用 posix_spawn，但某些条件下
            # （如 close_fds=True + text=True）会回退到 fork+exec。
            # Linux: close_fds=False 强制使用 posix_spawn，避免 fork+exec 回退到 fork()，
            # 在多线程 Qt 应用中可能导致 glibc 堆损坏崩溃。
            # Windows: close_fds=True 防止子进程继承不需要的文件句柄（如 SQLite 锁）。
            result = subprocess.run(
                cmd,
                capture_output=True, timeout=10,
                close_fds=(platform.system() != "Linux"),
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors='replace').strip()
                logger.error(f"sing-box config validation failed: {stderr}")
                # 输出配置文件路径，便于手动排查
                logger.error(f"Config file: {self.config_path}")
            else:
                logger.info("sing-box config validation passed")
            return result.returncode == 0
        except FileNotFoundError:
            # sing-box 未找到时跳过验证（而非返回 False 触发回滚），
            # 因为配置本身可能是正确的，只是二进制文件不存在。
            # 返回 True 表示"验证通过（跳过）"，避免误触发配置回滚。
            logger.warning("sing-box binary not found, skipping config validation")
            return True
        except subprocess.TimeoutExpired:
            # 超时不代表配置无效（可能是系统负载高或 sing-box check 启动慢），
            # 记录 warning 但返回 True 避免误触发回滚
            logger.warning(f"sing-box config validation timed out (10s), assuming valid to avoid false rollback. Config: {self.config_path}")
            return True

    def _backup(self) -> bool:
        """备份当前配置文件，返回是否成功"""
        if not self.config_path.exists():
            return True  # 无现有配置文件，无需备份
        try:
            backup_name = f"config_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
            shutil.copy(self.config_path, self.backup_dir / backup_name)
            backups = sorted(self.backup_dir.glob("config_*.json"))
            if len(backups) > 10:
                for old in backups[:-10]:
                    old.unlink()
            return True
        except Exception as e:
            logger.error(f"Config backup failed: {e}")
            return False

    def _rollback(self):
        backups = sorted(self.backup_dir.glob("config_*.json"), reverse=True)
        if backups:
            shutil.copy(backups[0], self.config_path)
    
    def get_tun_enabled(self) -> bool:
        return self.db.get_setting("tun_enabled", False)
    
    def set_tun_enabled(self, enabled: bool):
        self.db.update_setting("tun_enabled", enabled)
    
    def get_clash_api_port(self) -> int:
        """获取当前 Clash API 端口（供 SingboxWorker/StatsWorker 使用，避免硬编码）"""
        return self.db.get_setting("clash_api_port", DEFAULT_CLASH_API_PORT)

    def _collect_direct_ips_from_rules(self, rules: list, rule_sets: list) -> tuple[list[str], list[str]]:
        """从路由规则中收集直连目标的 IP CIDR 和 rule_set 标签

        参考 NekoBox BuildConfigSingBox：当 enable_tun_routing 启用时，
        直连目标的 IP CIDR 和 rule_set 会被添加到 TUN 的 route_exclude_address
        和 route_exclude_address_set，使直连流量在 OS 层面就不走 TUN 设备，
        直接通过物理网卡发出，提高直连性能并避免路由环路。

        NekoBox 代码：BuildTunInbound(directIPSets, directIPCIDRs) 中，
        directIPCIDRs 来自 routeChain->get_direct_ips() 中 "ip:" 前缀项，
        directIPSets 来自 "ruleset:" 前缀项。

        Returns:
            (direct_ip_cidrs, direct_rule_set_tags) — 直连 IP CIDR 列表和直连 rule_set tag 列表
        """
        direct_ip_cidrs = []
        direct_rule_set_ids = []

        # 构建 rule_set id → tag 映射
        rs_id_to_tag = {rs.get('id'): rs.get('tag') for rs in rule_sets}

        for rule in rules:
            if not rule.get('is_enabled', 0):
                continue

            # 仅收集直连（direct）出站的规则的 IP 和 rule_set
            outbound_tag = rule.get('outbound_tag', '')
            action = rule.get('action', 'route') or 'route'
            if action != 'route' or outbound_tag != 'direct':
                continue

            # 收集 IP CIDR
            ip_cidr_str = rule.get('ip_cidr', '')
            if ip_cidr_str:
                try:
                    import json as _json
                    cidrs = _json.loads(ip_cidr_str) if isinstance(ip_cidr_str, str) else ip_cidr_str
                    if isinstance(cidrs, list):
                        direct_ip_cidrs.extend(cidrs)
                except (json.JSONDecodeError, TypeError):
                    if isinstance(ip_cidr_str, str) and ip_cidr_str.strip():
                        direct_ip_cidrs.append(ip_cidr_str.strip())

            # 收集 rule_set
            rule_set_id = rule.get('rule_set_id')
            if rule_set_id:
                rs_tag = rs_id_to_tag.get(rule_set_id)
                if rs_tag:
                    # 检查 rule_set 是否启用
                    rs_enabled = any(
                        rs.get('id') == rule_set_id and rs.get('is_enabled', 1)
                        for rs in rule_sets
                    )
                    if rs_enabled:
                        direct_rule_set_ids.append(rs_tag)

        return direct_ip_cidrs, direct_rule_set_ids
