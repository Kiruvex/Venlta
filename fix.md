# Venlta vs NekoBox 对照检查问题清单

基于对 `download/nekobox`（参考标准）和 `download/Venlta` 的完整代码对照，以下为发现的差异和问题。

---

## 🔴 高优先级

### ~~1. 规则 action 类型不完整~~ ✅ 已修复
- **NekoBox**: 支持 route/reject/sniff/resolve/hijack-dns 等多种 action
- **Venlta**: ~~仅支持 route（通过 outbound 字段），缺少 reject/sniff/resolve/hijack-dns~~ 已修复
- **修复内容**:
  - `frontend/src/types/rule.d.ts` — 增加 `RuleAction` 类型（route/reject/sniff/resolve/hijack-dns）、`RejectMethod` 类型、`rejectMethod`/`resolveServer` 字段
  - `backend/core/database.py` — 新增 Migration V2：添加 `action`/`reject_method`/`resolve_server` 列；更新 `RULE_COLUMNS`、`RULE_KEY_MAP` 白名单和映射
  - `backend/core/config_manager.py` — `_build_route()` 根据 action 类型生成不同的 sing-box 规则 JSON：
    - `route` → `{"outbound": "..."}`（默认，向后兼容）
    - `reject` → `{"action": "reject", "method": "default/conn-reset"}`
    - `sniff` → `{"action": "sniff", "inbound": [...]}`
    - `resolve` → `{"action": "resolve", "server": "dns-direct"}`
    - `hijack-dns` → `{"action": "hijack-dns", "inbound": [...]}`
    - 兼容旧数据：outbound_tag 为 "block" 时自动转为 reject action
  - `frontend/src/pages/rules/index.tsx` — 新增 action 选择器 UI，条件显示 outbound/rejectMethod/resolveServer 选项
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 action 相关翻译（action_route/reject/sniff/resolve/hijack_dns + reject_method + resolve_server + hint 文本）

---

## 🟡 中优先级

### ~~2. 缺少 mux/brutal 拥塞控制配置~~ ✅ 已修复
- **NekoBox**: 支持启用 multiplex (mux) 和 Brutal 拥塞控制
- **Venlta**: ~~完全缺失~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — 新增 `_add_multiplex()` 方法，为 vmess/vless/trojan/shadowsocks 出站添加 `multiplex` 字段
    - 支持 muxEnabled/muxProtocol/muxMaxStreams/muxPadding 节点级配置
    - 支持 brutalEnabled/brutalSpeed Brutal 拥塞控制（参考 NekoBox ConfigBuilder.cpp:558-604）
    - VLESS + flow (XTLS) 自动跳过 mux；grpc/quic 传输不支持 mux
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 mux/brutal 相关翻译

### ~~3. 缺少 fakeip DNS 支持~~ ✅ 已修复
- **NekoBox**: 支持 fakeip DNS 类型（`{"type": "fakeip"}`），配合 fakeip IP 池
- **Venlta**: ~~`_parse_dns_address()` 虽识别 `fakeip` 前缀，但无 fakeip IP 池配置~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — `_build_dns()` 检测 fakeip 类型 DNS 服务器时，自动添加 `fakeip` 对象（inet4_range/inet6_range 可配置）
  - `frontend/src/pages/settings/index.tsx` — DNS 设置区新增 underlying_dns、fakeip_inet4_range、fakeip_inet6_range 配置项
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 fakeip 相关翻译

### ~~4. 订阅无去重~~ ✅ 已修复
- **NekoBox**: 使用 `ProfileFilter` 按 type+address+port+bean 去重，防止重复节点
- **Venlta**: ~~无去重逻辑~~ 已修复
- **修复内容**:
  - `backend/core/subscription.py` — `_fetch_and_parse()` 新增 `_node_identity_key()` 去重函数
    - 按 protocol + address + port + 协议关键标识字段（uuid/password/method+password/privateKey）生成唯一标识
    - 新节点列表内部去重（跳过重复项）
    - 新旧节点匹配时保留旧节点（保留用户自定义设置如启用/禁用状态），仅添加真正新增的节点
    - 删除不在新节点列表中的旧节点（过期节点清理）
  - 参考 NekoBox ProfileFilter.Common() 实现

