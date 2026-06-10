export interface SettingsType {
  // 设置项使用 snake_case 是有意为之的设计决策：app_settings 表采用扁平 key-value 存储，
  // key 字段直接作为属性名使用，无需额外的 key map 做字段名转换。
  // 如果使用 camelCase，则需要在 getSettings/setSettings 中维护双向映射表，
  // 对于扁平 key-value 存储来说，这种转换层是不必要的复杂度。
  // 因此 snake_case 与数据库 key 保持 1:1 映射，减少出错可能。
  socks_port: number;
  http_port: number;
  clash_api_port: number;
  tun_enabled: boolean;
  system_proxy_enabled: boolean;
  dns_server_1: string;
  dns_server_2: string;
  auto_update_enabled: boolean;
  app_version: string;
  log_level: string;
  encryption_degraded?: boolean;  // 运行时注入，非持久化字段
}
