export interface RuleType {
  id: string;
  name: string;
  outboundTag: string;
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
