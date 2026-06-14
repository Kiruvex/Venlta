import { useEffect, useRef } from 'preact/hooks';
import { signal } from '@preact/signals';
import { useTranslation } from './i18n/useTranslation';
import { callBridge } from './lib/api';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Toast } from './components/Toast';
import { proxyStore } from './stores/proxyStore';
import { nodeStore } from './stores/nodeStore';
import { statsStore } from './stores/statsStore';
import { logStore } from './stores/logStore';
import { toastStore } from './stores/toastStore';
import { currentRoute, navigate } from './router';
import { DashboardPage } from './pages/dashboard';
import { NodesPage } from './pages/nodes';
import { RulesPage } from './pages/rules';
import { LogsPage } from './pages/logs';
import { SettingsPage } from './pages/settings';
import { i18next } from './i18n';
import { DashboardIcon, NodesIcon, RulesIcon, LogsIcon, SettingsIcon } from './lib/icons';

// Global version signal, populated from backend on init
export const appVersion = signal('');

interface SubscriptionUpdateResult {
  ok: boolean;
  node_count?: number;
  error?: string;
}

const NAV_ITEMS = [
  { key: 'dashboard', label: 'Dashboard', Icon: DashboardIcon },
  { key: 'nodes', label: 'Nodes', Icon: NodesIcon },
  { key: 'rules', label: 'Rules', Icon: RulesIcon },
  { key: 'logs', label: 'Logs', Icon: LogsIcon },
  { key: 'settings', label: 'Settings', Icon: SettingsIcon },
] as const;

type RouteName = 'dashboard' | 'nodes' | 'rules' | 'logs' | 'settings';
const PAGE_MAP: Record<RouteName, () => preact.VNode> = {
  dashboard: DashboardPage,
  nodes: NodesPage,
  rules: RulesPage,
  logs: LogsPage,
  settings: SettingsPage,
};