### ~~5. domain_strategy 硬编码~~ ✅ 已修复
- **NekoBox**: `domain_strategy` 可在设置中配置（prefer_ipv4/prefer_ipv6/ipv4_only/ipv6_only）
- **Venlta**: ~~硬编码为 `"prefer_ipv4"`~~ 已修复
- **修复内容**:
  - `frontend/src/pages/settings/index.tsx` — DNS 设置区新增 `dns_strategy` 和 `outbound_domain_strategy` 选择器
  - `backend/core/config_manager.py` — `_build_dns()` 从数据库读取 dns_strategy；`_build_route()` 从数据库读取 outbound_domain_strategy，设置 route.strategy 和 default_domain_resolver.strategy
  - `frontend/src/i18n/en.json` / `zh.json` — 新增策略相关翻译
  - `backend/bridge/venlta_bridge.py` — config_affecting_keys 新增 dns_strategy/outbound_domain_strategy

### ~~6. 缺少 TUN split routing~~ ✅ 已修复
- **NekoBox**: 支持 TunSplit（proxy/direct/block 地址列表），精细控制 TUN 流量
- **Venlta**: ~~仅使用 `route_exclude_address` 排除私有 IP~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — TUN inbound 新增可配置的 `route_exclude_address`（自定义排除列表，留空使用默认私有 IP 段）和 `route_include_address`（仅指定地址走 TUN）
  - `frontend/src/pages/settings/index.tsx` — 新增 TUN Split Routing 设置区（tun_route_exclude_address/tun_route_include_address）
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 TUN 分流相关翻译
  - `backend/bridge/venlta_bridge.py` — config_affecting_keys 新增 tun_route_exclude_address/tun_route_include_address

### ~~7. 缺少 utls 指纹配置~~ ✅ 已修复
- **NekoBox**: 可配置 uTLS 指纹（chrome/firefox/safari/ios/android 等）
- **Venlta**: ~~缺失~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — 新增 `_add_utls_fingerprint()` 方法，为所有 TLS 出站（vmess/vless/trojan/hysteria2）添加 uTLS 指纹支持
    - 优先级：节点级 utlsFingerprint > 全局默认 utls_fingerprint
    - Reality 协议自动使用 "random" 指纹（NekoBox 行为）
    - sing-box 格式：`{"utls": {"enabled": true, "fingerprint": "chrome"}}`
  - `frontend/src/pages/settings/index.tsx` — DNS 设置区新增全局 uTLS 指纹选择器
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 utls_fingerprint 翻译
  - `backend/core/subscription.py` — 订阅解析器（sing-box JSON/Clash YAML/代理链接）新增 utlsFingerprint 字段解析

---

## 🟢 低优先级

### ~~8. 协议支持较少~~ ✅ 已修复
- **NekoBox**: 20+ 种协议（含 tuic, ssh, naive, tor, mieru, shadowtls, anytls, juicity, trusttunnel, chain, custom/extracore, http, socks, tailscale, amneziawg）
- **Venlta**: ~~6 种核心协议（vmess, vless, trojan, shadowsocks, hysteria2, wireguard）~~ → 7 种（新增 tuic）
- **修复内容**:
  - `backend/core/database.py` — V1 迁移更新 CHECK 约束包含 'tuic'；新增 V3 迁移重建 nodes 表以支持 tuic 协议
  - `backend/core/config_manager.py` — 新增 `_tuic_outbound()` 方法，支持 TLS/uTLS 指纹、congestion_control（cubic/new_reno/bbr）、udp_relay_mode（native/quic）、zero_rtt_handshake；更新 `outbound_map` 和 `required_fields`
  - `backend/core/subscription.py` — 全部 3 种解析器新增 tuic 解析：sing-box JSON outbound、Clash YAML proxy、tuic:// 代理链接；`_node_identity_key()` 新增 tuic 去重（uuid:password）
  - `frontend/src/types/node.d.ts` — protocol 类型联合增加 'tuic'
  - `frontend/src/pages/nodes/index.tsx` — VALID_PROTOCOLS/PROTOCOL_COLORS 新增 tuic（indigo）；添加节点弹窗新增 tuic 表单字段（UUID/Password/SNI/Congestion Control/UDP Relay Mode）
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 congestion_control/udp_relay_mode 翻译
  - **备注**: tuic 为最常见 QUIC 协议，其他小众协议（ssh/naive/tor/mieru/shadowtls/anytls/juicity/trusttunnel/chain/custom/extracore/http/socks/tailscale/amneziawg）可按需逐步添加

