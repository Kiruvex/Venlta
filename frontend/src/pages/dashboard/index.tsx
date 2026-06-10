import { useSignal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import { useTranslation } from '../../i18n/useTranslation';
import { proxyStore, type ProxyState } from '../../stores/proxyStore';
import { statsStore, chartData } from '../../stores/statsStore';
import { toastStore } from '../../stores/toastStore';
import { callBridge } from '../../lib/api';
import { formatBytes, formatRate } from '../../lib/format';
import { Card } from '../../components/Card';
import { TrafficChart } from '../../components/TrafficChart';

export function DashboardPage() {
  const { t } = useTranslation();
  const isTogglingProxy = useSignal(false);
  const isTogglingTun = useSignal(false);
  const enabledNodes = useSignal<Array<{tag: string; name: string}>>([]);
  // Local reactive proxy state — kept in sync with proxyStore via subscription
  // This ensures Preact re-renders when proxy state changes (reading
  // proxyStore.state.value into a local const breaks signal tracking)
  const proxyState = useSignal<ProxyState>(proxyStore.state.value);

  // TUN capability state
  const tunCapability = useSignal<{ canCreateTun: boolean; checked: boolean; granting: boolean }>({
    canCreateTun: false, checked: false, granting: false
  });

  // Subscribe to proxyStore changes and sync to local signal
  useEffect(() => {
    const fetchEnabledNodes = async () => {
      const result = await callBridge<any[]>('listNodes');
      if (result.ok && result.data) {
        enabledNodes.value = result.data
          .filter((n: any) => n.isEnabled)
          .map((n: any) => ({ tag: n.tag, name: n.name }));
      } else if (!result.ok) {
        toastStore.error(result.error?.message ?? t('common.error_start_proxy'));
      }
    };
    fetchEnabledNodes();

    const unsubscribe = proxyStore.subscribe((state) => {
      proxyState.value = state;
      // Refetch enabled nodes when proxy state changes (running → stopped or vice versa)
      fetchEnabledNodes();
    });
    return unsubscribe;
  }, []);

  // Fetch TUN capability
  useEffect(() => {
    const fetchData = async () => {
      const capResult = await callBridge<{ can_create_tun: boolean; platform: string; details: string }>('checkTunCapability');
      if (capResult.ok && capResult.data) {
        tunCapability.value = { canCreateTun: capResult.data.can_create_tun, checked: true, granting: false };
      }
    };
    fetchData();
  }, []);

  /** Toggle proxy on/off (starts or stops sing-box) */
  const handleToggleProxy = async () => {
    const currentState = proxyStore.state.value;
    if (currentState.isRunning) {
      // Stop proxy
      isTogglingProxy.value = true;
      try {
        const result = await callBridge('stopProxy');
        if (!result.ok) {
          toastStore.error(result.error?.message ?? t('common.error_stop_proxy'));
        } else {
          statsStore.reset();
        }
      } finally {
        isTogglingProxy.value = false;
      }
    } else {
      // Start proxy
      isTogglingProxy.value = true;
      try {
        const result = await callBridge('startProxy');
        if (!result.ok) {
          toastStore.error(result.error?.message ?? t('common.error_start_proxy'));
        }
      } finally {
        isTogglingProxy.value = false;
      }
    }
  };

  /** Toggle TUN on/off */
  const handleToggleTun = async () => {
    const currentState = proxyStore.state.value;
    if (currentState.isTunEnabled) {
      // Disable TUN
      isTogglingTun.value = true;
      try {
        const result = await callBridge('toggleTun', false);
        if (!result.ok) {
          toastStore.error(result.error?.message ?? t('dashboard.tun_failed'));
        }
      } finally {
        isTogglingTun.value = false;
      }
      return;
    }

    // Enable TUN - check capability first
    isTogglingTun.value = true;
    try {
      const capResult = await callBridge<{ can_create_tun: boolean }>('checkTunCapability');
      const canCreateTun = capResult.ok && capResult.data?.can_create_tun;

      if (canCreateTun) {
        const result = await callBridge('toggleTun', true);
        if (result.ok) {
          tunCapability.value = { ...tunCapability.value, canCreateTun: true };
        } else {
          toastStore.error(result.error?.message ?? t('dashboard.tun_failed'));
        }
        return;
      }

      // Missing capability - try to grant automatically
      tunCapability.value = { ...tunCapability.value, granting: true };
      const grantResult = await callBridge<{ already_has: boolean }>('grantTunCapability');
      tunCapability.value = { ...tunCapability.value, granting: false };

      if (grantResult.ok) {
        tunCapability.value = { canCreateTun: true, checked: true, granting: false };
        const result = await callBridge('toggleTun', true);
        if (result.ok) {
          toastStore.success(t('settings.tun_capability_granted'));
        } else {
          toastStore.error(result.error?.message ?? t('dashboard.tun_failed'));
        }
      } else {
        tunCapability.value = { canCreateTun: false, checked: true, granting: false };
        toastStore.error(grantResult.error?.message ?? t('settings.tun_capability_grant_failed'));
      }
    } finally {
      isTogglingTun.value = false;
    }
  };

  // Read from reactive local signals for rendering
  const state = proxyState.value;
  const stats = statsStore.state.value;

  return (
    <div class="p-6 space-y-5">
      {/* Hero status card */}
      <div class={`relative rounded-2xl overflow-hidden transition-all duration-500 ${
        state.isRunning
          ? 'bg-gradient-to-br from-green-500 via-emerald-500 to-teal-600 shadow-lg shadow-green-500/20'
          : 'bg-gradient-to-br from-gray-400 via-gray-500 to-slate-600 shadow-lg shadow-gray-500/10'
      }`}>
        {/* Decorative glows */}
        <div class="absolute top-0 right-0 w-64 h-64 bg-white/5 rounded-full blur-3xl -translate-y-1/2 translate-x-1/4" />
        <div class="absolute bottom-0 left-0 w-40 h-40 bg-black/5 rounded-full blur-2xl translate-y-1/2 -translate-x-1/4" />

        <div class="relative z-10 p-6">
          <div class="flex items-start justify-between">
            <div class="space-y-3">
              <div class="flex items-center gap-3">
                <div class={`w-3 h-3 rounded-full ${state.isRunning ? 'bg-white shadow-sm animate-pulse-soft' : 'bg-white/50'}`} />
                <span class="text-white/80 text-sm font-medium uppercase tracking-wider">
                  {t('dashboard.proxy_status')}
                </span>
              </div>
              <h2 class={`text-3xl font-bold ${state.isRunning ? 'text-white' : 'text-white/80'}`}>
                {state.isRunning ? t('status.running') : t('status.stopped')}
              </h2>
              <div class="flex items-center gap-4 text-white/70 text-sm">
                <span class="flex items-center gap-1.5">
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>
                  {t('dashboard.mode')}: <strong class="text-white/90">{state.currentMode}</strong>
                </span>
                {state.currentNode && (
                  <span class="flex items-center gap-1.5">
                    <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></svg>
                    <strong class="text-white/90 truncate max-w-32">{state.currentNode}</strong>
                  </span>
                )}
              </div>
            </div>
            {/* Primary action buttons: Start/Stop Proxy + Start/Stop TUN */}
            <div class="flex items-center gap-2">
              <button
                class={`px-4 py-2.5 rounded-xl text-sm font-semibold transition-all duration-200 btn-press disabled:opacity-40 ${
                  state.isRunning
                    ? 'bg-white/15 hover:bg-white/25 text-white backdrop-blur-sm border border-white/10'
                    : 'bg-white text-green-600 hover:bg-white/90 shadow-sm'
                }`}
                onClick={handleToggleProxy}
                disabled={isTogglingProxy.value}
              >
                {isTogglingProxy.value
                  ? t('common.loading')
                  : state.isRunning
                    ? t('dashboard.stop_proxy')
                    : t('dashboard.start_proxy')
                }
              </button>
              <button
                class={`px-4 py-2.5 rounded-xl text-sm font-semibold transition-all duration-200 btn-press disabled:opacity-40 ${
                  state.isTunEnabled
                    ? 'bg-white/15 hover:bg-white/25 text-white backdrop-blur-sm border border-white/10'
                    : 'bg-white/10 hover:bg-white/20 text-white backdrop-blur-sm border border-white/15'
                }`}
                onClick={handleToggleTun}
                disabled={isTogglingTun.value || tunCapability.value.granting}
              >
                {isTogglingTun.value || tunCapability.value.granting
                  ? t('common.loading')
                  : state.isTunEnabled
                    ? t('dashboard.stop_tun')
                    : t('dashboard.start_tun')
                }
              </button>
            </div>
          </div>

          {/* Mode badges row */}
          <div class="mt-4 pt-4 border-t border-white/15">
            <div class="flex items-center gap-2">
              {state.isTunEnabled && (
                <span class="px-2.5 py-1 rounded-full bg-white/15 text-white/90 text-xs font-semibold border border-white/10">TUN</span>
              )}
              {state.isRunning && !state.isTunEnabled && (
                <span class="px-2.5 py-1 rounded-full bg-white/15 text-white/90 text-xs font-semibold border border-white/10">{t('dashboard.system_proxy')}</span>
              )}
            </div>
            {/* TUN capability warning */}
            {state.isTunEnabled && tunCapability.value.checked && !tunCapability.value.canCreateTun && (
              <div class="mt-3 flex items-center gap-3 px-3 py-2.5 bg-amber-500/20 rounded-lg border border-amber-400/20">
                <svg class="w-4 h-4 text-amber-300 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>
                <p class="text-sm text-amber-200 flex-1">{t('dashboard.tun_no_capability')}</p>
                <button
                  class="px-2.5 py-1 text-xs font-medium rounded-md bg-amber-400/30 hover:bg-amber-400/40 text-amber-100 border border-amber-400/20 transition-colors disabled:opacity-50"
                  disabled={tunCapability.value.granting}
                  onClick={async () => {
                    tunCapability.value = { ...tunCapability.value, granting: true };
                    const grantResult = await callBridge<{ already_has: boolean }>('grantTunCapability');
                    tunCapability.value = { ...tunCapability.value, granting: false };
                    if (grantResult.ok) {
                      tunCapability.value = { canCreateTun: true, checked: true, granting: false };
                      toastStore.success(t('settings.tun_capability_granted'));
                    } else {
                      toastStore.error(grantResult.error?.message ?? t('settings.tun_capability_grant_failed'));
                    }
                  }}
                >
                  {t('dashboard.tun_grant_capability')}
                </button>
              </div>
            )}
          </div>

          {/* Node switching (when running) */}
          {state.isRunning && enabledNodes.value.length > 0 && (
            <div class="mt-4 pt-4 border-t border-white/15 flex items-center gap-3">
              <span class="text-white/60 text-sm">{t('dashboard.switch_node')}:</span>
              <select class="px-3 py-1.5 rounded-lg bg-white/15 border border-white/20 text-white text-sm backdrop-blur-sm focus:outline-none focus:ring-2 focus:ring-white/30 appearance-none cursor-pointer" value={state.currentNode ?? ''} onChange={async (e: any) => {
                const groupTag = proxyStore.state.value.currentSelectorTag ?? 'proxy';
                const result = await callBridge('switchNode', groupTag, e.target.value);
                if (!result.ok) toastStore.error(result.error?.message ?? t('dashboard.switch_failed'));
              }}>
                {enabledNodes.value.map(n => <option key={n.tag} value={n.tag} style="background:#1e293b;color:#f1f5f9">{n.name}</option>)}
              </select>
              <select class="px-3 py-1.5 rounded-lg bg-white/15 border border-white/20 text-white text-sm backdrop-blur-sm focus:outline-none focus:ring-2 focus:ring-white/30 appearance-none cursor-pointer" value={state.currentMode} onChange={async (e: any) => {
                const newMode = e.target.value;
                const result = await callBridge('switchMode', newMode);
                if (!result.ok) toastStore.error(result.error?.message ?? t('dashboard.switch_failed'));
              }}>
                <option value="route" style="background:#1e293b;color:#f1f5f9">{t('status.mode_route')}</option>
                <option value="global" style="background:#1e293b;color:#f1f5f9">{t('status.mode_global')}</option>
                <option value="direct" style="background:#1e293b;color:#f1f5f9">{t('status.mode_direct')}</option>
              </select>
            </div>
          )}

          {state.restartCount > 0 && (
            <div class="mt-3 px-3 py-2 bg-yellow-500/20 rounded-lg text-sm text-yellow-100 border border-yellow-400/20">
              {t('dashboard.crash_count', { count: state.restartCount })}{state.lastCrashTime ? ` · ${state.lastCrashTime}` : ''}
            </div>
          )}
        </div>
      </div>

      {/* Stats cards */}
      <div class="grid grid-cols-3 gap-4">
        <div class="stat-card rounded-xl bg-gradient-stats-upload p-5 text-white shadow-md shadow-blue-500/15 card-hover">
          <div class="flex items-center justify-between mb-3">
            <p class="text-xs font-medium uppercase tracking-wider text-blue-100">{t('dashboard.total_upload')}</p>
            <svg class="w-5 h-5 text-blue-200/60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
          </div>
          <p class="text-2xl font-bold tracking-tight">{formatBytes(stats.totalUpload)}</p>
          <p class="text-xs text-blue-200/80 mt-1">{formatRate(stats.uploadRate)}</p>
        </div>
        <div class="stat-card rounded-xl bg-gradient-stats-download p-5 text-white shadow-md shadow-green-500/15 card-hover">
          <div class="flex items-center justify-between mb-3">
            <p class="text-xs font-medium uppercase tracking-wider text-green-100">{t('dashboard.total_download')}</p>
            <svg class="w-5 h-5 text-green-200/60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12l7 7 7-7" /></svg>
          </div>
          <p class="text-2xl font-bold tracking-tight">{formatBytes(stats.totalDownload)}</p>
          <p class="text-xs text-green-200/80 mt-1">{formatRate(stats.downloadRate)}</p>
        </div>
        <div class="stat-card rounded-xl bg-gradient-stats-conn p-5 text-white shadow-md shadow-purple-500/15 card-hover">
          <div class="flex items-center justify-between mb-3">
            <p class="text-xs font-medium uppercase tracking-wider text-purple-100">{t('dashboard.connections')}</p>
            <svg class="w-5 h-5 text-purple-200/60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></svg>
          </div>
          <p class="text-2xl font-bold tracking-tight">{stats.connectionCount}</p>
          <p class="text-xs text-purple-200/80 mt-1">{t('dashboard.active_connections')}</p>
        </div>
      </div>

      {/* Real-time traffic chart */}
      <Card title={t('dashboard.traffic')}>
        <TrafficChart data={chartData.value} height={160} uploadLabel={t('dashboard.upload')} downloadLabel={t('dashboard.download')} />
      </Card>

      {/* Active connections */}
      <Card title={t('dashboard.active_connections')}>
        <div class="space-y-1">
          {statsStore.connections.value.length === 0 ? (
            <div class="text-center py-8">
              <svg class="w-10 h-10 mx-auto text-gray-300 dark:text-gray-600 mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" /></svg>
              <p class="text-gray-400 dark:text-gray-500 text-sm">{t('dashboard.no_active_connections')}</p>
            </div>
          ) : (
            <div class="max-h-48 overflow-y-auto space-y-0.5 scrollbar-thin">
              {statsStore.connections.value.slice(0, 20).map((conn: any) => (
                <div key={conn.id} class="flex items-center justify-between px-3 py-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 text-sm group transition-colors">
                  <div class="flex-1 min-w-0 flex items-center gap-2">
                    <span class="font-mono text-xs text-gray-700 dark:text-gray-300 truncate">{conn.metadata?.host || conn.metadata?.destinationIP}:{conn.metadata?.destinationPort}</span>
                    <span class="inline-block px-1.5 py-0.5 text-[10px] rounded-md bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 font-medium uppercase">{conn.metadata?.network}</span>
                  </div>
                  <button class="text-xs text-gray-300 hover:text-red-500 dark:text-gray-600 dark:hover:text-red-400 ml-2 shrink-0 opacity-0 group-hover:opacity-100 transition-all btn-press" onClick={async () => {
                    const result = await callBridge('closeConnection', conn.id);
                    if (!result.ok) toastStore.error(result.error?.message ?? t('dashboard.close_connection_failed'));
                  }}>
                    <svg class="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" /></svg>
                  </button>
                </div>
              ))}
              {statsStore.connections.value.length > 20 && (
                <p class="text-xs text-gray-400 text-center py-1">{t('dashboard.more_connections', { count: statsStore.connections.value.length - 20 })}</p>
              )}
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
