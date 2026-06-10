import { signal } from '@preact/signals';

export interface ToastItem {
  id: number;
  type: 'success' | 'error' | 'info' | 'warning';
  message: string;
};

// 使用 IIFE 闭包封装 nextId，避免模块级 let 变量被外部意外修改
const _createToastStore = () => {
  let nextId = 0;  // Toast 通知自增 ID，闭包内私有，单次会话内递增，不跨会话持久化，溢出风险可忽略
  const toasts = signal<ToastItem[]>([]);

  return {
    toasts,

    success(message: string) {
      toasts.value = [...toasts.value, { id: nextId++, type: 'success', message }];
    },

    error(message: string) {
      toasts.value = [...toasts.value, { id: nextId++, type: 'error', message }];
    },

    info(message: string) {
      toasts.value = [...toasts.value, { id: nextId++, type: 'info', message }];
    },

    warning(message: string) {
      toasts.value = [...toasts.value, { id: nextId++, type: 'warning', message }];
    },

    dismiss(id: number) {
      // 使用索引+splice避免filter创建新数组，高频场景性能更好
      const idx = toasts.value.findIndex(t => t.id === id);
      if (idx !== -1) {
        const next = [...toasts.value];
        next.splice(idx, 1);
        toasts.value = next;
      }
    },
  };
};

export const toastStore = _createToastStore();