### ~~9. 缺少 SIP008 ShadowSocks 格式解析~~ ✅ 已修复
- **NekoBox**: 支持 SIP008 JSON 格式
- **Venlta**: ~~不支持~~ 已修复
- **修复内容**:
  - `backend/core/subscription.py` — 新增 `_parse_sip008()` 方法，解析 SIP008 JSON 格式（`{"version": 8, "servers": [...]}`）
    - 支持 server/server_port/method/password/remarks 字段
    - 加密方法校验与现有 SS 解析器一致
  - 订阅解析优先级更新：SIP008 > WireGuard 配置 > sing-box JSON > Clash YAML > 代理链接

### ~~10. 缺少 Wireguard 配置文件解析~~ ✅ 已修复
- **NekoBox**: 可解析 Wireguard INI 风格配置文件
- **Venlta**: ~~不支持~~ 已修复
- **修复内容**:
  - `backend/core/subscription.py` — 新增 `_parse_wireguard_config()` 方法，解析 WireGuard INI 风格 `.conf` 文件
    - 解析 [Interface] 段（PrivateKey/Address/DNS）
    - 解析 [Peer] 段（PublicKey/Endpoint/AllowedIPs/Reserved）
    - 支持 IPv4 和 IPv6 Endpoint 格式（含方括号表示法）
    - 多 Peer 段支持（每个 Peer 生成一个节点）
  - 订阅解析优先级更新（位于 SIP008 之后、sing-box JSON 之前）

### ~~11. 缺少 jsdelivr CDN 镜像~~ ✅ 已修复
- **NekoBox**: rule_set URL 自动转换为 jsdelivr CDN 镜像（GCORE/QUANTIL/FASTLY/CDN/testingcf）
- **Venlta**: ~~直接使用原始 GitHub URL~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — 新增 `_convert_to_jsdelivr()` 静态方法，将 GitHub raw/releases URL 转换为 jsdelivr CDN 镜像
    - 支持 5 种 CDN：testingcf/gcore/quantil/fastly/cdn
    - 仅转换 raw.githubusercontent.com 和 github.com URLs，非 GitHub URL 保持原样
  - `_build_route()` 中 rule_set 构建时根据 `rule_set_cdn` 设置自动应用 CDN 镜像
  - adblock 规则集 URL 也受 CDN 镜像影响
  - `frontend/src/pages/settings/index.tsx` — 新增 CDN Mirror 设置区，可选 5 种 CDN 或禁用
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 CDN 镜像相关翻译
  - `backend/bridge/venlta_bridge.py` — config_affecting_keys 新增 rule_set_cdn

### ~~12. 缺少订阅 HTTP header 解析~~ ✅ 已修复
- **NekoBox**: 解析 `Profile-Title` HTTP 响应头获取订阅名称
- **Venlta**: ~~不解析~~ 已修复
- **修复内容**:
  - `backend/core/subscription.py` — `_fetch_and_parse()` 新增 Profile-Title HTTP 响应头解析
    - 支持 `profile-title` 和 `Profile-Title` 两种大小写格式
    - 自动 URL 解码（兼容编码后的中文订阅名）
    - 解析成功后自动更新订阅名称（`db.update_subscription()`）

### ~~13. 缺少订阅去重/增量更新~~ ✅ 已修复（见 #4）

