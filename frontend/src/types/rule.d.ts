// sing-box 路由规则 action 类型（参考 NekoBox RouteRule::ruleType）
// route: 路由到指定出站（默认行为，需配合 outboundTag 使用）
// reject: 拒绝连接（广告拦截等），可选 rejectMethod
// sniff: 协议嗅探（仅对入站流量生效）
// resolve: DNS 解析（指定 DNS 服务器解析域名）
// hijack-dns: DNS 劫持（将 DNS 查询劫持到 sing-box DNS 模块）
export type RuleAction = 'route' | 'reject' | 'sniff' | 'resolve' | 'hijack-dns';

// reject 方式（参考 NekoBox RouteRule::simpleAction / sing-box 文档）
// default: 默认拒绝（返回 RST 或 ICMP 不可达）
// conn-reset: TCP RST 重置连接
export type RejectMethod = 'default' | 'conn-reset';

export interface RuleType {
  id: string;
  name: string;
  outboundTag: string;
  action: RuleAction;
  rejectMethod?: RejectMethod;
  resolveServer?: string;  // resolve action 的 DNS 服务器 tag（如 dns-direct）
  domain?: string[];
  domainSuffix?: string[];
  domainKeyword?: string[];
  domainRegex?: string[];
  geosite?: string[];
  ipCidr?: string[];
  ipIsPrivate?: boolean;
  geoip?: string[];
  sourceIpCidr?: string[];
  sourceGeoip?: string[];
  port?: number | string;
  portRange?: string[];
  sourcePort?: string;
  sourcePortRange?: string[];
  processName?: string[];
  processPath?: string[];
  packageName?: string[];
  network?: string;
  protocol?: string;
  userId?: string[];
  clashMode?: string;
  invert?: boolean;
  ruleSetId?: string;
  isEnabled: boolean;
  sortOrder: number;
  createdAt?: string;
  updatedAt?: string;
  // 以下字段与后端 snake_case 对应，通过 DatabaseManager._convert_keys 转换
  // 后端字段: outbound_tag → outboundTag, is_enabled → isEnabled, sort_order → sortOrder 等
  // action → action (无需映射), reject_method → rejectMethod, resolve_server → resolveServer
}

export interface RuleSetType {
  id: string;
  name: string;
  tag: string;
  url: string;
  format: 'binary' | 'source';
  type: 'remote' | 'local';
  downloadDetour?: string;
  isEnabled: boolean;
  createdAt?: string;
  updatedAt?: string;
}
