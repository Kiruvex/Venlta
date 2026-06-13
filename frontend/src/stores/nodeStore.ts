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

// 测试操作的全局 loading 状态（跨组件共享，信号处理器可修改）
const isTestingLatency = signal(false);
const isTestingAllLatency = signal(false);
const isTestingSpeed = signal(false);
const updatingSubId = signal<string | null>(null);

// 异步测试完成追踪：记录待完成的节点数，信号回调递减，归零时清除 loading
const _pendingLatencyCount = signal(0);    // 延迟测试待完成节点数
const _pendingSpeedCount = signal(0);     // 速度测试待完成节点数
const _isAllLatency = signal(false);      // 标记当前延迟测试是否为"全部测试"

export const nodeStore = {
  nodes,
  groups,
  subscriptions,
  loading,
  isTestingLatency,
  isTestingAllLatency,
  isTestingSpeed,
  updatingSubId,

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

    // 递减待完成计数，归零时清除延迟测试 loading 状态
    _pendingLatencyCount.value = Math.max(0, _pendingLatencyCount.value - results.length);
    if (_pendingLatencyCount.value === 0) {
      // 根据 _isAllLatency 标记清除对应的 loading 信号
      if (_isAllLatency.value) {
        isTestingAllLatency.value = false;
        _isAllLatency.value = false;
      } else {
        isTestingLatency.value = false;
      }
    }
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

    // 递减待完成计数，归零时清除速度测试 loading 状态
    _pendingSpeedCount.value = Math.max(0, _pendingSpeedCount.value - results.length);
    if (_pendingSpeedCount.value === 0) {
      isTestingSpeed.value = false;
    }
  },

  // ---------- 测试操作 loading 控制 ----------

  /** 开始延迟测试（设置 loading 状态，记录待完成节点数） */
  startLatencyTest: (nodeCount: number, isAll: boolean = false) => {
    if (isAll) {
      isTestingAllLatency.value = true;
      _isAllLatency.value = true;
    } else {
      isTestingLatency.value = true;
    }
    _pendingLatencyCount.value = nodeCount;
    // 安全超时：60秒后强制清除 loading，防止信号丢失导致按钮永久禁用
    setTimeout(() => {
      if (_pendingLatencyCount.value > 0) {
        console.warn('[nodeStore] Latency test safety timeout, force-clearing loading state');
        _pendingLatencyCount.value = 0;
        isTestingLatency.value = false;
        isTestingAllLatency.value = false;
        _isAllLatency.value = false;
      }
    }, 60000);
  },

  /** 开始速度测试（设置 loading 状态，记录待完成节点数） */
  startSpeedTest: (nodeCount: number) => {
    isTestingSpeed.value = true;
    _pendingSpeedCount.value = nodeCount;
    // 安全超时：120秒后强制清除 loading（速度测试每个节点最多10秒）
    setTimeout(() => {
      if (_pendingSpeedCount.value > 0) {
        console.warn('[nodeStore] Speed test safety timeout, force-clearing loading state');
        _pendingSpeedCount.value = 0;
        isTestingSpeed.value = false;
      }
    }, 120000);
  },

  /** 设置订阅更新中状态 */
  startSubUpdate: (subId: string) => {
    updatingSubId.value = subId;
    // 安全超时：30秒后强制清除
    setTimeout(() => {
      if (updatingSubId.value === subId) {
        console.warn('[nodeStore] Subscription update safety timeout, force-clearing loading state');
        updatingSubId.value = null;
      }
    }, 30000);
  },

  /** 清除订阅更新中状态 */
  finishSubUpdate: () => {
    updatingSubId.value = null;
  },

  /** 强制清除所有延迟测试 loading 状态（bridge 调用失败时使用） */
  forceFinishLatencyTest: () => {
    _pendingLatencyCount.value = 0;
    isTestingLatency.value = false;
    isTestingAllLatency.value = false;
    _isAllLatency.value = false;
  },

  /** 强制清除速度测试 loading 状态（bridge 调用失败时使用） */
  forceFinishSpeedTest: () => {
    _pendingSpeedCount.value = 0;
    isTestingSpeed.value = false;
  },
};