### ~~14. 缺少 HWID headers~~ ✅ 已修复
- **NekoBox**: 请求订阅时附带 x-hwid/x-device-os/x-ver-os/x-device-model headers
- **Venlta**: ~~仅 User-Agent~~ 已修复
- **修复内容**:
  - `backend/core/subscription.py` — `_fetch_and_parse()` 更新 HTTP 请求头：
    - `x-hwid`: 基于 MAC 地址的设备标识（`uuid.getnode()`）
    - `x-device-os`: 操作系统名（`platform.system()`）
    - `x-ver-os`: 操作系统版本（`platform.version()`）
    - `x-device-model`: 机器架构（`platform.machine()`）
    - 保留原有 User-Agent 头

### 15. 缺少热键支持
- **NekoBox**: QHotkey 全局快捷键
- **Venlta**: 无
- **影响**: 无法通过快捷键快速切换代理/节点
- **备注**: 需要原生 QHotkey 库集成，属于桌面端特有功能，Web 前端无法实现

### 16. 缺少 QR 码扫描
- **NekoBox**: quirc 库支持扫描 QR 码导入节点
- **Venlta**: 无
- **影响**: 无法通过摄像头/图片导入节点
- **备注**: 需要原生 quirc 库或摄像头 API 集成，属于桌面端特有功能

### ~~17. 缺少 NTP 出站~~ ✅ 已修复
- **NekoBox**: 支持 NTP 出站时间同步
- **Venlta**: ~~无~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — `_build_config()` 新增 NTP 出站支持
    - 新增 `ntp_enabled` 开关（默认关闭）
    - 可配置 `ntp_server`（默认 time.google.com）、`ntp_server_port`（默认 123）、`ntp_interval`（默认 30m）
    - NTP outbound 插入在 outbounds 列表最前面（dns-out 之前）
  - `frontend/src/pages/settings/index.tsx` — 新增 NTP Time Sync 设置区（4 个配置项）
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 NTP 相关翻译
  - `backend/bridge/venlta_bridge.py` — config_affecting_keys 新增 ntp_enabled/ntp_server/ntp_server_port/ntp_interval

### ~~18. 规则集 (rule_set) 缺少本地类型~~ ✅ 已修复
- **NekoBox**: rule_set 支持 `remote` 和 `local` 两种类型
- **Venlta**: ~~仅 `remote`~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — `_build_route()` 中 rule_set 构建区分 remote/local 类型
    - `remote` 类型：生成 `{"type": "remote", "url": "...", "download_detour": "proxy"}`（向后兼容）
    - `local` 类型：生成 `{"type": "local", "path": "..."}`，url 字段存储本地文件路径
  - `frontend/src/pages/rules/index.tsx` — 添加规则集弹窗新增 Type 选择器（Remote/Local）
    - 选择 Local 时 URL 标签变为 "本地路径"，placeholder 变为文件路径格式
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 rule_set_type/rule_set_type_remote/rule_set_type_local/rule_set_path 翻译
  - **注**: database.py 中 rule_sets 表已有 `type` 列（CHECK: 'local'/'remote'），无需额外迁移

### ~~19. 缺少 adblock 注入~~ ✅ 已修复
- **NekoBox**: 自动注入广告拦截规则
- **Venlta**: ~~无~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — `_build_route()` 新增 adblock 自动注入
    - 新增 `adblock_enabled` 开关（默认关闭）
    - 使用 SagerNet geosite-category-ads-all 二进制规则集（`geosite-category-ads-all.srs`）
    - 自动生成 reject 路由规则（`{"rule_set": ["adblock"], "action": "reject"}`）
    - 受 CDN 镜像设置影响（启用 CDN 时 adblock URL 也自动转换）
  - `frontend/src/pages/settings/index.tsx` — 新增 Ad Blocking 设置区（开关 + 说明）
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 adblock 相关翻译
  - `backend/bridge/venlta_bridge.py` — config_affecting_keys 新增 adblock_enabled

