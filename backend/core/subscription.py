import json
import httpx
import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

class SubscriptionManager:
    """订阅管理器，负责获取和解析订阅链接"""
    
    def __init__(self, db, config_mgr):
        self.db = db
        self.config_mgr = config_mgr
        self._updating_subs: set[str] = set()  # 正在更新的订阅 ID 集合，防止并发更新
        self._lock = threading.Lock()  # 保护 _updating_subs 的线程安全
    
    def update_async(self, sub_id: str, on_done: Callable[[dict], None] | None = None):
        """异步更新订阅内容（在后台线程中执行，不阻塞 GUI）
        
        并发控制：同一订阅 ID 不会并发更新，防止节点重复添加和数据库竞争。
        如果该订阅正在更新中，直接返回，忽略本次请求。
        """
        with self._lock:
            if sub_id in self._updating_subs:
                logger.warning(f"Subscription {sub_id} is already being updated, skipping")
                if on_done:
                    on_done({"ok": False, "error": "Update already in progress"})
                return
            self._updating_subs.add(sub_id)
        
        def _worker():
            try:
                result = self._fetch_and_parse(sub_id)
                if on_done:
                    on_done(result)
            finally:
                with self._lock:
                    self._updating_subs.discard(sub_id)
        
        threading.Thread(target=_worker, daemon=True).start()
    
    def _fetch_and_parse(self, sub_id: str) -> dict:
        """获取订阅内容并解析为节点列表
        
        注意：订阅元数据（name/url/node_count/last_update 等）统一存储在 subscriptions 表中，
        通过 db.update_subscription() 更新。早期版本曾同时使用 update_setting() 存储部分元数据，
        造成双重存储冗余，现已移除 update_setting() 路径，所有订阅相关数据仅存于 subscriptions 表。
        """
        try:
            subs = self.db.get_subscriptions()
            # 注意：get_subscriptions() 返回 camelCase 键名（经 _convert_keys 转换），
            # 但 id 和 url 不在转换映射中，所以直接用 s.get('id') 和 s.get('url') 是安全的
            sub = next((s for s in subs if s.get('id') == sub_id), None)
            if not sub:
                return {"ok": False, "error": "Subscription not found"}
            
            # 生成设备标识（参考 NekoBox HTTPRequestHelper.cpp:91-128）
            # 使用机器名 + 系统 UUID 生成稳定的设备标识
            import platform
            import uuid as _sys_uuid
            device_id = str(_sys_uuid.getnode())  # MAC address based device ID
            device_os = platform.system()
            ver_os = platform.version()
            device_model = platform.machine()
            
            # 自定义 HWID 参数覆盖（参考 NekoBox GetHWID / sub_custom_hwid_params）
            # 格式: "hwid=xxx,os=xxx,osversion=xxx,model=xxx"
            # 每个键值对可覆盖自动检测的设备信息
            custom_hwid_params = self.db.get_setting("custom_hwid_params", "")
            if custom_hwid_params and isinstance(custom_hwid_params, str):
                custom = {}
                for pair in custom_hwid_params.split(','):
                    pair = pair.strip()
                    eq = pair.find('=')
                    if eq > 0:
                        custom[pair[:eq].strip().lower()] = pair[eq+1:].strip()
                if 'hwid' in custom:
                    device_id = custom['hwid']
                if 'os' in custom:
                    device_os = custom['os']
                if 'osversion' in custom:
                    ver_os = custom['osversion']
                if 'model' in custom:
                    device_model = custom['model']
            
            request_headers = {
                "User-Agent": "ClashForWindows/0.20.39",
                "x-hwid": device_id,
                "x-device-os": device_os,
                "x-ver-os": ver_os,
                "x-device-model": device_model,
            }
            resp = httpx.get(sub['url'], timeout=30, headers=request_headers)
            if resp.status_code != 200:
                return {"ok": False, "error": f"HTTP {resp.status_code}"}

            # 解析 Profile-Title HTTP header（参考 NekoBox GroupUpdater.cpp:1180-1199）
            # 部分订阅源通过此 header 返回订阅名称
            # NekoBox 支持 base64: 前缀递归解码（最多 33 次）
            profile_title = resp.headers.get('profile-title', resp.headers.get('Profile-Title', ''))
            if profile_title:
                profile_title = profile_title.strip()
                # 递归解码 base64: 前缀（参考 NekoBox GroupUpdater.cpp:1186-1193）
                decode_counter = 0
                while profile_title.startswith('base64:') and decode_counter < 33:
                    decode_counter += 1
                    import base64 as _b64
                    try:
                        b64_data = profile_title[7:].encode('utf-8')
                        decoded = _b64.b64decode(b64_data).decode('utf-8').strip()
                        if decoded:
                            profile_title = decoded
                        else:
                            break
                    except Exception:
                        break
                # 如果非 base64 编码，尝试 URL 解码
                if not profile_title.startswith('base64:'):
                    from urllib.parse import unquote
                    try:
                        profile_title = unquote(profile_title)
                    except Exception:
                        pass
                self.db.update_subscription(sub_id, {"name": profile_title})
                logger.info(f"Subscription name updated from Profile-Title header: {profile_title}")

            content = resp.text
            # 尝试 Base64 解码（兼容多种编码格式）
            import base64
            # 去除 UTF-8 BOM（\xEF\xBB\xBF 或解码后的 \ufeff），避免 BOM 前缀导致首行解析失败
            content = content.lstrip('\ufeff')
            try:
                # 预处理：去除空白字符（换行/空格/制表符），处理 URL-safe base64 和缺失的 padding
                b64_input = content.replace('\n', '').replace('\r', '').replace(' ', '').replace('\t', '')
                # 恢复缺失的 padding（base64 字符串长度应为 4 的倍数）
                padding = (-len(b64_input)) % 4
                if padding:
                    b64_input += '=' * padding
                # 先尝试标准 base64 解码
                try:
                    decoded = base64.b64decode(b64_input).decode('utf-8')
                except Exception:
                    # 回退到 URL-safe base64 解码（部分订阅源使用 - 替代 +，_ 替代 /）
                    decoded = base64.urlsafe_b64decode(b64_input).decode('utf-8')
                # 解码后的内容同样可能有 BOM
                decoded = decoded.lstrip('\ufeff')
                # 验证解码结果是否为有效文本（非二进制乱码）
                # 简单启发式：如果解码后的文本看起来像订阅格式（包含 proxies、outbounds、vmess:// 等关键字），
                # 或者是可打印的 ASCII/UTF-8 文本，则接受解码结果
                if decoded.strip() and not decoded.startswith('\x00'):
                    content = decoded
                    logger.debug("Base64 decode successful")
            except Exception as b64_err:
                logger.debug(f"Base64 decode failed or not base64 content: {b64_err}")
            
            # 调试：记录订阅内容格式和长度，便于排查解析问题
            content_preview = content[:300]
            logger.info(f"Subscription content length={len(content)}, starts_with={content[:20]!r}, preview={content_preview!r}")

            # 解析节点（优先级：SIP008 > WireGuard 配置 > sing-box JSON > Clash YAML > 代理链接逐行解析）
            parser_used = 'none'
            nodes = self._parse_sip008(content, sub_id)
            if nodes:
                parser_used = 'sip008'
            if not nodes:
                nodes = self._parse_wireguard_config(content, sub_id)
                if nodes:
                    parser_used = 'wireguard_config'
            if not nodes:
                nodes = self._parse_singbox_json(content, sub_id)
                if nodes:
                    parser_used = 'singbox_json'
            if not nodes:
                nodes = self._parse_clash_yaml(content, sub_id)
                if nodes:
                    parser_used = 'clash_yaml'
            if not nodes:
                nodes = self._parse_subscription_content(content, sub_id)
                if nodes:
                    parser_used = 'proxy_links'
            logger.info(f"Subscription parsed: {len(nodes)} nodes from {sub_id} (parser={parser_used})")
            
            # 先添加新节点，再删除旧节点（避免空窗期：中间状态配置仍可用）
            # 但需注意 tag 唯一性约束：先删除同 tag 的旧节点，再添加新节点
            old_nodes = self.db.get_nodes()
            # get_nodes() 返回 camelCase 键名，只需检查 subscriptionId
            old_node_ids = [node['id'] for node in old_nodes if node.get('subscriptionId') == sub_id]
            
            # 收集所有现有节点的 tag（包括其他订阅的），用于检测跨订阅 tag 冲突
            all_existing_tags = {node.get('tag') for node in old_nodes}

            # 订阅去重（参考 NekoBox ProfileFilter.Common()）
            # 按 protocol + address + port + config 关键字段 去重，防止重复节点。
            # 对于同一订阅内的旧节点：如果新节点列表中存在相同（protocol+address+port+关键config）的节点，
            # 则保留旧节点（保留用户的自定义设置如启用/禁用状态），跳过添加新节点。
            # 对于新节点列表内的重复：跳过后续重复项。
            def _node_identity_key(node_data: dict) -> str:
                """生成节点唯一标识（用于去重），参考 NekoBox ProfileFilter"""
                protocol = node_data.get('protocol', '')
                address = node_data.get('address', '')
                port = str(node_data.get('port', 0))
                config = node_data.get('config', {})
                # 每种协议的关键标识字段
                if protocol == 'vmess':
                    key = config.get('uuid', '')
                elif protocol == 'vless':
                    key = config.get('uuid', '')
                elif protocol == 'trojan':
                    key = config.get('password', '')
                elif protocol == 'shadowsocks':
                    key = f"{config.get('method', '')}:{config.get('password', '')}"
                elif protocol == 'hysteria2':
                    key = config.get('password', '')
                elif protocol == 'wireguard':
                    key = f"{config.get('privateKey', config.get('private_key', ''))}"
                elif protocol == 'tuic':
                    key = f"{config.get('uuid', '')}:{config.get('password', '')}"
                else:
                    key = ''
                return f"{protocol}:{address}:{port}:{key}"

            # 构建旧节点（同订阅）的唯一标识集合
            old_sub_nodes = [n for n in old_nodes if n.get('subscriptionId') == sub_id]
            old_identity_set = set()
            old_id_to_identity = {}  # id → identity_key，用于后续决定是否删除旧节点
            for n in old_sub_nodes:
                ik = _node_identity_key(n)
                old_identity_set.add(ik)
                old_id_to_identity[n['id']] = ik

            # 构建新节点的唯一标识集合，去重
            new_identity_set = set()
            deduped_nodes = []
            duplicate_count = 0
            for node_data in nodes:
                ik = _node_identity_key(node_data)
                # 跳过新节点列表内的重复
                if ik in new_identity_set:
                    duplicate_count += 1
                    continue
                new_identity_set.add(ik)
                deduped_nodes.append(node_data)
            
            if duplicate_count > 0:
                logger.info(f"Subscription dedup: skipped {duplicate_count} duplicate nodes in new subscription data")

            # 确定需要删除的旧节点：旧节点中不在新节点列表中的应删除
            # 保留旧节点中与新节点匹配的（保留用户的启用/禁用等设置）
            nodes_to_delete = []
            identities_to_keep = new_identity_set  # 新节点中存在的身份标识
            for nid in old_node_ids:
                ik = old_id_to_identity.get(nid, '')
                if ik and ik in identities_to_keep:
                    # 旧节点与新节点匹配，保留旧节点（不删除）
                    # 但需要从新节点列表中移除对应的条目（避免重复添加）
                    pass
                else:
                    nodes_to_delete.append(nid)

            # 从新节点列表中过滤掉与保留旧节点匹配的条目
            retained_identities = set()
            for nid in old_node_ids:
                ik = old_id_to_identity.get(nid, '')
                if ik and ik in identities_to_keep:
                    retained_identities.add(ik)
            final_new_nodes = [n for n in deduped_nodes if _node_identity_key(n) not in retained_identities]
            
            if retained_identities:
                logger.info(f"Subscription dedup: retained {len(retained_identities)} existing nodes (preserving user settings)")
            
            # 删除不在新节点列表中的旧节点
            # 使用 DatabaseManager 公开 API（delete_node/add_node），确保：
            # 1. config 加密序列化逻辑一致
            # 2. tag 唯一性校验不遗漏
            # 3. 白名单保护生效
            # 注意：delete_node/add_node 各自带 commit，无法做到跨操作事务原子性。
            # 缓解措施：订阅数据始终可从原始 URL 重新拉取，如果中途失败：
            # 1. 用户点击"刷新订阅"即可恢复
            # 2. 下面的重试机制会自动重试一次添加失败的节点
            # ★ 为订阅节点自动设置 group_id ★
            # 从订阅记录中读取关联的 group_id，使订阅的节点归入对应分组
            sub_group_id = sub.get('groupId') or sub.get('group_id')
            if sub_group_id:
                for node_data in final_new_nodes:
                    if not node_data.get('group_id'):
                        node_data['group_id'] = sub_group_id
                # 同时更新保留的旧节点的 group_id（旧订阅可能没有 group_id）
                for n in old_sub_nodes:
                    nid = n.get('id')
                    old_group = n.get('groupId') or n.get('group_id')
                    if nid and not old_group and nid not in nodes_to_delete:
                        try:
                            self.db.update_node(nid, {'group_id': sub_group_id})
                        except Exception:
                            pass  # 更新失败不影响主流程

            failed_adds = []  # 记录添加失败的节点，用于重试
            try:
                for nid in nodes_to_delete:
                    self.db.delete_node(nid)
                for node_data in final_new_nodes:
                    # 如果新节点 tag 与其他订阅的节点冲突，添加后缀
                    base_tag = node_data.get('tag', '')
                    final_tag = base_tag
                    suffix_counter = 1
                    while final_tag in all_existing_tags:
                        final_tag = f"{base_tag}-{suffix_counter}"
                        suffix_counter += 1
                    if final_tag != base_tag:
                        node_data['tag'] = final_tag
                    all_existing_tags.add(final_tag)
                    # 使用 db.add_node() 插入，确保 config 加密序列化、tag 唯一性校验等逻辑一致
                    try:
                        self.db.add_node(node_data)
                    except Exception as add_err:
                        logger.error(f"Failed to add node '{node_data.get('name', 'unknown')}': {add_err}")
                        failed_adds.append((node_data, str(add_err)))
            except Exception as e:
                logger.error(f"Subscription update failed during node replacement: {e}")
                return {"ok": False, "error": f"Node replacement failed: {e}"}
            
            # 重试一次添加失败的节点（可能因临时 tag 冲突等原因失败）
            for node_data, _ in failed_adds:
                try:
                    # 重新生成 tag 避免冲突
                    import uuid as _uid
                    node_data['tag'] = f"{node_data.get('protocol', 'node')}-{_uid.uuid4().hex[:16]}"
                    self.db.add_node(node_data)
                except Exception as retry_err:
                    logger.warning(f"Retry add node failed: {retry_err}")
            
            # 重新生成配置（使用防抖版本，因为批量操作中多次 add/delete 不需要每次都重写配置）
            self.config_mgr.regenerate_deferred()
            
            # 更新订阅表的 last_update 和 node_count 字段（直接更新 subscriptions 表，不再使用 update_setting 双重存储）
            from datetime import datetime as _dt
            self.db.update_subscription(sub_id, {"last_update": _dt.now().isoformat(), "node_count": len(nodes)})
            
            return {"ok": True, "node_count": len(nodes)}
        except Exception as e:
            logger.error(f"Failed to update subscription {sub_id}: {e}")
            return {"ok": False, "error": str(e)}
    
    def _parse_sip008(self, content: str, sub_id: str) -> list:
        """尝试解析 SIP008 ShadowSocks JSON 格式的订阅内容

        SIP008 格式示例：
        {
          "version": 8,
          "servers": [
            {
              "server": "1.2.3.4",
              "server_port": 8388,
              "method": "aes-256-gcm",
              "password": "password",
              "remarks": "Server 1"
            }
          ]
        }
        """
        content_stripped = content.strip()
        if not content_stripped.startswith('{'):
            return []
        try:
            data = json.loads(content_stripped)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        # SIP008 必须有 "version" 键且值为 8
        if data.get('version') != 8:
            return []
        servers = data.get('servers', [])
        if not servers:
            return []
        import uuid as _uid
        nodes = []
        SUPPORTED_SS_METHODS = {'aes-256-gcm', 'aes-128-gcm', 'chacha20-ietf-poly1305',
                                'xchacha20-ietf-poly1305', '2022-blake3-aes-128-gcm',
                                '2022-blake3-aes-256-gcm', '2022-blake3-chacha20-poly1305',
                                'none'}
        for srv in servers:
            if not isinstance(srv, dict):
                continue
            method = srv.get('method', '')
            password = srv.get('password', '')
            server = srv.get('server', '')
            port = int(srv.get('server_port', 0) or 0)
            name = srv.get('remarks', srv.get('server', 'SS'))
            if not method or not password or not server:
                continue
            if port <= 0 or port > 65535:
                continue
            if method.lower() not in SUPPORTED_SS_METHODS:
                logger.warning(f"Skipping SIP008 server '{name}' with unsupported method: {method}")
                continue
            config = {'method': method, 'password': password}
            nodes.append({
                'name': name, 'protocol': 'shadowsocks', 'address': server, 'port': port,
                'tag': f"ss-{_uid.uuid4().hex[:16]}", 'config': config,
                'subscription_id': sub_id, 'is_enabled': 1,
            })
        if nodes:
            logger.info(f"Parsed {len(nodes)} nodes from SIP008 format")
        return nodes

    def _parse_wireguard_config(self, content: str, sub_id: str) -> list:
        """解析 WireGuard INI 风格配置文件

        WireGuard .conf 文件格式：
        [Interface]
        PrivateKey = xxx
        Address = 172.19.0.1/24
        DNS = 1.1.1.1

        [Peer]
        PublicKey = yyy
        Endpoint = 1.2.3.4:51820
        AllowedIPs = 0.0.0.0/0
        Reserved = 123,456,789
        """
        content_stripped = content.strip()
        # 快速检查：必须包含 [Interface] 和 [Peer] 段
        if '[Interface]' not in content_stripped or '[Peer]' not in content_stripped:
            return []

        import uuid as _uid
        import re

        # 解析 INI 风格段落
        interface = {}
        peers = []
        current_section = None
        current_data = {}

        for line in content_stripped.split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line.startswith(';'):
                continue
            # 段落头
            m = re.match(r'^\[(\w+)\]$', line)
            if m:
                if current_section == 'Interface':
                    interface = current_data
                elif current_section == 'Peer':
                    peers.append(current_data)
                current_section = m.group(1)
                current_data = {}
                continue
            # Key = Value
            if '=' in line and current_section:
                key, _, value = line.partition('=')
                current_data[key.strip()] = value.strip()

        # 不要忘记最后一个段落
        if current_section == 'Interface':
            interface = current_data
        elif current_section == 'Peer':
            peers.append(current_data)

        if not interface.get('PrivateKey') or not peers:
            return []

        nodes = []
        private_key = interface.get('PrivateKey', '')
        local_addresses = [a.strip() for a in interface.get('Address', '').split(',') if a.strip()]

        for peer in peers:
            peer_public_key = peer.get('PublicKey', '')
            endpoint = peer.get('Endpoint', '')
            if not peer_public_key or not endpoint:
                continue

            # 解析 endpoint: host:port
            # 处理 IPv6: [::1]:port
            if endpoint.startswith('['):
                bracket_end = endpoint.find(']')
                if bracket_end < 0:
                    continue
                host = endpoint[1:bracket_end]
                port_str = endpoint[bracket_end+1:].lstrip(':')
            else:
                # IPv4 或主机名
                parts = endpoint.rsplit(':', 1)
                if len(parts) != 2:
                    continue
                host, port_str = parts

            try:
                port = int(port_str)
            except (ValueError, TypeError):
                continue

            if port <= 0 or port > 65535 or not host:
                continue

            # 解析 reserved（逗号分隔整数）
            reserved = []
            reserved_str = peer.get('Reserved', '')
            if reserved_str:
                try:
                    reserved = [int(x.strip()) for x in reserved_str.split(',') if x.strip().isdigit()]
                except (ValueError, TypeError):
                    reserved = []

            config = {
                'privateKey': private_key,
                'peerPublicKey': peer_public_key,
                'reserved': reserved,
                'localAddress': local_addresses,
            }

            name = peer.get('Remarks', f"WireGuard-{host}")
            nodes.append({
                'name': name, 'protocol': 'wireguard', 'address': host, 'port': port,
                'tag': f"wg-{_uid.uuid4().hex[:16]}", 'config': config,
                'subscription_id': sub_id, 'is_enabled': 1,
            })

        if nodes:
            logger.info(f"Parsed {len(nodes)} nodes from WireGuard config format")
        return nodes

    def _parse_singbox_json(self, content: str, sub_id: str) -> list:
        """尝试解析 sing-box JSON 格式的订阅内容

        sing-box JSON 格式示例：
        {
          "outbounds": [
            {"type": "vless", "tag": "node1", "server": "...", "server_port": 443, "uuid": "..."},
            ...
          ]
        }

        如果内容不是 sing-box JSON 格式（或解析失败），返回空列表，由调用方回退到 Clash YAML 解析。
        """
        content_stripped = content.strip()
        if not content_stripped.startswith('{') and not content_stripped.startswith('['):
            return []
        try:
            data = json.loads(content_stripped)
        except (json.JSONDecodeError, ValueError):
            return []

        # 提取 outbounds 列表（可能在顶层或嵌套在 outbounds 键下）
        outbounds = []
        if isinstance(data, dict):
            outbounds = data.get('outbounds', [])
        elif isinstance(data, list):
            outbounds = data

        if not outbounds:
            return []

        import uuid as _uid
        nodes = []
        for out in outbounds:
            if not isinstance(out, dict):
                continue
            ptype = out.get('type', '')
            # 跳过非代理类型的 outbound（selector, urltest, direct, block, dns 等）
            if ptype in ('selector', 'urltest', 'direct', 'block', 'dns', 'compat', ''):
                continue
            node = self._singbox_outbound_to_node(out, sub_id)
            if node:
                nodes.append(node)

        if nodes:
            logger.info(f"Parsed {len(nodes)} nodes from sing-box JSON format")
        return nodes

    def _singbox_outbound_to_node(self, out: dict, sub_id: str) -> dict | None:
        """将 sing-box outbound 格式转换为内部节点数据"""
        import uuid as _uid
        ptype = out.get('type', '')
        tag = out.get('tag', f"{ptype}-{_uid.uuid4().hex[:16]}")
        server = out.get('server', '')
        port = int(out.get('server_port', 0) or 0)

        if port <= 0 or port > 65535:
            logger.warning(f"Skipping sing-box outbound '{tag}' with invalid port: {port}")
            return None
        if not server:
            logger.warning(f"Skipping sing-box outbound '{tag}' with empty server")
            return None

        try:
            if ptype == 'shadowsocks':
                method = out.get('method', '')
                password = out.get('password', '')
                if not method or not password:
                    return None
                config = {'method': method, 'password': password}
                return {
                    'name': tag, 'protocol': 'shadowsocks', 'address': server, 'port': port,
                    'tag': f"ss-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'vmess':
                uuid_val = out.get('uuid', '')
                if not uuid_val:
                    return None
                config = {'uuid': uuid_val, 'network': 'tcp', 'security': out.get('security', 'auto')}
                # 传输层
                transport = out.get('transport', {})
                if isinstance(transport, dict):
                    ttype = transport.get('type', 'tcp')
                    config['network'] = ttype
                    if ttype == 'ws':
                        config['wsPath'] = transport.get('path', '/')
                        headers = transport.get('headers', {})
                        if headers.get('Host'):
                            config['wsHeaders'] = {'Host': headers['Host']}
                    elif ttype == 'grpc':
                        config['grpcServiceName'] = transport.get('service_name', '')
                # TLS
                tls = out.get('tls', {})
                if isinstance(tls, dict) and tls.get('enabled'):
                    config['tls'] = True
                    if tls.get('server_name'):
                        config['sni'] = tls['server_name']
                    if tls.get('insecure'):
                        config['allowInsecure'] = True
                    # uTLS fingerprint
                    utls = tls.get('utls', {})
                    if isinstance(utls, dict) and utls.get('fingerprint'):
                        config['utlsFingerprint'] = utls['fingerprint']
                return {
                    'name': tag, 'protocol': 'vmess', 'address': server, 'port': port,
                    'tag': f"vmess-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'vless':
                uuid_val = out.get('uuid', '')
                if not uuid_val:
                    return None
                config = {'uuid': uuid_val, 'network': 'tcp'}
                if out.get('flow'):
                    config['flow'] = out['flow']
                # 传输层
                transport = out.get('transport', {})
                if isinstance(transport, dict):
                    ttype = transport.get('type', 'tcp')
                    config['network'] = ttype
                    if ttype == 'ws':
                        config['wsPath'] = transport.get('path', '/')
                        headers = transport.get('headers', {})
                        if headers.get('Host'):
                            config['wsHeaders'] = {'Host': headers['Host']}
                    elif ttype == 'grpc':
                        config['grpcServiceName'] = transport.get('service_name', '')
                # TLS
                tls = out.get('tls', {})
                if isinstance(tls, dict) and tls.get('enabled'):
                    config['tls'] = True
                    if tls.get('server_name'):
                        config['sni'] = tls['server_name']
                    if tls.get('insecure'):
                        config['allowInsecure'] = True
                    # Reality
                    reality = tls.get('reality', {})
                    if isinstance(reality, dict) and reality.get('enabled'):
                        config['reality'] = True
                        if reality.get('public_key'):
                            config['realityPublicKey'] = reality['public_key']
                        if reality.get('short_id'):
                            config['realityShortId'] = reality['short_id']
                    # uTLS fingerprint
                    utls = tls.get('utls', {})
                    if isinstance(utls, dict) and utls.get('fingerprint'):
                        config['utlsFingerprint'] = utls['fingerprint']
                return {
                    'name': tag, 'protocol': 'vless', 'address': server, 'port': port,
                    'tag': f"vless-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'trojan':
                password = out.get('password', '')
                if not password:
                    return None
                config = {'password': password, 'tls': True, 'network': 'tcp'}
                # TLS
                tls = out.get('tls', {})
                if isinstance(tls, dict):
                    if tls.get('server_name'):
                        config['sni'] = tls['server_name']
                    if tls.get('insecure'):
                        config['allowInsecure'] = True
                    # uTLS fingerprint
                    utls = tls.get('utls', {})
                    if isinstance(utls, dict) and utls.get('fingerprint'):
                        config['utlsFingerprint'] = utls['fingerprint']
                # 传输层
                transport = out.get('transport', {})
                if isinstance(transport, dict):
                    ttype = transport.get('type', 'tcp')
                    config['network'] = ttype
                    if ttype == 'ws':
                        config['wsPath'] = transport.get('path', '/')
                        headers = transport.get('headers', {})
                        if headers.get('Host'):
                            config['wsHeaders'] = {'Host': headers['Host']}
                    elif ttype == 'grpc':
                        config['grpcServiceName'] = transport.get('service_name', '')
                return {
                    'name': tag, 'protocol': 'trojan', 'address': server, 'port': port,
                    'tag': f"trojan-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'hysteria2':
                password = out.get('password', '')
                if not password:
                    return None
                config = {'password': password, 'tls': True}
                # TLS
                tls = out.get('tls', {})
                if isinstance(tls, dict):
                    if tls.get('server_name'):
                        config['sni'] = tls['server_name']
                    if tls.get('insecure'):
                        config['allowInsecure'] = True
                    # uTLS fingerprint
                    utls = tls.get('utls', {})
                    if isinstance(utls, dict) and utls.get('fingerprint'):
                        config['utlsFingerprint'] = utls['fingerprint']
                # 混淆
                obfs = out.get('obfs', {})
                if isinstance(obfs, dict) and obfs.get('type'):
                    config['obfs'] = True
                    config['obfs_type'] = obfs['type']
                    if obfs.get('password'):
                        config['obfs_password'] = obfs['password']
                return {
                    'name': tag, 'protocol': 'hysteria2', 'address': server, 'port': port,
                    'tag': f"hy2-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'tuic':
                password = out.get('password', '')
                uuid_val = out.get('uuid', '')
                if not password or not uuid_val:
                    return None
                config = {'uuid': uuid_val, 'password': password, 'tls': True}
                tls = out.get('tls', {})
                if isinstance(tls, dict):
                    if tls.get('server_name'):
                        config['sni'] = tls['server_name']
                    if tls.get('insecure'):
                        config['allowInsecure'] = True
                    if tls.get('disable_sni'):
                        config['disableSni'] = True
                    # alpn：sing-box 格式为数组，存储为逗号分隔字符串
                    alpn_val = tls.get('alpn', [])
                    if isinstance(alpn_val, list) and alpn_val:
                        config['alpn'] = ','.join(alpn_val)
                    elif isinstance(alpn_val, str) and alpn_val:
                        config['alpn'] = alpn_val
                    # uTLS fingerprint
                    utls = tls.get('utls', {})
                    if isinstance(utls, dict) and utls.get('fingerprint'):
                        config['utlsFingerprint'] = utls['fingerprint']
                # TUIC 特有字段（参考 NekoBox QUICBean.cpp:224-233）
                if out.get('congestion_control'):
                    config['congestionControl'] = out['congestion_control']
                if out.get('udp_relay_mode'):
                    config['udpRelayMode'] = out['udp_relay_mode']
                # udp_over_stream 与 udp_relay_mode 互斥
                if out.get('udp_over_stream'):
                    config['uos'] = True
                if out.get('zero_rtt_handshake'):
                    config['zeroRttHandshake'] = out['zero_rtt_handshake']
                if out.get('heartbeat'):
                    config['heartbeat'] = out['heartbeat']
                return {
                    'name': tag, 'protocol': 'tuic', 'address': server, 'port': port,
                    'tag': f"tuic-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            else:
                logger.debug(f"Skipping unsupported sing-box outbound type '{ptype}' for '{tag}'")
                return None

        except Exception as e:
            logger.debug(f"Failed to parse sing-box outbound '{tag}' (type={ptype}): {e}")
            return None

    def _parse_clash_yaml(self, content: str, sub_id: str) -> list:
        """尝试解析 Clash YAML 格式的订阅内容

        支持两种格式：
        1. Block-style（多行缩进）：
            proxies:
              - name: "node1"
                type: ss
                server: server1.example.com
                port: 443
                cipher: aes-256-gcm
                password: "password"
        2. Flow-style（单行花括号）：
            proxies:
              - {name: node1, type: ss, server: server1.example.com, port: 443, cipher: aes-256-gcm, password: password}

        如果内容不是 Clash YAML 格式（或解析失败），返回空列表，由调用方回退到代理链接解析。
        使用线性扫描而非完整 YAML 解析器，避免引入 pyyaml 依赖。
        支持嵌套结构（ws-opts, grpc-opts, reality-opts 等）。
        """
        # 快速检测：Clash YAML 必须包含 "proxies:" 行
        # 使用大小写不敏感匹配，因为某些订阅源使用 "Proxies:" 
        lines = content.strip().split('\n')
        proxies_start = -1
        for i, line in enumerate(lines):
            if line.strip().lower().startswith('proxies:'):
                proxies_start = i
                break
        if proxies_start < 0:
            return []

        import uuid
        nodes = []
        current_proxy: dict | None = None
        current_nested_key: str | None = None  # 当前嵌套键名（如 "ws-opts"）
        current_nested: dict | None = None  # 当前嵌套字典
        current_sub_nested_key: str | None = None  # 二级嵌套键名（如 "headers"）
        current_sub_nested: dict | None = None  # 二级嵌套字典

        def flush_nested():
            """将嵌套结构写入 current_proxy"""
            nonlocal current_nested_key, current_nested, current_sub_nested_key, current_sub_nested
            if current_proxy is None:
                return
            # 先将二级嵌套写入一级嵌套
            if current_sub_nested_key and current_sub_nested is not None and current_nested is not None:
                current_nested[current_sub_nested_key] = current_sub_nested
            # 再将一级嵌套写入 proxy
            if current_nested_key and current_nested is not None:
                current_proxy[current_nested_key] = current_nested
            current_nested_key = None
            current_nested = None
            current_sub_nested_key = None
            current_sub_nested = None

        def save_current_proxy():
            """保存当前 proxy 条目为节点"""
            nonlocal current_proxy
            flush_nested()
            if current_proxy:
                node = self._clash_proxy_to_node(current_proxy, sub_id)
                if node:
                    nodes.append(node)

        # 计算 proxies: 行的缩进级别
        proxies_indent = len(lines[proxies_start]) - len(lines[proxies_start].lstrip())

        for line in lines[proxies_start + 1:]:
            stripped = line.strip()
            # 空行跳过
            if not stripped:
                continue

            # 计算当前行缩进
            current_indent = len(line) - len(line.lstrip())

            # 遇到顶级键（缩进与 proxies 相同或更少，且不是列表项），proxies 列表结束
            if current_indent <= proxies_indent and not stripped.startswith('-'):
                break

            # 列表项以 "  - " 开头（缩进 + 短横线）
            if stripped.startswith('- '):
                # 新的 proxy 条目开始，先保存上一个
                save_current_proxy()
                # 解析新条目的第一行："  - key: value" 格式
                first_line = stripped[2:].strip()  # 去掉 "- " 前缀
                current_nested_key = None
                current_nested = None
                current_sub_nested_key = None
                current_sub_nested = None
                # 检测 flow-style YAML: - {key: value, key: value, ...}
                if first_line.startswith('{'):
                    current_proxy = self._parse_flow_style_entry(first_line)
                else:
                    current_proxy = {}
                    if ':' in first_line:
                        key, _, value = first_line.partition(':')
                        current_proxy[key.strip()] = value.strip().strip('"').strip("'")
            elif current_proxy is not None and ':' in stripped:
                key, _, value = stripped.partition(':')
                key = key.strip()
                value = value.strip().strip('"').strip("'")

                # 判断缩进级别来决定是顶级键还是嵌套键
                # 二级嵌套（如 ws-opts 下的 headers:）
                if current_nested_key and current_nested is not None:
                    if current_sub_nested_key and current_sub_nested is not None:
                        # 已经在二级嵌套中
                        if current_indent > (proxies_indent + 8):  # 3级缩进
                            current_sub_nested[key] = value
                            continue
                    # 检查是否是新的二级嵌套键（value 为空表示子字典）
                    if not value and key in ('headers',):
                        current_sub_nested_key = key
                        current_sub_nested = {}
                        continue
                    # 普通一级嵌套键值对
                    current_nested[key] = value
                    continue

                # 一级嵌套键（如 ws-opts:, grpc-opts:, reality-opts: 等）
                # 这些键的值为空（子字典）或为简单字符串
                if not value and key in ('ws-opts', 'grpc-opts', 'reality-opts', 'h2-opts', 'http-opts', 'smux-opts'):
                    # 先刷出之前的嵌套
                    flush_nested()
                    current_nested_key = key
                    current_nested = {}
                    continue

                # 普通顶级键值对
                current_proxy[key] = value

        # 保存最后一个 proxy 条目
        save_current_proxy()

        return nodes

    def _parse_flow_style_entry(self, content: str) -> dict:
        """解析 Clash YAML flow-style 条目: {key: value, key: value, ...}

        支持引号字符串、嵌套字典（如 ws-opts: {path: /v2}）和数组。
        状态机扫描，正确处理引号内的逗号和冒号。
        """
        content = content.strip()
        if content.startswith('{'):
            content = content[1:]
        if content.endswith('}'):
            content = content[:-1]

        result = {}
        buf = ''
        in_double_quote = False
        in_single_quote = False
        brace_depth = 0
        bracket_depth = 0

        for char in content:
            if char == '"' and not in_single_quote and brace_depth == 0 and bracket_depth == 0:
                in_double_quote = not in_double_quote
                buf += char
            elif char == "'" and not in_double_quote and brace_depth == 0 and bracket_depth == 0:
                in_single_quote = not in_single_quote
                buf += char
            elif char == '{' and not in_double_quote and not in_single_quote:
                brace_depth += 1
                buf += char
            elif char == '}' and not in_double_quote and not in_single_quote:
                brace_depth -= 1
                buf += char
            elif char == '[' and not in_double_quote and not in_single_quote:
                bracket_depth += 1
                buf += char
            elif char == ']' and not in_double_quote and not in_single_quote:
                bracket_depth -= 1
                buf += char
            elif char == ',' and not in_double_quote and not in_single_quote and brace_depth == 0 and bracket_depth == 0:
                self._parse_flow_kv(buf.strip(), result)
                buf = ''
            else:
                buf += char

        if buf.strip():
            self._parse_flow_kv(buf.strip(), result)

        return result

    def _parse_flow_kv(self, kv: str, result: dict):
        """解析 flow-style 中的单个 key: value 对，写入 result 字典"""
        if ':' not in kv:
            return
        key, _, value = kv.partition(':')
        key = key.strip().strip('"').strip("'")  # 去除键名两端引号（兼容 JSON 风格）
        value = value.strip()

        # 嵌套字典: {sub_key: sub_value, ...} -- 使用递归解析以处理多层嵌套
        if value.startswith('{') and value.endswith('}'):
            result[key] = self._parse_flow_style_entry(value)
            return

        # 数组: [item1, item2]
        if value.startswith('[') and value.endswith(']'):
            try:
                result[key] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                result[key] = value
            return

        # 普通值：去除引号
        value = value.strip('"').strip("'")
        result[key] = value

    @staticmethod
    def _try_b64_decode(text: str) -> str | None:
        """尝试对文本进行 Base64 解码，支持标准/URL-safe/有/无 padding 的格式

        Returns:
            解码后的字符串，如果解码失败返回 None
        """
        import base64
        text = text.replace('\n', '').replace('\r', '').replace(' ', '').replace('\t', '')
        padding = (-len(text)) % 4
        if padding:
            text += '=' * padding
        try:
            return base64.b64decode(text).decode('utf-8')
        except Exception:
            try:
                return base64.urlsafe_b64decode(text).decode('utf-8')
            except Exception:
                return None

    def _clash_proxy_to_node(self, proxy: dict, sub_id: str) -> dict | None:
        """将 Clash YAML 格式的单个 proxy 条目转换为内部节点数据

        支持的 type: ss, vmess, vless, trojan, hysteria2
        不支持的 type 静默跳过（如 socks5, http 等）
        """
        import uuid as _uid
        ptype = proxy.get('type', '').lower()
        name = proxy.get('name', 'Unnamed')
        server = proxy.get('server', '')
        port_raw = proxy.get('port', 0)
        # port 可能为字符串（flow-style YAML 解析结果），需转为整数
        try:
            port = int(port_raw) if port_raw else 0
        except (ValueError, TypeError):
            port = 0

        # 调试：记录解析到的关键字段，便于排查 port=0 等问题
        logger.debug(f"Clash proxy parsed: type={ptype!r}, name={name!r}, server={server!r}, port={port!r}, keys={list(proxy.keys())}")

        # 端口校验
        if port <= 0 or port > 65535:
            logger.warning(f"Skipping Clash proxy '{name}' with invalid port: {port} (raw={port_raw!r})")
            return None
        if not server:
            logger.warning(f"Skipping Clash proxy '{name}' with empty server")
            return None

        try:
            if ptype == 'ss':
                method = proxy.get('cipher', proxy.get('method', ''))
                password = proxy.get('password', '')
                if not method or not password:
                    return None
                # 加密方法校验（与 _parse_proxy_link 中的 SS 解析器一致）
                SUPPORTED_SS_METHODS = {'aes-256-gcm', 'aes-128-gcm', 'chacha20-ietf-poly1305',
                                        'xchacha20-ietf-poly1305', '2022-blake3-aes-128-gcm',
                                        '2022-blake3-aes-256-gcm', '2022-blake3-chacha20-poly1305',
                                        'none'}
                if method.lower() not in SUPPORTED_SS_METHODS:
                    logger.warning(f"Skipping SS proxy '{name}' with unsupported cipher: {method}")
                    return None
                config = {'method': method, 'password': password}
                return {
                    'name': name, 'protocol': 'shadowsocks', 'address': server, 'port': port,
                    'tag': f"ss-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'vmess':
                uuid_val = proxy.get('uuid', '')
                if not uuid_val:
                    return None
                config = {
                    'uuid': uuid_val,
                    'network': proxy.get('network', 'tcp'),
                    'security': proxy.get('cipher', 'auto'),
                }
                # alterId 已移除：sing-box 1.8+ 强制使用 VMess AEAD
                # 传输层
                if config['network'] == 'ws':
                    config['wsPath'] = proxy.get('ws-opts', {}).get('path', '/') if isinstance(proxy.get('ws-opts'), dict) else proxy.get('ws-path', '/')
                    ws_host = proxy.get('ws-opts', {}).get('headers', {}).get('Host', '') if isinstance(proxy.get('ws-opts'), dict) else proxy.get('ws-host', '')
                    if ws_host:
                        config['wsHeaders'] = {'Host': ws_host}
                elif config['network'] == 'grpc':
                    config['grpcServiceName'] = proxy.get('grpc-opts', {}).get('grpc-service-name', '') if isinstance(proxy.get('grpc-opts'), dict) else proxy.get('grpc-service-name', '')
                # TLS
                if proxy.get('tls') in (True, 'true', '1'):
                    config['tls'] = True
                    if proxy.get('servername'):
                        config['sni'] = proxy.get('servername')
                    if proxy.get('skip-cert-verify') in (True, 'true', '1'):
                        config['allowInsecure'] = True
                    # uTLS fingerprint (Clash: client-fingerprint)
                    fp = proxy.get('client-fingerprint', '')
                    if fp:
                        config['utlsFingerprint'] = fp
                return {
                    'name': name, 'protocol': 'vmess', 'address': server, 'port': port,
                    'tag': f"vmess-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'vless':
                uuid_val = proxy.get('uuid', '')
                if not uuid_val:
                    return None
                config = {'uuid': uuid_val, 'network': proxy.get('network', 'tcp')}
                flow = proxy.get('flow', '')
                if flow:
                    config['flow'] = flow
                # TLS
                if proxy.get('tls') in (True, 'true', '1'):
                    config['tls'] = True
                    sni = proxy.get('servername', proxy.get('sni', ''))
                    if sni:
                        config['sni'] = sni
                    if proxy.get('skip-cert-verify') in (True, 'true', '1'):
                        config['allowInsecure'] = True
                    # uTLS fingerprint (Clash: client-fingerprint)
                    fp = proxy.get('client-fingerprint', '')
                    if fp:
                        config['utlsFingerprint'] = fp
                    # Reality
                    if proxy.get('reality-opts') and isinstance(proxy.get('reality-opts'), dict):
                        config['reality'] = True
                        pbk = proxy['reality-opts'].get('public-key', '')
                        if pbk:
                            config['realityPublicKey'] = pbk
                        sid = proxy['reality-opts'].get('short-id', '')
                        if sid:
                            config['realityShortId'] = sid
                # 传输层
                if config['network'] == 'ws':
                    config['wsPath'] = proxy.get('ws-opts', {}).get('path', '/') if isinstance(proxy.get('ws-opts'), dict) else proxy.get('ws-path', '/')
                    ws_host = proxy.get('ws-opts', {}).get('headers', {}).get('Host', '') if isinstance(proxy.get('ws-opts'), dict) else proxy.get('ws-host', '')
                    if ws_host:
                        config['wsHeaders'] = {'Host': ws_host}
                elif config['network'] == 'grpc':
                    config['grpcServiceName'] = proxy.get('grpc-opts', {}).get('grpc-service-name', '') if isinstance(proxy.get('grpc-opts'), dict) else proxy.get('grpc-service-name', '')
                return {
                    'name': name, 'protocol': 'vless', 'address': server, 'port': port,
                    'tag': f"vless-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'trojan':
                password = proxy.get('password', '')
                if not password:
                    return None
                config = {'password': password, 'tls': True}
                sni = proxy.get('sni', proxy.get('servername', ''))
                if sni:
                    config['sni'] = sni
                if proxy.get('skip-cert-verify') in (True, 'true', '1'):
                    config['allowInsecure'] = True
                # uTLS fingerprint (Clash: client-fingerprint)
                fp = proxy.get('client-fingerprint', '')
                if fp:
                    config['utlsFingerprint'] = fp
                # 传输层
                network = proxy.get('network', 'tcp')
                config['network'] = network
                if network == 'ws':
                    config['wsPath'] = proxy.get('ws-opts', {}).get('path', '/') if isinstance(proxy.get('ws-opts'), dict) else proxy.get('ws-path', '/')
                    ws_host = proxy.get('ws-opts', {}).get('headers', {}).get('Host', '') if isinstance(proxy.get('ws-opts'), dict) else proxy.get('ws-host', '')
                    if ws_host:
                        config['wsHeaders'] = {'Host': ws_host}
                elif network == 'grpc':
                    config['grpcServiceName'] = proxy.get('grpc-opts', {}).get('grpc-service-name', '') if isinstance(proxy.get('grpc-opts'), dict) else proxy.get('grpc-service-name', '')
                return {
                    'name': name, 'protocol': 'trojan', 'address': server, 'port': port,
                    'tag': f"trojan-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype in ('hysteria2', 'hy2'):
                password = proxy.get('password', '')
                if not password:
                    return None
                config = {'password': password, 'tls': True}
                sni = proxy.get('sni', '')
                if sni:
                    config['sni'] = sni
                if proxy.get('skip-cert-verify') in (True, 'true', '1'):
                    config['allowInsecure'] = True
                # 混淆
                obfs = proxy.get('obfs', '')
                if obfs:
                    config['obfs'] = True
                    config['obfs_type'] = obfs
                    obfs_password = proxy.get('obfs-password', '')
                    if obfs_password:
                        config['obfs_password'] = obfs_password
                return {
                    'name': name, 'protocol': 'hysteria2', 'address': server, 'port': port,
                    'tag': f"hy2-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            elif ptype == 'tuic':
                password = proxy.get('password', '')
                uuid_val = proxy.get('uuid', '')
                if not password or not uuid_val:
                    return None
                config = {'uuid': uuid_val, 'password': password, 'tls': True}
                sni = proxy.get('sni', proxy.get('servername', ''))
                if sni:
                    config['sni'] = sni
                if proxy.get('skip-cert-verify') in (True, 'true', '1'):
                    config['allowInsecure'] = True
                if proxy.get('disable_sni') in (True, 'true', '1'):
                    config['disableSni'] = True
                # alpn：Clash 格式可能为数组或逗号分隔字符串
                alpn_val = proxy.get('alpn', '')
                if isinstance(alpn_val, list):
                    alpn_val = ','.join(alpn_val)
                if alpn_val:
                    config['alpn'] = alpn_val
                if proxy.get('congestion-controller'):
                    config['congestionControl'] = proxy['congestion-controller']
                if proxy.get('udp-relay-mode'):
                    config['udpRelayMode'] = proxy['udp-relay-mode']
                # heartbeat-interval（参考 NekoBox GroupUpdater.cpp:994-996）
                heartbeat_interval = proxy.get('heartbeat-interval')
                if heartbeat_interval:
                    try:
                        # Clash 格式通常为毫秒数，转换为 sing-box 时间格式
                        ms = int(heartbeat_interval)
                        if ms >= 1000:
                            config['heartbeat'] = f"{ms // 1000}s"
                        else:
                            config['heartbeat'] = f"{ms}ms"
                    except (ValueError, TypeError):
                        config['heartbeat'] = str(heartbeat_interval)
                return {
                    'name': name, 'protocol': 'tuic', 'address': server, 'port': port,
                    'tag': f"tuic-{_uid.uuid4().hex[:16]}", 'config': config,
                    'subscription_id': sub_id, 'is_enabled': 1,
                }

            else:
                # 不支持的类型（socks5, http 等）静默跳过
                logger.debug(f"Skipping unsupported Clash proxy type '{ptype}' for '{name}'")
                return None

        except Exception as e:
            logger.debug(f"Failed to parse Clash proxy '{name}' (type={ptype}): {e}")
            return None

    def _parse_subscription_content(self, content: str, sub_id: str) -> list:
        """解析订阅内容为节点数据列表"""
        nodes = []
        for line in content.strip().split('\n'):
            line = line.strip()
            # 跳过空行和注释行（以 # 开头）
            if not line or line.startswith('#'):
                continue
            node = self._parse_proxy_link(line, sub_id)
            if node:
                nodes.append(node)
        return nodes
    
    def _parse_proxy_link(self, link: str, sub_id: str) -> dict | None:
        """解析单个代理协议链接"""
        import base64
        import uuid
        try:
            if link.startswith('vmess://'):
                decoded = self._try_b64_decode(link[8:])
                if not decoded:
                    return None
                payload = decoded
                raw = json.loads(payload)
                # 将 vmess 标准基础64 JSON 键名映射为 _vmess_outbound 期望的 camelCase 键名
                # vmess 标准键：id=UUID, net=传输层, scy=加密, aid=alterId, path=ws路径, host=ws主机
                # _vmess_outbound 期望：uuid, network, security, alterId, wsPath, wsHeaders, grpcServiceName
                config = {
                    'uuid': raw.get('id', ''),
                    'network': raw.get('net', 'tcp'),
                    'security': raw.get('scy', 'auto'),
                    # alterId 已移除：sing-box 1.8+ 强制使用 VMess AEAD（等价于 alterId=0）
                    # 即使订阅源包含 aid 字段也不再使用
                }
                # 传输层配置
                if config['network'] == 'ws':
                    config['wsPath'] = raw.get('path', '/')
                    ws_host = raw.get('host', '')
                    if ws_host:
                        config['wsHeaders'] = {'Host': ws_host}
                elif config['network'] == 'grpc':
                    config['grpcServiceName'] = raw.get('path', '') or raw.get('serviceName', '')
                # TLS 配置
                if raw.get('tls') in ('tls', True):
                    config['tls'] = True
                    if raw.get('sni'):
                        config['sni'] = raw['sni']
                    if raw.get('allowInsecure') in ('1', 'true', True):
                        config['allowInsecure'] = True
                # 端口校验：无效端口跳过该节点（避免单个坏节点导致整个订阅失败）
                port = int(raw.get('port', 0))
                if port <= 0 or port > 65535:
                    logger.warning(f"Skipping VMess node '{raw.get('ps', 'unknown')}' with invalid port: {port}")
                    return None
                return {
                    'name': raw.get('ps', 'VMess'),
                    'protocol': 'vmess',
                    'address': raw.get('add', ''),
                    'port': port,
                    'tag': f"vmess-{uuid.uuid4().hex[:16]}",
                    'config': config,
                    'subscription_id': sub_id,
                    'is_enabled': 1,
                }
            elif link.startswith('vless://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                config = {'uuid': parsed.username or ''}
                # 端口校验：在解析参数前先检查端口有效性
                vless_port = parsed.port or 0
                vless_name = params.get('name', ['VLESS'])[0] or parsed.fragment or 'VLESS'
                if vless_port <= 0 or vless_port > 65535:
                    logger.warning(f"Skipping VLESS node '{vless_name}' with invalid port: {vless_port}")
                    return None
                if not parsed.hostname:
                    logger.warning(f"Skipping VLESS node '{vless_name}' with empty address")
                    return None
                flow = params.get('flow', [''])[0]
                if flow:
                    config['flow'] = flow
                # TLS 配置
                security = params.get('security', [''])[0]
                if security in ('tls', 'reality'):
                    config['tls'] = True
                    sni = params.get('sni', [''])[0]
                    if sni:
                        config['sni'] = sni
                    fp = params.get('fp', [''])[0]
                    if fp:
                        config['utlsFingerprint'] = fp
                    alpn = params.get('alpn', [''])[0]
                    if alpn:
                        config['alpn'] = alpn.split(',')
                    allow_insecure = params.get('allowInsecure', ['0'])[0]
                    config['allowInsecure'] = allow_insecure == '1'
                    if security == 'reality':
                        config['reality'] = True
                        pbk = params.get('pbk', [''])[0]
                        if pbk:
                            config['realityPublicKey'] = pbk
                        sid = params.get('sid', [''])[0]
                        if sid:
                            config['realityShortId'] = sid
                # 传输层
                transport = params.get('type', ['tcp'])[0]
                config['network'] = transport
                if transport == 'ws':
                    config['wsPath'] = params.get('path', ['/'])[0]
                    host = params.get('host', [''])[0]
                    if host:
                        config['wsHeaders'] = {'Host': host}
                elif transport == 'grpc':
                    config['grpcServiceName'] = params.get('serviceName', [''])[0]
                return {
                    'name': vless_name,
                    'protocol': 'vless',
                    'address': parsed.hostname,
                    'port': vless_port,
                    'tag': f"vless-{uuid.uuid4().hex[:16]}",
                    'config': config,
                    'subscription_id': sub_id,
                    'is_enabled': 1,
                }
            elif link.startswith('trojan://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                config = {'password': parsed.username or ''}
                # TLS 配置
                config['tls'] = True
                sni = params.get('sni', [''])[0]
                if sni:
                    config['sni'] = sni
                allow_insecure = params.get('allowInsecure', ['0'])[0]
                config['allowInsecure'] = allow_insecure == '1'
                fp = params.get('fp', [''])[0]
                if fp:
                    config['utlsFingerprint'] = fp
                # 传输层
                transport = params.get('type', ['tcp'])[0]
                config['network'] = transport
                if transport == 'ws':
                    config['wsPath'] = params.get('path', ['/'])[0]
                    host = params.get('host', [''])[0]
                    if host:
                        config['wsHeaders'] = {'Host': host}
                elif transport == 'grpc':
                    config['grpcServiceName'] = params.get('serviceName', [''])[0]
                name = params.get('name', ['Trojan'])[0] or parsed.fragment or 'Trojan'
                trojan_port = parsed.port or 0
                if trojan_port <= 0 or trojan_port > 65535:
                    logger.warning(f"Skipping Trojan node '{name}' with invalid port: {trojan_port}")
                    return None
                return {
                    'name': name,
                    'protocol': 'trojan',
                    'address': parsed.hostname or '',
                    'port': trojan_port,
                    'tag': f"trojan-{uuid.uuid4().hex[:16]}",
                    'config': config,
                    'subscription_id': sub_id,
                    'is_enabled': 1,
                }
            elif link.startswith('ss://'):
                import base64
                name = ''
                if '#' in link:
                    link, name = link.rsplit('#', 1)
                    # URL 解码节点名称（如 %20 → 空格，%E4%B8%AD → 中文字符）
                    from urllib.parse import unquote
                    name = unquote(name)
                rest = link[5:]
                # SIP002 格式: ss://base64(method:password)@host:port#name
                if '@' in rest:
                    # SIP002: userinfo@host:port
                    try:
                        userinfo, hostport = rest.rsplit('@', 1)
                        decoded = base64.urlsafe_b64decode(userinfo + '=' * (-len(userinfo) % 4)).decode('utf-8')
                        method, password = decoded.split(':', 1)
                        host, port = hostport.rsplit(':', 1)
                    except Exception:
                        return None
                else:
                    # Legacy: ss://base64(method:password@host:port)
                    try:
                        decoded = base64.urlsafe_b64decode(rest + '=' * (-len(rest) % 4)).decode('utf-8')
                        method, addr = decoded.split(':', 1)
                        password, host_port = addr.split('@', 1)
                        host, port = host_port.rsplit(':', 1)
                    except Exception:
                        return None
                # 端口校验：无效端口跳过该节点（与 VMess 解析器一致）
                ss_port = int(port)
                if ss_port <= 0 or ss_port > 65535:
                    logger.warning(f"Skipping SS node '{name or 'unknown'}' with invalid port: {ss_port}")
                    return None
                # 加密方法校验：不支持的 method 导致 sing-box 启动失败
                SUPPORTED_SS_METHODS = {'aes-256-gcm', 'aes-128-gcm', 'chacha20-ietf-poly1305',
                                        'xchacha20-ietf-poly1305', '2022-blake3-aes-128-gcm',
                                        '2022-blake3-aes-256-gcm', '2022-blake3-chacha20-poly1305',
                                        'none'}
                if method.lower() not in SUPPORTED_SS_METHODS:
                    logger.warning(f"Skipping SS node '{name or 'unknown'}' with unsupported method: {method}")
                    return None
                return {
                    'name': name or 'SS',
                    'protocol': 'shadowsocks',
                    'address': host,
                    'port': ss_port,
                    'tag': f"ss-{uuid.uuid4().hex[:16]}",
                    'config': {'method': method, 'password': password},
                    'subscription_id': sub_id,
                    'is_enabled': 1,
                }
            elif link.startswith('hysteria2://') or link.startswith('hy2://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                # hysteria2 URL 格式: hysteria2://auth_str@host:port?params#name
                # auth_str (即 password) 在 URL userinfo 部分
                config = {'password': parsed.username or ''}
                # hysteria2 始终使用 TLS
                config['tls'] = True
                # TLS 配置（sni/insecure）
                sni = params.get('sni', [''])[0]
                if sni:
                    config['sni'] = sni
                # 兼容两种参数名：insecure（Hysteria2 原生）和 allowInsecure（Clash 兼容格式）
                allow_insecure = params.get('insecure', params.get('allowInsecure', ['0']))[0]
                config['allowInsecure'] = allow_insecure in ('1', 'true')
                # 混淆
                obfs = params.get('obfs', [''])[0]
                if obfs:
                    config['obfs'] = True
                    config['obfs_type'] = obfs
                    obfs_password = params.get('obfs-password', [''])[0]
                    if obfs_password:
                        config['obfs_password'] = obfs_password
                name = params.get('name', ['Hysteria2'])[0] or parsed.fragment or 'Hysteria2'
                hy2_port = parsed.port or 0
                if hy2_port <= 0 or hy2_port > 65535:
                    logger.warning(f"Skipping Hysteria2 node '{name}' with invalid port: {hy2_port}")
                    return None
                return {
                    'name': name,
                    'protocol': 'hysteria2',
                    'address': parsed.hostname or '',
                    'port': hy2_port,
                    'tag': f"hy2-{uuid.uuid4().hex[:16]}",
                    'config': config,
                    'subscription_id': sub_id,
                    'is_enabled': 1,
                }
            elif link.startswith('tuic://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                config = {
                    'uuid': parsed.username or '',
                    'password': (parsed.password or ''),
                    'tls': True,
                }
                sni = params.get('sni', [''])[0]
                if sni:
                    config['sni'] = sni
                # 兼容 NekoBox 下划线格式和 hyphen 格式（参考 NekoBox QUICBean.cpp:308-313）
                allow_insecure = params.get('allow_insecure', params.get('allowInsecure', ['0']))[0]
                config['allowInsecure'] = allow_insecure in ('1', 'true')
                disable_sni = params.get('disable_sni', params.get('disableSni', ['0']))[0]
                if disable_sni in ('1', 'true'):
                    config['disableSni'] = True
                alpn = params.get('alpn', [''])[0]
                if alpn:
                    config['alpn'] = alpn
                # congestion_control：兼容下划线和连字符格式
                congestion = params.get('congestion_control', params.get('congestion-control', ['']))[0]
                if congestion:
                    config['congestionControl'] = congestion
                # udp_relay_mode：兼容下划线和连字符格式
                udp_relay = params.get('udp_relay_mode', params.get('udp-relay-mode', ['']))[0]
                if udp_relay:
                    config['udpRelayMode'] = udp_relay
                name = params.get('name', ['TUIC'])[0] or parsed.fragment or 'TUIC'
                tuic_port = parsed.port or 0
                if tuic_port <= 0 or tuic_port > 65535:
                    logger.warning(f"Skipping TUIC node '{name}' with invalid port: {tuic_port}")
                    return None
                if not parsed.hostname:
                    logger.warning(f"Skipping TUIC node '{name}' with empty address")
                    return None
                return {
                    'name': name,
                    'protocol': 'tuic',
                    'address': parsed.hostname,
                    'port': tuic_port,
                    'tag': f"tuic-{uuid.uuid4().hex[:16]}",
                    'config': config,
                    'subscription_id': sub_id,
                    'is_enabled': 1,
                }
            elif link.startswith('wg://') or link.startswith('wireguard://'):
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(link)
                params = parse_qs(parsed.query)
                config = {
                    'privateKey': parsed.username or '',
                    'peerPublicKey': params.get('public-key', [''])[0] or params.get('pk', [''])[0],
                    'reserved': [int(x) for x in params.get('reserved', [''])[0].split(',') if x.strip().isdigit()] if params.get('reserved', [''])[0] else [],
                    'localAddress': params.get('ip', [''])[0].split(',') if params.get('ip', [''])[0] else [],
                }
                name = params.get('name', ['WireGuard'])[0] or parsed.fragment or 'WireGuard'
                wg_port = parsed.port or 0
                if wg_port <= 0 or wg_port > 65535:
                    logger.warning(f"Skipping WireGuard node '{name}' with invalid port: {wg_port}")
                    return None
                return {
                    'name': name,
                    'protocol': 'wireguard',
                    'address': parsed.hostname or '',
                    'port': wg_port,
                    'tag': f"wg-{uuid.uuid4().hex[:16]}",
                    'config': config,
                    'subscription_id': sub_id,
                    'is_enabled': 1,
                }
        except Exception as e:
            logger.debug(f"Failed to parse proxy link: {e}")
        return None
