export interface NodeType {
  id: string;
  name: string;
  protocol: 'vmess' | 'vless' | 'trojan' | 'shadowsocks' | 'hysteria2' | 'wireguard';
  address: string;
  port: number;
  tag: string;
  groupId: string | null;
  subscriptionId: string | null;
  isEnabled: boolean;
  latency: number | null;
  speed?: number | null;
  lastTestAt?: string | null;
  sortOrder: number;
  config?: Record<string, any>;
  createdAt?: string;
  updatedAt?: string;
}