### ~~20. DNS final 出站选择不可配置~~ ✅ 已修复
- **NekoBox**: `dns_final_out_direct` 可配置 DNS 默认出站
- **Venlta**: ~~硬编码 `dns-remote`~~ 已修复
- **修复内容**:
  - `backend/core/config_manager.py` — `_build_dns()` 新增 `dns_final_out_direct` 配置
    - 默认 `dns-remote`（代理 DNS），可配置为 `dns-direct`（直连 DNS）
    - 服务器排列顺序跟随 final 选择（dns_final_out_direct 时 dns-direct 排前面）
  - `frontend/src/pages/settings/index.tsx` — DNS 设置区新增 DNS Default Outbound 选择器（Remote/Proxy vs Direct）
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 dns_final_out 相关翻译
  - `backend/bridge/venlta_bridge.py` — config_affecting_keys 新增 dns_final_out_direct

---

## ✅ 已正确实现（与 NekoBox 一致）

- DNS 三服务器架构（dns-remote/dns-direct/dns-local，打破循环依赖）
- Mixed inbound（HTTP+SOCKS5 合并）
- TUN 提权（Linux setcap/pkexec, Windows UAC, macOS osascript）
- 系统代理配置（Windows 注册表, GNOME, KDE, macOS networksetup）
- sing-box 配置验证 + 备份/回滚
- 防抖配置重生成
- 数据加密（Fernet PBKDF2）
- 自动更新（GitHub Releases + ETag 缓存）
- 速度测试（Cloudflare 10MB 下载）
- 连接管理（Clash API）
- 订阅解析优先级（SIP008 → WireGuard 配置 → sing-box JSON → Clash YAML → 代理链接）— ✅ 更新
- tag 冲突处理（后缀 + UUID 重试）
- sing-box 1.12+ DNS 新格式（type+server 替代 URL）
- sing-box 1.13+ sniff 新格式（route rule action 替代 inbound 字段）
- 规则 action 类型（route/reject/sniff/resolve/hijack-dns）— ✅ 新增
- domain_strategy 可配置（dns_strategy + outbound_domain_strategy）— ✅ 新增
- uTLS 指纹（节点级 + 全局默认，Reality 自动 random）— ✅ 新增
- mux/brutal 拥塞控制（multiplex + Brutal）— ✅ 新增
- fakeip DNS 支持（inet4_range/inet6_range 可配置）— ✅ 新增
- 订阅去重（protocol+address+port+关键config，保留旧节点设置）— ✅ 新增
- TUN split routing（route_include/exclude_address 可配置）— ✅ 新增
- tuic 协议（QUIC 代理，congestion_control/udp_relay_mode/zero_rtt_handshake）— ✅ 新增
- SIP008 ShadowSocks 格式解析 — ✅ 新增
- WireGuard INI 配置文件解析 — ✅ 新增
- jsdelivr CDN 镜像（testingcf/gcore/quantil/fastly/cdn）— ✅ 新增
- 订阅 HTTP header 解析（Profile-Title）— ✅ 新增
- HWID headers（x-hwid/x-device-os/x-ver-os/x-device-model）— ✅ 新增
- NTP 出站时间同步（server/port/interval 可配置）— ✅ 新增
- 本地 rule_set 类型（local path）— ✅ 新增
- Adblock 自动注入（geosite-category-ads-all + reject）— ✅ 新增
- DNS final 出站可配置（dns-remote/dns-direct）— ✅ 新增
- TUIC 默认 congestion_control=bbr（与 NekoBox 一致）— ✅ 修正
- TUIC heartbeat/uos(alpn/disableSni) 字段支持 — ✅ 新增
- Profile-Title base64: 前缀递归解码 — ✅ 新增
- Adblock 规则集使用 217heidai（与 NekoBox 一致）— ✅ 修正
- NTP 顶层配置（而非 outbound）— ✅ 修正
- HWID custom_hwid_params 自定义覆盖 — ✅ 新增
- selector + urltest outbound
- gvisor strict_route 自动修复
- cache_file 持久化

---

## 🔵 NekoBox 仓库对照修复（2026-03-05）

基于克隆 `https://github.com/qr243vbi/nekobox` 后的逐行代码对照，发现的额外差异及修复。

### ~~8a. TUIC 默认 congestion_control 不一致~~ ✅ 已修复
- **NekoBox**: 默认 `"bbr"`（QUICBean.hpp:51）
- **Venlta**: ~~默认 `"cubic"`~~ → 修正为 `"bbr"`
- **修复**: `config_manager.py` `_tuic_outbound()` 默认值改为 `"bbr"`；前端默认值同步更新

