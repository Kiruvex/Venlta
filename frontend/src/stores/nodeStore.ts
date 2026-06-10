import { signal } from '@preact/signals';
import { callBridge } from '../lib/api';

export interface NodeItem {
  id: string;
  name: string;
  protocol: string;
  address: string;
  port: number;
  tag: string;
  groupId: string | null;
  subscriptionId: string | null;
  isEnabled: boolean;
  latency: number | null;
  speed?: number | null;  // 速度测试结果（字节/秒）
  lastTestAt?: string | null;  // 最近一次测试时间（ISO 字符串）
  sortOrder: number;
  config?: Record<string, any>;  // 协议特定配置
  createdAt?: string;
  updatedAt?: string;
};

export interface NodeGroup {
  id: string;
  name: string;
  sortOrder: number;
  createdAt?: string;
  updatedAt?: string;
};

export interface Subscription {
  id: string;
  name: string;
  url: string;
  // nodeCount 语义说明：
  //   null = 尚未从服务器加载（初始状态，页面打开后首次获取前）
  //   0    = 已加载，该订阅下无节点（数据库中 node_count = 0）
  // 区分两者很重要：null 时不应显示 "0 nodes"，而应显示加载占位符
  nodeCount: number | null;
  lastUpdate: string | null;
  autoUpdate: boolean;
  updateInterval: number;
  loading: boolean;  // 订阅是否正在更新中（从服务器拉取节点列表）
  createdAt?: string;
  updatedAt?: string;
};

const nodes = signal<NodeItem[]>([]);
const groups = signal<NodeGroup[]>([]);
const subscriptions = signal<Subscription[]>([]);
const loading = signal(false);

export const nodeStore = {
  nodes,
  groups,
  subscriptions,
  loading,

  fetchNodes: async () => {
    loading.value = true;
    try {
      const result = await callBridge<NodeItem[]>('listNodes');
      if (result.ok && result.data) {
        nodes.value = result.data;
      }
    } finally {
      loading.value = false;
    }
  },

  fetchGroups: async () => {
    const result = await callBridge<NodeGroup[]>('listNodeGroups');
    if (result.ok && result.data) {
      groups.value = result.data;
    }
  },

  addNode: async (nodeData: any) => {
    const result = await callBridge('addNode', JSON.stringify(nodeData));
    if (result.ok) {
      await nodeStore.fetchNodes();
    }
    return result;
  },

  deleteNode: async (nodeId: string) => {
    const result = await callBridge('deleteNode', nodeId);
    if (result.ok) {
      await nodeStore.fetchNodes();
    }
    return result;
  },

  addGroup: async (name: string) => {
    const result = await callBridge('addNodeGroup', JSON.stringify({ name }));
    if (result.ok) {
      await nodeStore.fetchGroups();
    }
    return result;
  },

  deleteGroup: async (groupId: string) => {
    const result = await callBridge('deleteNodeGroup', groupId);
    if (result.ok) {
      await nodeStore.fetchGroups();
      await nodeStore.fetchNodes();
    }
    return result;
  },

  fetchSubscriptions: async () => {
    const result = await callBridge<Subscription[]>('listSubscriptions');
    if (result.ok && result.data) {
      subscriptions.value = result.data;
    }
  },

  updateLatencyResults: (results: Array<{nodeId: string; latency: number; error?: string}>) => {
    // nodeId 实际为 Clash API 的 proxy tag（前端发送 tag 而非 UUID）
    // 批量持久化延迟结果：使用单个 Bridge 调用（batchUpdateNodeLatency）替代逐节点调用，
    // 避免大量节点时产生 N 个并发请求（100 节点 = 100 次 callBridge）
    const updates = results
      .filter(r => r.latency >= 0)
      .map(r => ({ tag: r.nodeId, latency: r.latency, lastTestAt: new Date().toISOString() }));
    if (updates.length > 0) {
      callBridge('batchUpdateNodeLatency', JSON.stringify(updates)).catch(e => console.warn('[persistLatency]', e));
    }
    // 使用 Map 优化 O(n*m) → O(n+m)，避免每次 node 都扫描整个 results 数组
    const resultMap = new Map(results.map(r => [r.nodeId, r]));
    const updated = nodes.value.map(node => {
      const r = resultMap.get(node.tag) || resultMap.get(node.id);
      if (r) {
        return { ...node, latency: r.latency >= 0 ? r.latency : null };
      }
      return node;
    });
    nodes.value = updated;
  },

  updateSpeedResults: (results: Array<{nodeId: string; speed: number; error?: string}>) => {
    // 带宽测试结果更新：每节点完成后由 speedResult 信号推送
    // 结果中 speed 为字节/秒，-1 表示测试失败
    // 使用 Map 优化 O(n*m) → O(n+m)
    const resultMap = new Map(results.map(r => [r.nodeId, r]));
    const updated = nodes.value.map(node => {
      const r = resultMap.get(node.tag) || resultMap.get(node.id);
      if (r) {
        return { ...node, speed: r.speed >= 0 ? r.speed : null };
      }
      return node;
    });
    nodes.value = updated;
  },
};
