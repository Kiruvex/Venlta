import { signal } from '@preact/signals';

export type ThemeMode = 'light' | 'dark' | 'system';

const theme = signal<ThemeMode>(
  (() => { try { return (localStorage.getItem('venlta-theme') as ThemeMode) || 'system'; } catch { return 'system'; } })()
);
const sidebarCollapsed = signal(false);

function applyTheme(mode: ThemeMode) {
  const root = document.documentElement;
  if (mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    root.classList.add('dark');
  } else {
    root.classList.remove('dark');
  }
}

// 初始化时应用主题
applyTheme(theme.value);

// 监听系统主题变化
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (theme.value === 'system') {
    applyTheme('system');
  }
});

export const uiStore = {
  theme,
  sidebarCollapsed,

  setTheme: (mode: ThemeMode) => {
    theme.value = mode;
    try { localStorage.setItem('venlta-theme', mode); } catch (e) { console.warn('Failed to save theme:', e); }
    applyTheme(mode);
  },

  toggleSidebar: () => {
    sidebarCollapsed.value = !sidebarCollapsed.value;
  },
};