### ~~8b. TUIC 缺少 heartbeat 字段~~ ✅ 已修复
- **NekoBox**: `heartbeat = "10s"`（QUICBean.hpp:54），写入 `outbound["heartbeat"]`（QUICBean.cpp:182）
- **Venlta**: ~~不支持~~ → 已添加
- **修复**:
  - `config_manager.py` `_tuic_outbound()` — 新增 heartbeat 字段输出
  - `subscription.py` — sing-box JSON/Clash YAML/代理链接解析器新增 heartbeat 解析
  - `frontend/src/pages/nodes/index.tsx` — 新增 heartbeat 输入框（默认 "10s"）

### ~~8c. TUIC 缺少 udp_over_stream (uos) 字段~~ ✅ 已修复
- **NekoBox**: `uos` 标志，当 true 时使用 `udp_over_stream: true` 替代 `udp_relay_mode`（QUICBean.cpp:176-179）
- **Venlta**: ~~不支持~~ → 已添加
- **修复**:
  - `config_manager.py` `_tuic_outbound()` — uos 与 udp_relay_mode 互斥处理
  - `subscription.py` — sing-box JSON 解析器新增 `udp_over_stream` 读取

### ~~8d. TUIC 缺少 alpn 和 disableSni 支持~~ ✅ 已修复
- **NekoBox**: TUIC 的 TLS 支持 alpn（逗号分隔数组）和 disable_sni（QUICBean.cpp:114,109）
- **Venlta**: ~~不支持~~ → 已添加
- **修复**:
  - `config_manager.py` `_tuic_outbound()` — TLS 对象新增 alpn 和 disable_sni
  - `subscription.py` — 三种解析器（sing-box JSON/Clash YAML/代理链接）新增 alpn 和 disableSni 解析
  - `frontend/src/pages/nodes/index.tsx` — 新增 Disable SNI 开关和 ALPN 输入框

### ~~12a. Profile-Title 缺少 base64: 前缀解码~~ ✅ 已修复
- **NekoBox**: 递归解码 `base64:` 前缀的 Profile-Title（最多 33 次）（GroupUpdater.cpp:1186-1193）
- **Venlta**: ~~仅 URL 解码~~ → 已添加 base64: 前缀递归解码
- **修复**: `subscription.py` `_fetch_and_parse()` — 先尝试 base64: 递归解码，再尝试 URL 解码

### ~~19a. Adblock 规则集 URL 不一致~~ ✅ 已修复
- **NekoBox**: 使用 `217heidai/adblockfilters` 的 `adblocksingbox.srs`（ConfigBuilder.cpp:86-91）
- **Venlta**: ~~使用 `SagerNet/sing-geosite` 的 `geosite-category-ads-all.srs`~~ → 修正为 NekoBox 一致
- **修复**: `config_manager.py` `_build_route()` — URL 改为 `https://raw.githubusercontent.com/217heidai/adblockfilters/main/rules/adblocksingbox.srs`

### ~~17a. NTP 配置位置不正确~~ ✅ 已修复
- **NekoBox**: NTP 作为顶层 `config["ntp"]` 对象（ConfigBuilder.cpp:888-896）
- **Venlta**: ~~作为 outbound 类型插入 outbounds 数组~~ → 修正为顶层配置
- **修复**: `config_manager.py` `_build_config()` — NTP 从 outbounds 移至顶层 `config["ntp"]`

### ~~14a. HWID 缺少自定义参数覆盖~~ ✅ 已修复
- **NekoBox**: 支持 `sub_custom_hwid_params` 自定义设备标识（HTTPRequestHelper.cpp:91-128）
- **Venlta**: ~~仅使用自动检测值~~ → 已添加 custom_hwid_params 支持
- **修复**: `subscription.py` `_fetch_and_parse()` — 新增 `custom_hwid_params` 解析（格式: `hwid=xxx,os=xxx,osversion=xxx,model=xxx`）

### 保留差异（无需修复）