export function App() {
  const { t } = useTranslation();

  const registeredRef = useRef(false);
  useEffect(() => {
    if (!window.bridge || registeredRef.current) return;
    registeredRef.current = true;

    // Fetch app version from backend for sidebar display
    callBridge<{ app_version: string; singbox_version: string }>('getAppVersion').then(result => {
      if (result.ok && result.data?.app_version) {
        appVersion.value = result.data.app_version;
      }
    }).catch(e => console.warn('[getAppVersion]', e));

    // Check if sing-box core is installed, show notification if not
    callBridge<{ installed: boolean }>('isSingboxInstalled').then(result => {
      if (result.ok && result.data && !result.data.installed) {
        toastStore.warning(i18next.t('settings.singbox_not_installed'));
      }
    }).catch(() => {});

    // Determine language: prefer saved preference over system locale
    // so that frontend and tray stay in sync after user changes language
    callBridge<{ language: string }>('getSystemLanguage').then(sysResult => {
      const detectLang = (raw: string) => raw.startsWith('zh') ? 'zh' : 'en';
      if (sysResult.ok && sysResult.data?.language) {
        const sysLang = detectLang(sysResult.data.language);
        // First apply system language as fallback
        i18next.changeLanguage(sysLang);
      }
      // Then try to load saved language preference from settings
      callBridge<Record<string, any>>('getSettings').then(settingsResult => {
        if (settingsResult.ok && settingsResult.data?.language) {
          const savedLang = settingsResult.data.language;
          // Apply saved language to frontend
          i18next.changeLanguage(savedLang);
          // Sync backend/tray language with saved preference
          callBridge('setBackendLanguage', savedLang).catch(e => console.warn('[setBackendLanguage]', e));
        }
      }).catch(e => console.warn('[getSettings for language]', e));
    }).catch(e => console.warn('[getSystemLanguage]', e));

    (async () => {
      try {
        const result = await callBridge<any>('getProxyState');
        if (result.ok && result.data) {
          proxyStore.setState(result.data);
        }
      } catch (e) { console.warn('[getProxyState]', e); }
    })();

    const handleProxyState = (stateJson: string | null) => {
      try {
        if (!stateJson) return;
        const result = JSON.parse(stateJson);
        if (result.ok && result.data) {
          proxyStore.setState(result.data);
        }
      } catch (e) { console.warn('[onProxyStateChange]', e); }
    };

    const handleTraffic = (statsJson: string | null) => {
      try {
        if (!statsJson) return;
        const result = JSON.parse(statsJson);
        if (result.ok && result.data) {
          statsStore.updateTraffic(result.data);
        }
      } catch (e) { console.warn('[onTraffic]', e); }
    };

    // QWebChannel 信号连接：Qt Signal 可能传 null 或未定义参数，
    // 回调函数需做 null 安全检查，避免 'object null is not iterable' 错误
    try { window.bridge.proxyStateChanged?.connect(handleProxyState); } catch (e) { console.warn('[signal] proxyStateChanged connect failed:', e); }
    try { window.bridge.trafficStatsUpdated?.connect(handleTraffic); } catch (e) { console.warn('[signal] trafficStatsUpdated connect failed:', e); }

    const handleLatencyResult = (resultJson: string | null) => {
      try {
        if (!resultJson) return;
        const result = JSON.parse(resultJson);
        if (result.ok && result.data?.results) {
          nodeStore.updateLatencyResults(result.data.results);
        }
        // 注意：如果 result 不 ok 或没有 results，后端仍然会为每个批次发送信号，
        // 所以计数追踪应该正常。如果没有信号到达，安全超时会兜底。
      } catch (e) { console.warn('[onLatencyResult]', e); }
    };
    try { window.bridge.latencyResult?.connect(handleLatencyResult); } catch (e) { console.warn('[signal] latencyResult connect failed:', e); }

    const handleConnections = (connsJson: string | null) => {
      try {
        if (!connsJson) return;
        const result = JSON.parse(connsJson);
        if (result.ok && result.data) {
          statsStore.updateConnections(result.data);
        }
      } catch (e) { console.warn('[onConnectionsUpdated]', e); }
    };
    try { window.bridge.connectionsUpdated?.connect(handleConnections); } catch (e) { console.warn('[signal] connectionsUpdated connect failed:', e); }

    const handleLog = (logJson: string | null) => {
      try {
        if (!logJson) return;
        const result = JSON.parse(logJson);
        if (result.ok && result.data?.log) {
          const msg = result.data.log;
          const level = result.data.level || (msg.includes('[ERROR]') ? 'error' : msg.includes('[WARN]') ? 'warning' : msg.includes('[DEBUG]') ? 'debug' : 'info');
          logStore.addLog({ id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, time: new Date().toLocaleTimeString(), level, message: msg });
        }
      } catch (e) { console.warn('[onLogReceived]', e); }
    };
    try { window.bridge.logEmitted?.connect(handleLog); } catch (e) { console.warn('[signal] logEmitted connect failed:', e); }

    const handleSubUpdated = (resultJson: string | null) => {
      try {
        if (!resultJson) return;
        const result = JSON.parse(resultJson);
        if (result.ok && result.data) {
          nodeStore.fetchNodes();
          nodeStore.fetchSubscriptions();
          if (result.data.result?.ok) {
            toastStore.success(i18next.t('nodes.subscription_updated_with_count', { count: (result.data.result as SubscriptionUpdateResult).node_count }));
          } else {
            toastStore.error(result.data.result?.error ?? i18next.t('nodes.subscription_update_failed'));
          }
        }
        // 无论成功失败，订阅更新已完成，清除 loading 状态
        nodeStore.finishSubUpdate();
      } catch (e) { console.warn('[onSubscriptionUpdated]', e); nodeStore.finishSubUpdate(); }
    };
    try { window.bridge.subscriptionUpdated?.connect(handleSubUpdated); } catch (e) { console.warn('[signal] subscriptionUpdated connect failed:', e); }

    const handleSpeedResult = (resultJson: string | null) => {
      try {
        if (!resultJson) return;
        const result = JSON.parse(resultJson);
        if (result.ok && result.data?.results) {
          nodeStore.updateSpeedResults(result.data.results);
        }
      } catch (e) { console.warn('[onSpeedResult]', e); }
    };
    try { window.bridge.speedResult?.connect(handleSpeedResult); } catch (e) { console.warn('[signal] speedResult connect failed:', e); }

    const handleConnectionClosed = (resultJson: string | null) => {
      try {
        if (!resultJson) return;
        const result = JSON.parse(resultJson);
        if (result.ok && result.data && !result.data.ok) {
          toastStore.error(result.data.error ?? i18next.t('dashboard.close_connection_failed'));
        }
      } catch (e) { console.warn('[onConnectionClosed]', e); }
    };
    try { window.bridge.connectionClosed?.connect(handleConnectionClosed); } catch (e) { console.warn('[signal] connectionClosed connect failed:', e); }

    return () => {};
  }, []);

  const PageComponent = PAGE_MAP[currentRoute.value as RouteName] || DashboardPage;
  const isRunning = proxyStore.state.value.isRunning;

  return (
    <ErrorBoundary fallback={(error: Error) => (
      <div class="p-6 text-center bg-gray-50 dark:bg-gray-900 min-h-screen">
        <h2 class="text-xl font-bold text-red-500">{t('common.error_boundary_title')}</h2>
        <p class="mt-2 text-gray-600 dark:text-gray-400">{error.message}</p>
        <button class="mt-4 px-4 py-2 bg-green-600 text-white rounded-lg" onClick={() => location.reload()}>
          {t('common.reload')}
        </button>
      </div>
    )}>
      <div class="flex h-screen overflow-hidden">
        {/* 侧边栏 — 深色渐变 + 玻璃效果 */}
        <nav class="w-56 bg-gradient-sidebar shrink-0 flex flex-col relative overflow-hidden">
          {/* 装饰性背景光晕 */}
          <div class="absolute top-0 left-0 w-32 h-32 bg-green-500/10 rounded-full blur-3xl -translate-x-1/2 -translate-y-1/2" />
          <div class="absolute bottom-20 right-0 w-24 h-24 bg-emerald-400/5 rounded-full blur-2xl translate-x-1/2" />

          {/* Brand header */}
          <div class="px-5 py-6 relative z-10">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 rounded-lg overflow-hidden shadow-lg shadow-green-500/30">
                <svg viewBox="0 0 512 512" width="100%" height="100%">
                  <defs>
                    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                      <stop offset="0%" stop-color="#0F172A"/>
                      <stop offset="100%" stop-color="#1E293B"/>
                    </linearGradient>
                    <linearGradient id="planeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                      <stop offset="0%" stop-color="#34E89E"/>
                      <stop offset="100%" stop-color="#0FDBF6"/>
                    </linearGradient>
                  </defs>
                  <rect x="56" y="56" width="400" height="400" rx="96" fill="url(#bgGrad)" stroke="#334155" stroke-width="2"/>
                  <path d="M256,144 L144,368 L256,320 L368,368 Z" fill="url(#planeGrad)"/>
                  <line x1="256" y1="144" x2="256" y2="320" stroke="#FFFFFF" stroke-width="3" opacity="0.5" stroke-linecap="round"/>
                  <line x1="256" y1="320" x2="184" y2="360" stroke="#0FDBF6" stroke-width="4" opacity="0.6" stroke-linecap="round"/>
                  <line x1="256" y1="320" x2="328" y2="360" stroke="#34E89E" stroke-width="4" opacity="0.6" stroke-linecap="round"/>
                </svg>
              </div>
              <div>
                <h1 class="text-base font-bold text-white tracking-tight">Venlta</h1>
                <p class="text-[10px] text-gray-500 tracking-wide">sing-box client</p>
              </div>
            </div>
          </div>

          {/* Nav items */}
          <div class="flex-1 py-1 px-3 space-y-0.5 relative z-10">
            {NAV_ITEMS.map(({ key, Icon, label }) => {
              const isActive = currentRoute.value === key;
              return (
                <button
                  key={key}
                  class={`w-full px-3 py-2.5 rounded-lg text-left transition-all duration-200 flex items-center gap-3 group
                         ${isActive
                           ? 'bg-white/10 text-white font-medium shadow-sm'
                           : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'}`}
                  onClick={() => navigate(key)}
                  aria-label={label}
                >
                  <div class={`w-7 h-7 rounded-md flex items-center justify-center transition-all duration-200
                    ${isActive
                      ? 'bg-gradient-to-br from-green-400 to-emerald-600 shadow-sm shadow-green-500/30'
                      : 'bg-white/5 group-hover:bg-white/10'}`}>
                    <Icon class={`w-3.5 h-3.5 ${isActive ? 'text-white' : 'text-gray-400 group-hover:text-gray-300'}`} />
                  </div>
                  <span class="text-sm">{t(`nav.${key}`)}</span>
                  {isActive && <div class="ml-auto w-1.5 h-1.5 rounded-full bg-green-400 shadow-sm shadow-green-400/50" />}
                </button>
              );
            })}
          </div>

          {/* 代理状态指示 */}
          <div class="px-4 py-3 mx-3 mb-3 rounded-xl bg-white/5 border border-white/5 relative z-10">
            <div class="flex items-center gap-2">
              <span class={`w-2 h-2 rounded-full ${isRunning ? 'bg-green-400 shadow-sm shadow-green-400/50 animate-pulse-soft' : 'bg-gray-500'}`} />
              <span class="text-xs text-gray-400">{isRunning ? t('status.running') : t('status.stopped')}</span>
            </div>
          </div>

          {/* Version footer */}
          <div class="px-5 py-3 relative z-10">
            <p class="text-[10px] text-gray-600">{appVersion.value ? `v${appVersion.value} Alpha` : ''}</p>
          </div>
        </nav>

        {/* 内容区 */}
        <main class="flex-1 overflow-auto bg-gray-50/80 dark:bg-gray-900/80">
          <div class="page-enter">
            <PageComponent />
          </div>
        </main>
      </div>
      <Toast />
    </ErrorBoundary>
  );
}
