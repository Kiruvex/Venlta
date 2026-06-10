import { signal, computed } from '@preact/signals';
import { shallowEqual } from '../lib/shallow-equal';

export interface ConnectionInfo {
  id: string;
  metadata?: {
    host?: string;
    destinationIP?: string;
    destinationPort?: number;
    network?: string;
    sourceIP?: string;
    sourcePort?: number;
    type?: string;
  };
  start?: string;
  chains?: string[];
  rule?: string;
  rulePayload?: string;
  upload?: number;
  download?: number;
};

export interface StatsState {
  uploadRate: number;       // bytes/s
  downloadRate: number;     // bytes/s
  totalUpload: number;      // bytes
  totalDownload: number;    // bytes
  connectionCount: number;
};

const initialState: StatsState = {
  uploadRate: 0,
  downloadRate: 0,
  totalUpload: 0,
  totalDownload: 0,
  connectionCount: 0,
};

const state = signal<StatsState>(initialState);

// 活跃连接列表（单独 signal，避免与流量统计耦合导致不必要的重渲染）
const connections = signal<ConnectionInfo[]>([]);

// 流量历史数据（用于图表）
const MAX_HISTORY_POINTS = 60;
const historyUpload = signal<number[]>([]);
const historyDownload = signal<number[]>([]);
const historyTimestamps = signal<number[]>([]);

export const statsStore = {
  state,
  connections,

  updateTraffic: (data: { uploadRate?: number; downloadRate?: number; totalUpload?: number; totalDownload?: number }) => {
    const next = { ...state.value };
    if (data.uploadRate !== undefined) next.uploadRate = data.uploadRate;
    if (data.downloadRate !== undefined) next.downloadRate = data.downloadRate;
    // totalUpload/totalDownload are absolute values from sing-box, NOT increments
    if (data.totalUpload !== undefined) next.totalUpload = data.totalUpload;
    if (data.totalDownload !== undefined) next.totalDownload = data.totalDownload;
    if (!shallowEqual(next, state.value)) {
      state.value = next;
    }
    // 更新历史数据（用于流量图表）
    // 批量更新三个历史数组，避免三次独立 signal 赋值导致 TrafficChart 多次重渲染
    // 条件：任一速率字段有值即更新（不仅依赖 uploadRate，否则仅有 downloadRate 时图表不更新）
    if (data.uploadRate !== undefined || data.downloadRate !== undefined) {
      const now = Date.now() / 1000;
      const newUpload = [...historyUpload.value, data.uploadRate ?? 0].slice(-MAX_HISTORY_POINTS);
      const newDownload = [...historyDownload.value, data.downloadRate ?? 0].slice(-MAX_HISTORY_POINTS);
      const newTimestamps = [...historyTimestamps.value, now].slice(-MAX_HISTORY_POINTS);
      // 使用 batch 更新或直接同步赋值，减少中间状态触发
      historyUpload.value = newUpload;
      historyDownload.value = newDownload;
      historyTimestamps.value = newTimestamps;
    }
  },

  updateConnections: (data: { count?: number; connections?: ConnectionInfo[] }) => {
    const next = { ...state.value, connectionCount: data.count ?? (data.connections?.length ?? 0) };
    if (!shallowEqual(next, state.value)) {
      state.value = next;
    }
    if (data.connections !== undefined) {
      connections.value = data.connections;
    }
  },

  reset: () => {
    state.value = initialState;
    historyUpload.value = [];
    historyDownload.value = [];
    historyTimestamps.value = [];
    connections.value = [];
  },
};

// 计算属性：图表数据
export const chartData = computed(() => ({
  upload: historyUpload.value,
  download: historyDownload.value,
  timestamps: historyTimestamps.value,
}));
