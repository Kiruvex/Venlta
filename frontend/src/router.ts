import { signal } from '@preact/signals';

export const currentRoute = signal<string>('dashboard');

const VALID_ROUTES = ['dashboard', 'nodes', 'rules', 'logs', 'settings'];

function parseHash(): string {
  // 移除 # 前缀，然后按 ? 分离路由和查询参数
  // 注意：不能先按 & 分割，否则会丢失查询参数中的键值对
  // 使用 split('#') 替代 slice(1)，正确处理 URL 中包含多个 # 的情况（如 "##nodes"）
  // split('#') 会将 "#dashboard" 分割为 ["", "dashboard"]，取索引 1 即路由部分
  // 而 slice(1) 对 "##nodes" 会得到 "#nodes"（错误），split 方法则得到 "nodes"
  const hash = window.location.hash;
  const segments = hash.split('#');
  const routePart = (segments[1] || '').split('?')[0];  // 取第一个 # 后的路由部分，再按 ? 分离查询参数
  return VALID_ROUTES.includes(routePart) ? routePart : 'dashboard';
}

function updateRoute() {
  currentRoute.value = parseHash();
}

// 监听 hash 变化
if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', updateRoute);
  // 初始化
  if (!window.location.hash) {
    window.location.hash = '#dashboard';
  }
  updateRoute();
}

export function navigate(route: string) {
  if (!VALID_ROUTES.includes(route)) {
    console.warn(`Invalid route: ${route}, falling back to dashboard`);
    route = 'dashboard';
  }
  window.location.hash = `#${route}`;
}

export { VALID_ROUTES };
