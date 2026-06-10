import json
import shutil
import subprocess
import platform
import logging
from pathlib import Path
from datetime import datetime

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

def find_singbox_binary() -> str:
    """统一解析 sing-box 二进制路径，优先打包目录，其次 PATH"""
    # 1. 打包后的 resources/sing-box/ 目录
    bundled = Path(__file__).parent.parent / "resources" / "sing-box" / ("sing-box.exe" if platform.system() == "Windows" else "sing-box")
    if bundled.exists():
        return str(bundled)
    # 2. 系统PATH
    path_bin = shutil.which("sing-box")
    if path_bin:
        return path_bin
    # 3. 优雅降级：返回 "sing-box" 让 subprocess 在运行时查找
    logger.warning("sing-box binary not found in resources/ or PATH, will retry at runtime")
    return "sing-box"

# 注意：不再在模块级别缓存 SINGBOX_BIN，因为 find_singbox_binary() 可能因环境变化返回不同结果
# 每次调用 find_singbox_binary() 即可，开销可忽略（Path.exists() 和 shutil.which()
# 均为轻量系统调用，且调用频率低——仅在启动/重启/更新时调用）
class ConfigManager:
    def __init__(self, db):
        self.db = db
        self.config_dir = Path.home() / ".venlta"
        self.config_dir.mkdir(exist_ok=True)
        self.config_path = self.config_dir / "config.json"
        self.backup_dir = self.config_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)

    def regenerate(self):
        # TODO: 当节点/规则数量大时，可优化为增量更新而非全量重写
        # 先备份，备份失败则中止重生成（避免无备份时回滚到更旧的版本）
        if not self._backup():
            raise RuntimeError("Config backup failed, aborting regeneration to prevent data loss")
        config = self._build_config()
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        if not self._validate():
            self._rollback()
            raise RuntimeError("Invalid config generated")
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

    def write_config(self) -> Path:
        # 始终重新生成配置文件，确保反映当前设置（TUN 开关、端口、DNS、节点等）。
        # 之前的缓存逻辑（仅当文件不存在或无效时才重新生成）会导致严重 Bug：
        # 用户切换 TUN 后重启代理，旧的有效配置（不含 TUN inbound）被复用，
        # sing-box 启动时实际没有 TUN 支持，表现为"开 TUN 不生效"。
        # 重新生成的开销可忽略（仅 start/restart 时调用，非热路径），
        # 而 _validate() 的 sing-box check 开销远大于 _build_config() 本身。
        try:
            return self.regenerate()
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
            tun_stack = self.db.get_setting("tun_stack", "mixed")
            tun_address = self.db.get_setting("tun_address", "172.19.0.1/24")
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
            tun_inbound = {
                "type": "tun",
                "tag": "tun-in",
                "interface_name": tun_ifname,
                "address": tun_address_list,
                "auto_route": True,
                "strict_route": self.db.get_setting("tun_strict_route", True),
                "mtu": self.db.get_setting("tun_mtu", 1500),
                "stack": tun_stack,
            }
            # 排除私有 IP 地址段，避免 TUN 劫持局域网流量导致无法访问路由器/NAS/打印机等
            # NekoBox 也排除了这些地址段（ConfigBuilder.cpp:709-723）
            tun_inbound["route_exclude_address"] = [
                "127.0.0.0/8",
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "169.254.0.0/16",
                "224.0.0.0/4",
                "255.255.255.255/32",
            ]
            # gvisor stack 不支持 strict_route，自动关闭避免报错
            if tun_stack == "gvisor" and tun_inbound.get("strict_route"):
                tun_inbound["strict_route"] = False
            inbounds.append(tun_inbound)
            # TUN 模式下添加 DNS 入站（参考 NekoBox BuildConfigSingBox L1159-1164）
            # dns-in 是 direct 类型入站，监听本地端口接收 DNS 查询，
            # 配合 route 规则中的 hijack-dns 实现透明 DNS 劫持。
            # 没有 dns-in，TUN 模式下部分应用的 DNS 查询可能无法被正确拦截。
            dns_listen_port = self.db.get_setting("dns_in_port", 5353)
            inbounds.append({
                "type": "direct",
                "tag": "dns-in",
                "listen": "127.0.0.1",
                "listen_port": dns_listen_port,
            })

        # 先构建 outbounds 以获知 enabled_tags 和代理服务器域名，
        # 然后构建 DNS（需要知道代理服务器域名来添加直连 DNS 规则，避免循环依赖），
        # 最后构建 route（需要 DNS 服务器 IP 来添加直连规则）
        outbounds, enabled_tags, proxy_server_domains = self._build_outbounds(nodes)
        dns_config, dns_server_ips = self._build_dns(tun_enabled, has_proxy=bool(enabled_tags), proxy_server_domains=proxy_server_domains)
        route = self._build_route(rules, rule_sets, enabled_tags, dns_server_ips, tun_enabled)

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
            # certificate 配置：指定 TLS 证书存储源
            # 在某些系统（尤其是最小化 Linux 容器）上系统证书可能缺失，
            # 设置 store 为 system 让 sing-box 使用系统证书库
            "certificate": {"store": "system"},
            "experimental": {
                "clash_api": {
                    "external_controller": f"127.0.0.1:{clash_api_port}",
                    "secret": self.get_clash_api_secret(),
                    "access_control_allow_origin": ["http://127.0.0.1"]
                },
                # cache_file 持久化 selector 选择、fakeip 映射、RDRC 缓存
                # 不配置此项会导致重启后节点选择重置、fakeip 映射丢失
                "cache_file": {
                    "enabled": True,
                    "store_fakeip": True,
                    "store_rdrc": True,
                },
            }
        }
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
        # 返回值：(dns_config, dns_server_ips) - dns_server_ips 用于添加直连路由规则
        dns_server_1 = self.db.get_setting("dns_server_1", "tls://8.8.8.8")
        dns_server_2 = self.db.get_setting("dns_server_2", "https://223.5.5.5/dns-query")

        # --- 远程 DNS（通过代理连接）---
        remote_fields = self._parse_dns_address(dns_server_1)
        # Remote DNS 必须通过代理出站连接（NekoBox 也设置 detour: "proxy"），
        # 否则 DNS 查询会绕过代理直连，导致 DNS 泄漏或解析失败。
        # 注意：detour: "proxy" 是有效的，因为 proxy 是 selector outbound（非空 direct）。
        # sing-box 1.13 禁止的是 detour 指向无 bind_interface/bind_address 的 bare direct outbound。
        remote_server = {"tag": "dns-remote", "detour": "proxy", **remote_fields}
        # 所有使用域名地址的 DNS 服务器，domain_resolver 必须指向 dns-local（非业务 DNS），
        # 避免与 remote/local 之间的循环依赖（NekoBox 关键设计）。
        # 参考 NekoBox：始终设置 domain_resolver 为 "dns-local"（即使服务器是 IP），
        # IP 服务器会忽略此字段，但设置后更安全，避免遗漏
        remote_server["domain_resolver"] = "dns-local"

        # --- 直连 DNS ---
        local_fields = self._parse_dns_address(dns_server_2)
        local_server = {"tag": "dns-direct", **local_fields}
        # 直连 DNS 的 domain_resolver 也始终指向 dns-local（与 NekoBox 一致）
        # IP 服务器会忽略此字段，但始终设置更安全
        local_server["domain_resolver"] = "dns-local"

        # --- 基础 DNS（underlying DNS，仅用于域名解析）---
        # NekoBox: dnsLocalAddress 默认为 "local"（即 type: "local"），
        # 用户可配置为 IP 地址（如 8.8.8.8）。
        # 此服务器不参与业务 DNS 查询，仅作为 domain_resolver 目标。
        underlying_dns = self.db.get_setting("underlying_dns", "local")
        local_dns_fields = self._parse_dns_address(underlying_dns)
        underlying_server = {"tag": "dns-local", **local_dns_fields}

        # 收集 DNS 服务器的 IP 地址（用于添加直连路由规则）
        dns_server_ips = []
        for fields in (remote_fields, local_fields):
            server_ip = fields.get("server", "")
            if server_ip and self._is_ip_address(server_ip):
                dns_server_ips.append(server_ip)

        dns_rules = []

        # TUN 模式下需要 DNS 劫持，确保所有 DNS 查询都走代理 DNS
        # sing-box 1.12+ DNS 规则必须包含 action 字段
        if tun_enabled:
            dns_rules.append({
                "inbound": "tun-in",
                "action": "route",
                "server": "dns-remote",
            })

        # 代理服务器域名的 DNS 直连规则（NekoBox 关键设计）
        # 当代理服务器使用域名地址（如 server.example.com）时，
        # 其 DNS 解析必须走直连（dns-direct），否则会形成循环依赖：
        # 代理需要 DNS → DNS 走代理(detour: proxy) → 代理需要 DNS → ...
        # 这会导致代理连接永远无法建立（DNS 解析超时）
        if proxy_server_domains:
            dns_rules.append({
                "domain": proxy_server_domains,
                "action": "route",
                "server": "dns-direct",
            })

        # localhost 解析规则（NekoBox 也有此规则）
        # TUN 模式下系统 DNS 不可用，必须提供 localhost 解析
        # 否则本地服务（如数据库、缓存）可能无法解析 localhost
        dns_rules.append({
            "domain": "localhost",
            "action": "predefined",
            "query_type": "A",
            "rcode": "NOERROR",
            "answer": "localhost. IN A 127.0.0.1",
        })
        dns_rules.append({
            "domain": "localhost",
            "action": "predefined",
            "query_type": "AAAA",
            "rcode": "NOERROR",
            "answer": "localhost. IN AAAA ::1",
        })

        # DNS 服务器排列顺序（参考 NekoBox）
        # NekoBox: 如果 dns_final_out_direct，dns-direct 放在前面；否则 dns-remote 在前面
        # 默认 dns-remote 在前，作为主要 DNS
        servers = [remote_server, local_server, underlying_server]

        dns_config = {
            "servers": servers,
            "rules": dns_rules,
            "final": "dns-remote",
            "strategy": self.db.get_setting("dns_strategy", "prefer_ipv4"),
        }
        return dns_config, dns_server_ips

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

    def _build_outbounds(self, nodes) -> tuple[list, list, list[str]]:
        """构建 outbounds 列表，同时返回 enabled_tags 和代理服务器域名列表

        参考 NekoBox：dns outbound 是 sing-box 的特殊 outbound 类型，
        用于接收 DNS 协议流量并转发到内部 DNS 解析器。
        没有 dns outbound，TUN 模式下 DNS 查询无法被正确处理，
        route rules 中的 {"protocol": "dns"} 也无法匹配到正确的处理程序。

        返回值：(outbounds, enabled_tags, proxy_server_domains)
        - proxy_server_domains: 使用域名地址的代理服务器域名列表，
          用于添加 DNS 直连规则，避免循环依赖（DNS→proxy→DNS→...）
        """
        outbounds = [
            {"type": "dns", "tag": "dns-out"},
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ]
        enabled_tags = []
        proxy_server_domains = []  # 收集使用域名地址的代理服务器
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
            # 收集代理服务器域名（NekoBox 关键设计）
            # 当代理服务器使用域名地址时，其 DNS 解析必须走直连，
            # 否则与 detour: "proxy" 形成循环依赖
            server_addr = node.get('address', '')
            if server_addr and not self._is_ip_address(server_addr):
                proxy_server_domains.append(server_addr)
        outbounds.append({
            "type": "selector",
            "tag": "proxy",
            "outbounds": enabled_tags + ["direct"],
            "default": enabled_tags[0] if enabled_tags else "direct"
        })
        if enabled_tags:
            outbounds.append({
                "type": "urltest",
                "tag": "auto",
                "outbounds": enabled_tags,
                "url": "https://www.gstatic.com/generate_204",
                "interval": "5m"
            })
        return outbounds, enabled_tags, proxy_server_domains

    def _node_to_outbound(self, node):
        outbound_map = {
            "vmess": self._vmess_outbound,
            "vless": self._vless_outbound,
            "trojan": self._trojan_outbound,
            "shadowsocks": self._shadowsocks_outbound,
            "hysteria2": self._hysteria2_outbound,
            "wireguard": self._wireguard_outbound,
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
        transport = config.get('network', 'tcp')
        if transport == 'ws':
            out["transport"] = {"type": "ws", "path": config.get('wsPath', '/'), "headers": config.get('wsHeaders', {})}
        elif transport == 'grpc':
            out["transport"] = {"type": "grpc", "service_name": config.get('grpcServiceName', '')}
        return out

    def _trojan_outbound(self, node):
        config = node.get('config', {})
        out = {"type": "trojan", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "password": config.get('password', ''), "tls": {"enabled": True, "server_name": config.get('sni', node.get('address')), "insecure": config.get('allowInsecure', False)}}
        transport = config.get('network', 'tcp')
        if transport == 'ws':
            out["transport"] = {"type": "ws", "path": config.get('wsPath', '/'), "headers": config.get('wsHeaders', {})}
        elif transport == 'grpc':
            out["transport"] = {"type": "grpc", "service_name": config.get('grpcServiceName', '')}
        return out

    def _shadowsocks_outbound(self, node):
        config = node.get('config', {})
        return {"type": "shadowsocks", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "method": config.get('method', ''), "password": config.get('password', '')}

    def _hysteria2_outbound(self, node):
        config = node.get('config', {})
        out = {"type": "hysteria2", "tag": node.get('tag'), "server": node.get('address'), "server_port": node.get('port'), "password": config.get('password', '')}
        # hysteria2 始终使用 TLS，不依赖 sni 判断
        out["tls"] = {"enabled": True, "server_name": config.get('sni', node.get('address')), "insecure": config.get('allowInsecure', False)}
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

    def _build_route(self, rules, rule_sets, enabled_tags: list, dns_server_ips: list[str] | None = None, tun_enabled: bool = False) -> dict:
        route_rules = []
        # 参考 NekoBox：DNS 协议流量必须路由到 dns-out outbound（sing-box 内部 DNS 解析器），
        # 而非 direct outbound。dns-out outbound 接收 DNS 查询包并通过 sing-box 的 DNS 模块处理，
        # 包括 DNS 分流、缓存、域名解析策略等。
        # 如果路由到 direct，DNS 查询会绕过 sing-box 的 DNS 模块，导致：
        # 1. TUN 模式下 DNS 劫持失效
        # 2. DNS 分流规则不生效
        # 3. 域名解析可能失败（TUN 网络环境下 DNS 服务器不可达）
        route_rules.append({"protocol": "dns", "outbound": "dns-out"})
        # sing-box 1.13.0 移除了 sniff_override_destination 字段（inbound 和 route rule 均不再支持），
        # 仅保留 action: sniff 启用协议嗅探。默认行为：嗅探但不覆盖目标地址。
        # 参考 NekoBox：sniff 规则应仅应用于入站流量（mixed-in, tun-in），
        # 不加 inbound 限制会导致出站连接也被嗅探，增加不必要的开销。
        # 注意：仅在 TUN 启用时包含 "tun-in"，否则 sing-box 会因引用不存在的 inbound tag 而验证失败
        sniff_inbounds = ["mixed-in"]
        if tun_enabled:
            sniff_inbounds.append("tun-in")
            # TUN 模式下 dns-in 入站也需要嗅探（参考 NekoBox L912-913）
            sniff_inbounds.append("dns-in")
        route_rules.append({"action": "sniff", "inbound": sniff_inbounds})
        # TUN 模式下添加 hijack-dns 路由规则（参考 NekoBox L916-919）
        # 当 dns-in 入站收到 DNS 查询时，通过 hijack-dns 动作将其劫持到 sing-box DNS 模块处理。
        # 这确保了所有 DNS 查询都通过 sing-box 的 DNS 分流规则处理，
        # 而不是直接转发到系统 DNS 服务器（TUN 模式下系统 DNS 可能不可达）
        if tun_enabled:
            route_rules.append({"inbound": ["dns-in"], "action": "hijack-dns"})
        # DNS 服务器 IP 直连规则：确保 DNS 服务器连接不走代理（替代已移除的 detour: "direct"）
        # sing-box 1.13 不允许 detour 指向空 direct outbound，改用路由规则实现直连
        if enabled_tags and dns_server_ips:
            route_rules.append({"ip_cidr": [f"{ip}/32" for ip in dns_server_ips], "outbound": "direct"})
        # 构建 rule_set id → tag 映射（sing-box 配置中 rule_set 引用 tag 而非 id）
        rs_id_to_tag = {rs.get('id'): rs.get('tag') for rs in rule_sets}
        # 合法的 outbound tag 集合（用于验证规则引用的 outbound 是否存在）
        # 注意："auto" 仅在 enabled_tags 非空时有效（urltest outbound 仅在有可用节点时创建）
        # 如果所有节点被禁用，"auto" outbound 不存在，引用它的规则应 fallback 到 direct
        valid_outbound_tags = set(enabled_tags) | {"direct", "block", "proxy"}
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
            outbound = rule.get('outbound_tag')
            # 验证 outbound_tag 是否存在于 outbounds 中，无效则 fallback 到 direct
            if outbound not in valid_outbound_tags:
                logger.warning(f"Rule '{rule.get('name', '')}' references unknown outbound '{outbound}', falling back to 'direct'")
                outbound = "direct"
            route_rule = {"outbound": outbound}
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
                # rule_set_id 的特殊处理：不在 rule_field_map 中，因为
                # 1) 它是 UUID→tag 的映射（非直接键名映射），需要 rs_id_to_tag 查找
                # 2) 输出格式是数组 ["tag"]（sing-box 规则中 rule_set 为数组）
                # 3) 需要检查 rule_set 是否启用（禁用的 rule_set 不能出现在配置中）
                # 这与 rule_field_map 的简单 key→value 映射模式不兼容，因此独立处理
                # sing-box 配置中 rule_set 引用 tag 名称，而非 UUID id。
                # rule_set_id 是数据库中的 UUID 主键，rs_id_to_tag 将其映射为 sing-box 配置所需的 tag。
                tag = rs_id_to_tag.get(rule.get('rule_set_id'))
                if tag:
                    # 验证该 rule_set 是否已启用：仅包含启用的 rule_set 的 tag
                    # 如果 rule_set 已禁用，跳过该字段（否则 sing-box 验证会因找不到 tag 而失败）
                    rs_enabled = any(
                        rs.get('id') == rule.get('rule_set_id') and rs.get('is_enabled', 1)
                        for rs in rule_sets
                    )
                    if rs_enabled:
                        route_rule["rule_set"] = [tag]
                    else:
                        logger.warning(f"Rule '{rule.get('name', '')}' references disabled rule_set '{tag}', skipping rule_set field")
            route_rules.append(route_rule)

        # 仅包含已启用的 rule_set
        rule_set_defs = [{"type": "remote", "tag": rs.get('tag'), "format": rs.get('format'), "url": rs.get('url'), "download_detour": "proxy"} for rs in rule_sets if rs.get('is_enabled', 1)]
        # auto_detect_interface: sing-box 自动检测默认网络接口，用于直连出站流量。
        # TUN 模式下此选项尤其重要，确保直连流量通过正确的物理网卡发出。
        # 非 TUN 模式下也能正确处理多网卡环境。
        # sing-box 1.12+ 的 default_domain_resolver 必须是对象格式（而非字符串），
        # 包含 server 和 strategy 字段。使用字符串格式会导致 sing-box 配置验证失败。
        # 参考 NekoBox：指定 dns-direct（直连 DNS）作为域名解析器
        # （NekoBox 使用 dns-direct，不是 dns-local，因为 default_domain_resolver
        # 是为出站域名解析服务的，需要使用有实际 DNS 查询能力的直连 DNS 服务器）
        dns_strategy = self.db.get_setting("dns_strategy", "prefer_ipv4")
        result = {
            "rules": route_rules,
            "final": "proxy" if enabled_tags else "direct",
            "auto_detect_interface": True,
            "default_domain_resolver": {
                "server": "dns-direct",
                "strategy": dns_strategy,
            }
        }
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
        """
        try:
            singbox_bin = find_singbox_binary()
            result = subprocess.run(
                [singbox_bin, "check", "-c", str(self.config_path)],
                capture_output=True, timeout=10
            )
            if result.returncode != 0:
                stderr = result.stderr.decode(errors='replace').strip()
                logger.error(f"sing-box config validation failed: {stderr}")
                # 输出配置文件路径，便于手动排查
                logger.error(f"Config file: {self.config_path}")
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
