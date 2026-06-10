import { signal } from '@preact/signals';

export interface LogEntry { id: string; time: string; level: string; message: string };

let _logCounter = 0;

/** 生成唯一日志 ID（计数器 + 时间戳 + 哈希，避免同时间同消息的哈希冲突） */
function generateLogId(time: string, message: string): string {
  _logCounter++;
  let hash = 0;
  const str = time + message;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash |= 0; // 转为 32 位整数
  }
  return `log-${_logCounter}-${Math.abs(hash).toString(36)}`;
}

const MAX_LOG_ENTRIES = 500;
const logs = signal<LogEntry[]>([]);

export const logStore = {
  logs,

  addLog: (entry: LogEntry) => {
    // 为日志条目生成唯一 ID（如果调用方未提供）
    if (!entry.id) {
      entry = { ...entry, id: generateLogId(entry.time, entry.message) };
    }
    // 使用 splice 原地修改+重新赋值，避免每次创建完整新数组
    const current = logs.value;
    if (current.length >= MAX_LOG_ENTRIES) {
      // 超出上限时截取尾部并追加，避免无限增长
      const next = current.slice(-(MAX_LOG_ENTRIES - 1));
      next.push(entry);
      logs.value = next;
    } else {
      // 未超上限，追加并创建新数组引用以触发 Signal 更新
      logs.value = [...current, entry];
    }
  },

  clear: () => {
    logs.value = [];
  },
};
