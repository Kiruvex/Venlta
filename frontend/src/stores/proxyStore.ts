import { signal, computed, effect } from '@preact/signals';
import { shallowEqual } from '../lib/shallow-equal';

export interface ProxyState {
  isRunning: boolean;
  currentMode: 'route' | 'global' | 'direct';
  isTunEnabled: boolean;
  currentNode: string | null;
  currentSelectorTag: string | null;  // Clash API selector group tag（如 "proxy"），用于 switchNode
  restartCount: number;
  lastCrashTime: string | null;
};

const initialState: ProxyState = {
  isRunning: false,
  currentMode: 'route',
  isTunEnabled: false,
  currentNode: null,
  currentSelectorTag: 'proxy',
  restartCount: 0,
  lastCrashTime: null,
};

const state = signal<ProxyState>(initialState);

// 计算属性
const isRunning = computed(() => state.value.isRunning);

// 监听器管理（支持多个独立 subscribe/unsubscribe）
type Listener = (state: ProxyState) => void;
const listenerSet = new Set<Listener>();

// 自动追踪 Signal 变化并通知监听器（带 shallowEqual 去重）
// 关键：与上一次的值比较，而非与 initialState 比较
// 注意：首次执行时 prevState === null，会触发所有 listener 回调
// 如果订阅者不期望立即收到初始值，应在 subscribe 时自行过滤
// 注意：prevState 是模块级变量，在 HMR（热模块替换）时可能导致陈旧状态。
// 生产环境无影响（无 HMR），开发环境可通过重置机制（如 proxyStore.reset()）缓解
let prevState: ProxyState | null = null;
effect(() => {
  const current = state.value;
  // 首次执行或状态真正变化时才通知监听器
  if (prevState === null || !shallowEqual(current, prevState)) {
    prevState = current;
    listenerSet.forEach(fn => fn(current));
  }
});

export const proxyStore = {
  state,
  isRunning,

  setState: (partial: Partial<ProxyState>) => {
    const next = { ...state.value, ...partial };
    // 注意：此处 shallowEqual 检查与下方 effect 中的检查形成双重去重
    // setState 中的检查避免 signal 赋值触发，effect 中的检查避免监听器回调
    // 两层检查各有用途：state 赋值会触发 effect，effect 再过滤监听器
    if (!shallowEqual(next, state.value)) {
      state.value = next;
    }
  },

  subscribe: (fn: Listener): (() => void) => {
    listenerSet.add(fn);
    return () => { listenerSet.delete(fn); };
  },

  reset: () => {
    state.value = initialState;
    prevState = null;  // 重置 prevState，确保 HMR 后 effect 能正常触发
  },
};