- **jsdelivr CDN**: Venlta 额外支持 `github.com/releases/download` URL 转换（NekoBox 仅转换 `raw.githubusercontent.com`），属于增强功能
- **Rule set local 类型**: Venlta 显式支持 local 类型（NekoBox 注释掉了缓存路径方案），属于增强功能
- **热键支持 (#15)**: 需要原生 QHotkey 库，Web 前端无法实现，正确跳过
- **QR 码扫描 (#16)**: 需要原生 quirc 库或摄像头 API，正确跳过
- **HWID per-group 配置**: NekoBox 支持按订阅组配置 HWID（GroupExtra::enable_hwid/custom_hwid），Venlta 仅支持全局 custom_hwid_params，功能覆盖主要场景

---

## 🟣 TUN 模式对照修复（2026-03-05）

基于对 NekoBox `BuildTunInbound()` / `BuildConfigSingBox()` / `DataStore.hpp` 的逐行对照，发现的 TUN 模式逻辑不一致及修复。

### ~~21. dns-in + hijack-dns 在 TUN 模式下自动添加（与 NekoBox 不一致）~~ ✅ 已修复
- **NekoBox**: `dns-in` 入站和 `hijack-dns` 路由规则仅在 `enable_dns_server` 启用时才添加（ConfigBuilder.cpp:905-924）。TUN 模式本身不添加这些，DNS 通过 DNS 规则 `{"inbound": "tun-in", "action": "route", "server": "dns-remote"}` 处理。
- **Venlta**: ~~TUN 模式下自动添加 dns-in (127.0.0.1:5353) 和 hijack-dns 路由规则~~
- **修复**: 移除 TUN 模式下自动添加的 dns-in 入站和 hijack-dns 路由规则。TUN 模式下 DNS 由 DNS 规则和 `protocol: dns` 路由规则处理，无需 dns-in。

### ~~22. sniff 规则包含 dns-in（与 NekoBox 不一致）~~ ✅ 已修复
- **NekoBox**: sniff 规则仅应用于 `mixed-in` 和 `tun-in`（ConfigBuilder.cpp:985-989），`dns-in` 仅在 `enable_dns_server` 启用时单独嗅探。
- **Venlta**: ~~TUN 模式下 sniff 规则包含 `dns-in`~~
- **修复**: sniff 规则仅包含 `mixed-in` 和 `tun-in`（TUN 模式），与 NekoBox 一致。

### ~~23. 缺少 enable_tun_routing 功能~~ ✅ 已修复
- **NekoBox**: `enable_tun_routing` 开关（DataStore.hpp:108）。启用时，自动将路由规则中直连目标的 IP CIDR 添加到 TUN 的 `route_exclude_address`，直连 rule_set 添加到 `route_exclude_address_set`（ConfigBuilder.cpp:730-739）。使直连流量在 OS 层面不走 TUN 设备，提高直连性能。
- **Venlta**: ~~无此功能~~
- **修复**:
  - `config_manager.py` — 新增 `_collect_direct_ips_from_rules()` 方法，从路由规则中收集直连目标的 IP CIDR 和 rule_set
  - `_build_config()` TUN inbound 构建时，若 `enable_tun_routing` 启用，将直连 IP/rule_set 添加到 `route_exclude_address`/`route_exclude_address_set`
  - `frontend/src/pages/settings/index.tsx` — TUN 设置区新增 TUN Direct Routing 开关
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 enable_tun_routing 翻译

### ~~24. 缺少 TunSplit 按进程路径分流~~ ✅ 已修复
- **NekoBox**: `TunSplit` 类支持三个进程路径列表：`proxy`（走代理）、`direct`（直连）、`block`（屏蔽）（DataStore.hpp:30-36）。TUN 模式下生成 `process_path` 路由规则（ConfigBuilder.cpp:1069-1084）。
- **Venlta**: ~~无此功能，仅有 IP 级别的 TUN 分流~~
- **修复**:
  - `config_manager.py` `_build_route()` — 新增 TunSplit 路由规则生成，支持 `tun_split_proxy`/`tun_split_direct`/`tun_split_block` 三个设置（每行一个进程路径）
  - `frontend/src/pages/settings/index.tsx` — TUN 设置区新增 Per-App Routing (TunSplit) 子区，三个 textarea 输入
  - `frontend/src/i18n/en.json` / `zh.json` — 新增 tun_split 相关翻译
  - `venlta_bridge.py` — config_affecting_keys 新增 enable_tun_routing/tun_split_proxy/tun_split_direct/tun_split_block

### ~~25. hijack-dns 路由规则的 inbound 不正确~~ ✅ 已修复
- **NekoBox**: hijack-dns 仅在 `enable_dns_server` 启用时添加，且 inbound 为 `["dns-in"]`（ConfigBuilder.cpp:916-919）。不启用 DNS server 时不添加。
- **Venlta**: ~~用户自定义 hijack-dns 规则的 inbound 在 TUN 模式下为 `["dns-in"]`，非 TUN 模式为 `["mixed-in"]`~~
- **修复**: 由于 dns-in 不再自动添加，hijack-dns 规则统一使用 `["mixed-in"]` 作为 inbound。

### TUN 已正确实现（与 NekoBox 一致）

- TUN 提权（Linux setcap/pkexec, Windows UAC, macOS osascript）
- TUN inbound 配置（address/auto_route/strict_route/mtu/stack）
- 持久化接口名（Venlta 改进：避免每次重启创建新 TUN 设备）
- gvisor strict_route 自动修复（Venlta 改进：gvisor 不支持 strict_route）
- route_exclude_address 默认排除私有 IP 段
- route_include_address TUN split routing
- TUN DNS 规则（inbound: tun-in → dns-remote）
- TUN sniff 规则（mixed-in + tun-in）
- TUN find_process 启用
- auto_detect_interface 始终启用
- TUN 与系统代理完全独立（与 NekoBox spmode_vpn / spmode_system_proxy 一致）

### ~~26. 系统代理与 TUN 独立性修复~~ ✅ 已修复
- **NekoBox**: `spmode_vpn` 和 `spmode_system_proxy` 是两个完全独立的布尔开关。两者可以同时开启。
- **Venlta**: ~~TUN 开启时自动开启系统代理，TUN 关闭时自动关闭系统代理~~ 已修复为完全独立
- **修复内容**:
  - `venlta_bridge.py` — `toggleTun()` 不再自动开启/关闭系统代理
  - `venlta_bridge.py` — 新增 `toggleSystemProxy()` 方法，独立控制系统代理
  - `singbox_manager.py` — `get_state()` 新增 `isSystemProxyEnabled` 字段
  - `frontend` — 仪表盘改为两张独立切换卡片（系统代理 + TUN），各有状态感知背景
  - **sing-box 生命周期管理（NekoBox 模型）**:
    - 任一模式开启 → sing-box 运行（如未运行则自动启动）
    - 两个模式都关闭 → sing-box 停止
    - `toggleSystemProxy(true)` + sing-box 未运行 → 自动启动 sing-box
    - `toggleTun(true)` + sing-box 未运行 → 自动启动 sing-box
    - `toggleSystemProxy(false)` + TUN 关闭 → 停止 sing-box
    - `toggleTun(false)` + 系统代理关闭 → 停止 sing-box
  - **托盘切换**: 改为切换系统代理（而非 start/stop sing-box），与 NekoBox `toggle_system_proxy()` 一致
  - **自动启动**: 启动时仅当 system_proxy_enabled 或 tun_enabled 为 true 时才自动启动 sing-box

### TUN 保留差异（无需修复）

- **接口名持久化**: Venlta 使用持久化接口名（保存到 DB），NekoBox 每次随机生成。Venlta 方案更优，避免旧 TUN 设备未清理。
- **auto_detect_interface**: Venlta 始终设为 True（NekoBox 仅 TUN 模式），在多网卡环境下有益。
- **find_process**: Venlta 仅 TUN 模式启用（NekoBox 基于 connection_statistics 设置），合理设计。
- **DNS server 功能**: NekoBox 有完整的 `enable_dns_server` 功能（本地 DNS 服务器、自定义响应 IP、LAN 监听），Venlta 暂未实现此独立功能。
