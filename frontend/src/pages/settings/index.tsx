import { useSignal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import { useTranslation } from '../../i18n/useTranslation';
import { callBridge } from '../../lib/api';
import { uiStore } from '../../stores/uiStore';
import { toastStore } from '../../stores/toastStore';
import { Card } from '../../components/Card';
import { Button } from '../../components/Button';
import { Switch } from '../../components/Switch';
import { Input } from '../../components/Input';
import type { BridgeResult } from '../../lib/bridge-result';

type DownloadStage = 'idle' | 'downloading' | 'done' | 'error';

interface DownloadState {
  stage: DownloadStage;
  type: 'app' | 'core';
  path?: string;
  error?: string;
}

/** A single settings row */
function SettingRow({ label, description, children }: { label: string; description?: string; children: any }) {
  return (
    <div class="flex items-center justify-between py-3.5 gap-4">
      <div class="min-w-0">
        <span class="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
        {description && <p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{description}</p>}
      </div>
      <div class="w-56 shrink-0 flex justify-end">{children}</div>
    </div>
  );
}

/** Section card with save button */
function SettingSection({ title, icon, dirty, onSave, children }: { title: string; icon?: any; dirty: boolean; onSave: () => void; children: any }) {
  const { t } = useTranslation();
  return (
    <Card title={title}>
      <div class="space-y-0 divide-y divide-gray-100/80 dark:divide-gray-700/50">
        {children}
      </div>
      {dirty && (
        <div class="mt-4 pt-4 border-t border-gray-100 dark:border-gray-700/50 flex justify-end items-center animate-fade-in">
          <span class="mr-3 text-xs text-amber-500 flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse-soft" />
            {t('settings.unsaved')}
          </span>
          <Button variant="primary" size="sm" onClick={onSave}>{t('action.save')}</Button>
        </div>
      )}
    </Card>
  );
}

export function SettingsPage() {
  const { t, i18n } = useTranslation();
  const settings = useSignal<Record<string, any>>({});
  const localSettings = useSignal<Record<string, any>>({});
  const dirtySections = useSignal<Set<string>>(new Set());

  const appUpdateInfo = useSignal<Record<string, any> | null>(null);
  const coreUpdateInfo = useSignal<Record<string, any> | null>(null);
  const appDownloadState = useSignal<DownloadState>({ stage: 'idle', type: 'app' });
  const coreDownloadState = useSignal<DownloadState>({ stage: 'idle', type: 'core' });
  const coreUpdateAvailable = useSignal(false);

  const fetchSettings = async () => {
    const result = await callBridge<Record<string, any>>('getSettings');
    if (result.ok && result.data) {
      settings.value = result.data;
      localSettings.value = { ...result.data };
      dirtySections.value = new Set();
    }
  };

  useEffect(() => { fetchSettings(); }, []);

  // Auto-update notification on startup
  useEffect(() => {
    const bridge = (window as any).bridge;
    if (!bridge?.checkAndNotifyUpdates) return;
  }, []);

  useEffect(() => {
    const bridge = (window as any).bridge;
    if (!bridge?.downloadProgress?.connect) return;
    bridge.downloadProgress.connect((raw: string | null) => {
      try {
        if (!raw) return;
        const result: BridgeResult<any> = typeof raw === 'string' ? JSON.parse(raw) : raw;
        if (!result.ok) {
          const isApp = appDownloadState.value.type === 'app';
          if (isApp) appDownloadState.value = { stage: 'error', type: 'app', error: result.error?.message };
          else coreDownloadState.value = { stage: 'error', type: 'core', error: result.error?.message };
          toastStore.error(result.error?.message ?? t('settings.download_failed'));
          return;
        }
        const data = result.data;
        if (!data) return;
        const type = data.type as 'app' | 'core';
        const setState = type === 'app' ? appDownloadState : coreDownloadState;
        if (data.stage === 'downloading') setState.value = { stage: 'downloading', type };
        else if (data.stage === 'done') { setState.value = { stage: 'done', type, path: data.path }; toastStore.success(t('settings.download_complete')); }
        else if (data.stage === 'error') { setState.value = { stage: 'error', type, error: data.error }; toastStore.error(t('settings.download_failed')); }
      } catch (e) { console.warn('[downloadProgress] parse error', e); }
    });
  }, []);

  const markDirty = (section: string, key: string, value: any) => {
    localSettings.value = { ...localSettings.value, [key]: value };
    const newDirty = new Set(dirtySections.value);
    newDirty.add(section);
    dirtySections.value = newDirty;
  };

  const saveSection = async (section: string, keys: string[]) => {
    const updates: Record<string, any> = {};
    for (const key of keys) { if (key in localSettings.value) updates[key] = localSettings.value[key]; }
    const result = await callBridge('setSettings', JSON.stringify(updates));
    if (result.ok) {
      toastStore.success(t('action.save'));
      settings.value = { ...settings.value, ...updates };
      const newDirty = new Set(dirtySections.value);
      newDirty.delete(section);
      dirtySections.value = newDirty;
    } else { toastStore.error(result.error?.message ?? t('settings.save_failed')); }
  };

  const ls = localSettings.value;
  const s = settings.value;

  const proxyKeys = ['http_port', 'clash_api_port'];
  const dnsKeys = ['dns_server_1', 'dns_server_2'];
  const autoUpdateKeys = ['auto_update_enabled'];

  const selectClass = "w-full px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm focus:ring-2 focus:ring-green-500/30 focus:border-green-500 transition-all";

  return (
    <div class="p-6 space-y-5 max-w-3xl">
      <div>
        <h2 class="text-xl font-bold text-gray-900 dark:text-gray-100">{t('settings.title')}</h2>
        <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">Manage your preferences</p>
      </div>

      {/* General */}
      <SettingSection title={t('settings.general')} dirty={dirtySections.value.has('general')} onSave={async () => {
        const generalUpdates: Record<string, any> = {};
        if (ls.language !== s.language) {
          generalUpdates.language = ls.language;
          i18n.changeLanguage(ls.language);
          callBridge('setBackendLanguage', ls.language).catch(() => {});
        }
        if (ls.theme !== s.theme) {
          generalUpdates.theme = ls.theme;
          uiStore.setTheme(ls.theme);
        }
        if (Object.keys(generalUpdates).length > 0) {
          try {
            await callBridge('setSettings', JSON.stringify(generalUpdates));
          } catch (e) { console.warn('[saveGeneralSettings]', e); }
        }
        settings.value = { ...settings.value, language: ls.language, theme: ls.theme };
        const newDirty = new Set(dirtySections.value);
        newDirty.delete('general');
        dirtySections.value = newDirty;
        toastStore.success(t('action.save'));
      }}>
        <SettingRow label={t('settings.theme')} description={t('settings.theme_system')}>
          <select class={selectClass} value={ls.theme ?? uiStore.theme.value} onChange={(e: any) => markDirty('general', 'theme', e.target.value)}>
            <option value="light">{t('settings.theme_light')}</option>
            <option value="dark">{t('settings.theme_dark')}</option>
            <option value="system">{t('settings.theme_system')}</option>
          </select>
        </SettingRow>
        <SettingRow label={t('settings.language')}>
          <select class={selectClass} value={ls.language ?? i18n.language} onChange={(e: any) => markDirty('general', 'language', e.target.value)}>
            <option value="en">English</option>
            <option value="zh">中文</option>
          </select>
        </SettingRow>
      </SettingSection>

      {/* Proxy Settings */}
      <SettingSection title={t('settings.proxy')} dirty={dirtySections.value.has('proxy')} onSave={() => saveSection('proxy', proxyKeys)}>
        <SettingRow label={t('settings.http_port')} description={t('settings.http_port_desc')}>
          <Input value={String(ls.http_port ?? 10809)} onInput={(e: any) => { const v = parseInt(e.target.value); if (v > 0 && v <= 65535) markDirty('proxy', 'http_port', v); }} />
        </SettingRow>
        <SettingRow label={t('settings.clash_api_port')}>
          <Input value={String(ls.clash_api_port ?? 9090)} onInput={(e: any) => { const v = parseInt(e.target.value); if (v > 0 && v <= 65535) markDirty('proxy', 'clash_api_port', v); }} />
        </SettingRow>
      </SettingSection>

      {/* DNS Settings */}
      <SettingSection title={t('settings.dns')} dirty={dirtySections.value.has('dns')} onSave={() => saveSection('dns', dnsKeys)}>
        <SettingRow label={t('settings.dns_server_1')}>
          <Input value={String(ls.dns_server_1 ?? 'tls://8.8.8.8')} onInput={(e: any) => markDirty('dns', 'dns_server_1', e.target.value)} />
        </SettingRow>
        <SettingRow label={t('settings.dns_server_2')}>
          <Input value={String(ls.dns_server_2 ?? 'https://223.5.5.5/dns-query')} onInput={(e: any) => markDirty('dns', 'dns_server_2', e.target.value)} />
        </SettingRow>
      </SettingSection>

      {/* Port Check */}
      <Card title={t('settings.port_check')}>
        <div class="py-2 flex items-center justify-between">
          <span class="text-sm text-gray-600 dark:text-gray-400">{t('settings.port_check_desc')}</span>
          <Button variant="secondary" size="sm" onClick={async () => {
            try {
              const result = await callBridge('checkPortConflicts');
              if (result.ok && (result.data as any)?.conflicts?.length > 0) {
                toastStore.warning(t('settings.port_conflicts_detail', { ports: (result.data as any).conflicts.map((c: any) => c.port).join(', ') }));
              } else if (result.ok) { toastStore.success(t('settings.no_port_conflicts')); }
              else { toastStore.error(result.error?.message ?? t('settings.port_check_failed')); }
            } catch (e) { console.warn('[checkPortConflicts]', e); toastStore.error(t('settings.port_check_failed')); }
          }}>{t('settings.check_ports')}</Button>
        </div>
      </Card>

      {/* Auto Update */}
      <SettingSection title={t('settings.auto_update')} dirty={dirtySections.value.has('autoUpdate')} onSave={() => saveSection('autoUpdate', autoUpdateKeys)}>
        <SettingRow label={t('settings.auto_update')}>
          <Switch checked={!!ls.auto_update_enabled} onChange={(v) => markDirty('autoUpdate', 'auto_update_enabled', v)} />
        </SettingRow>
        {/* App update */}
        <div class="py-3.5 border-t border-gray-100/80 dark:border-gray-700/50">
          <div class="flex items-center justify-between">
            <div>
              <span class="text-sm font-medium text-gray-700 dark:text-gray-300">Venlta</span>
              <p class="text-xs text-gray-500 dark:text-gray-400">v{s.app_version ?? '0.0.0'}</p>
            </div>
            <div class="flex items-center gap-2">
              <Button variant="secondary" size="sm" disabled={appDownloadState.value.stage === 'downloading'} onClick={async () => {
                try {
                  const result = await callBridge<Record<string, any>>('checkUpdate');
                  if (result.ok && result.data) { appUpdateInfo.value = result.data; toastStore.info(t('settings.new_version_available', { version: result.data.version })); }
                  else if (result.ok) { appUpdateInfo.value = null; toastStore.info(t('settings.version_with_number', { version: s.app_version ?? '0.0.0' })); }
                  else { toastStore.error(result.error?.message ?? t('settings.check_update_failed')); }
                } catch (e) { console.warn('[checkUpdate]', e); toastStore.error(t('settings.check_update_failed')); }
              }}>{t('settings.check_update')}</Button>
              {appUpdateInfo.value && appDownloadState.value.stage === 'idle' && (
                <Button variant="primary" size="sm" onClick={async () => {
                  const result = await callBridge('downloadLatestUpdate');
                  if (!result.ok) toastStore.error(result.error?.message ?? t('settings.download_failed'));
                }}>{t('settings.download_update')}</Button>
              )}
              {appDownloadState.value.stage === 'downloading' && (
                <span class="text-xs text-gray-500 animate-pulse">{t('settings.downloading')}</span>
              )}
              {appDownloadState.value.stage === 'done' && (
                <Button variant="primary" size="sm" onClick={async () => {
                  const result = await callBridge('installAppUpdate', appDownloadState.value.path || '');
                  if (result.ok) { toastStore.success(t('settings.core_installed')); appDownloadState.value = { stage: 'idle', type: 'app' }; appUpdateInfo.value = null; }
                  else { toastStore.error(result.error?.message ?? t('settings.install_failed')); }
                }}>{t('settings.install_restart')}</Button>
              )}
              {appDownloadState.value.stage === 'error' && (
                <span class="text-xs text-red-500">{t('settings.download_failed')}: {appDownloadState.value.error}</span>
              )}
            </div>
          </div>
          {appUpdateInfo.value && appDownloadState.value.stage === 'idle' && (
            <p class="text-xs text-green-600 dark:text-green-400 mt-1 animate-fade-in">{t('settings.new_version_available', { version: appUpdateInfo.value.version })}</p>
          )}
        </div>
        {/* Core update */}
        <div class="py-3.5 border-t border-gray-100/80 dark:border-gray-700/50">
          <div class="flex items-center justify-between">
            <div>
              <span class="text-sm font-medium text-gray-700 dark:text-gray-300">sing-box</span>
              {coreUpdateAvailable.value && (
                <span class="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
              )}
              <p class="text-xs text-gray-500 dark:text-gray-400">{t('settings.singbox_version_with_number', { version: s.singbox_version ?? '0.0.0' })}</p>
            </div>
            <div class="flex items-center gap-2">
              <Button variant="secondary" size="sm" disabled={coreDownloadState.value.stage === 'downloading'} onClick={async () => {
                try {
                  const result = await callBridge<Record<string, any>>('checkCoreUpdate');
                  if (result.ok && result.data) { coreUpdateInfo.value = result.data; coreUpdateAvailable.value = true; toastStore.info(t('settings.new_core_version_available', { version: result.data.version })); }
                  else if (result.ok) { coreUpdateInfo.value = null; toastStore.info(t('settings.singbox_version_with_number', { version: t('settings.no_update') })); }
                  else { toastStore.error(result.error?.message ?? t('settings.check_update_failed')); }
                } catch (e) { console.warn('[checkCoreUpdate]', e); toastStore.error(t('settings.check_update_failed')); }
              }}>{t('settings.check_core_update')}</Button>
              {coreUpdateInfo.value && coreDownloadState.value.stage === 'idle' && (
                <Button variant="primary" size="sm" onClick={async () => {
                  const result = await callBridge('downloadLatestCoreUpdate');
                  if (!result.ok) toastStore.error(result.error?.message ?? t('settings.download_failed'));
                }}>{t('settings.download_core_update')}</Button>
              )}
              {coreDownloadState.value.stage === 'downloading' && (
                <span class="text-xs text-gray-500 animate-pulse">{t('settings.downloading_core')}</span>
              )}
              {coreDownloadState.value.stage === 'done' && (
                <Button variant="primary" size="sm" onClick={async () => {
                  const result = await callBridge('installCoreUpdate', coreDownloadState.value.path || '');
                  if (result.ok) { toastStore.success(t('settings.core_installed')); coreDownloadState.value = { stage: 'idle', type: 'core' }; coreUpdateInfo.value = null; coreUpdateAvailable.value = false; }
                  else { toastStore.error(result.error?.message ?? t('settings.install_failed')); }
                }}>{t('settings.install_restart')}</Button>
              )}
              {coreDownloadState.value.stage === 'error' && (
                <span class="text-xs text-red-500">{t('settings.download_failed')}: {coreDownloadState.value.error}</span>
              )}
            </div>
          </div>
          {coreUpdateInfo.value && coreDownloadState.value.stage === 'idle' && (
            <p class="text-xs text-green-600 dark:text-green-400 mt-1 animate-fade-in">{t('settings.new_core_version_available', { version: coreUpdateInfo.value.version })}</p>
          )}
        </div>
      </SettingSection>

      {/* About */}
      <Card>
        <div class="flex items-center gap-4">
          <div class="w-12 h-12 rounded-xl bg-gradient-to-br from-green-400 to-emerald-600 flex items-center justify-center shadow-md shadow-green-500/25">
            <svg class="w-6 h-6 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
          </div>
          <div>
            <h3 class="font-bold text-gray-900 dark:text-gray-100">Venlta</h3>
            <p class="text-xs text-gray-500 dark:text-gray-400">v{settings.value?.app_version ?? '0.0.0'} · sing-box proxy client</p>
          </div>
        </div>
      </Card>

      {/* Security warning */}
      {settings.value?.encryption_degraded && (
        <Card title={t('settings.security_warning')}>
          <div class="flex items-center gap-3 p-3 bg-amber-50 dark:bg-amber-900/20 rounded-xl border border-amber-200 dark:border-amber-800/30">
            <svg class="w-5 h-5 text-amber-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" /></svg>
            <p class="text-sm text-amber-700 dark:text-amber-300">{t('settings.encryption_degraded')}</p>
          </div>
        </Card>
      )}
    </div>
  );
}
