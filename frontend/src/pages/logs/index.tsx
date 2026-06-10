import { useSignal, computed, useSignalEffect } from '@preact/signals';
import { useEffect, useRef, useState } from 'preact/hooks';
import { useTranslation } from '../../i18n/useTranslation';
import { Button } from '../../components/Button';
import { Card } from '../../components/Card';
import { logStore } from '../../stores/logStore';

const LEVEL_CONFIG: Record<string, { bg: string; text: string; icon: string }> = {
  info: { bg: 'bg-sky-50 dark:bg-sky-900/20', text: 'text-sky-600 dark:text-sky-400', icon: 'i' },
  warning: { bg: 'bg-amber-50 dark:bg-amber-900/20', text: 'text-amber-600 dark:text-amber-400', icon: '!' },
  error: { bg: 'bg-red-50 dark:bg-red-900/20', text: 'text-red-600 dark:text-red-400', icon: 'x' },
  debug: { bg: 'bg-gray-50 dark:bg-gray-800/50', text: 'text-gray-500 dark:text-gray-400', icon: '·' },
};

export function LogsPage() {
  const { t } = useTranslation();
  const levelFilter = useSignal('all');
  const autoScroll = useSignal(true);
  const containerRef = useRef<HTMLDivElement>(null);

  const [filteredLogs] = useState(() =>
    computed(() => {
      const logs = logStore.logs.value;
      return levelFilter.value === 'all' ? logs : logs.filter(l => l.level === levelFilter.value);
    })
  );

  useSignalEffect(() => {
    const len = filteredLogs.value.length;
    if (autoScroll.value && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  });

  return (
    <div class="p-6 space-y-5">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-xl font-bold text-gray-900 dark:text-gray-100">{t('logs.title')}</h2>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{filteredLogs.value.length} entries</p>
        </div>
        <div class="flex gap-2 items-center">
          <label class="flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-400 cursor-pointer select-none">
            <input type="checkbox" checked={autoScroll.value} onChange={(e: any) => { autoScroll.value = e.target.checked; }} class="rounded border-gray-300 text-green-600 focus:ring-green-500 cursor-pointer" />
            {t('logs.auto_scroll')}
          </label>
          <div class="flex rounded-lg bg-gray-100 dark:bg-gray-700/50 p-0.5 gap-0.5">
            {['all', 'info', 'warning', 'error', 'debug'].map(level => {
              const config = LEVEL_CONFIG[level] || LEVEL_CONFIG.info;
              return (
                <button key={level} class={`px-3 py-1.5 text-xs font-medium rounded-md transition-all duration-150
                  ${levelFilter.value === level
                    ? 'bg-white dark:bg-gray-600 text-gray-900 dark:text-white shadow-sm'
                    : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'}`}
                  onClick={() => { levelFilter.value = level; }}>
                  {t(`logs.${level}`)}
                </button>
              );
            })}
          </div>
          <Button variant="ghost" size="sm" onClick={() => { logStore.clear(); }}>
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
            {t('logs.clear')}
          </Button>
        </div>
      </div>
      <Card>
        <div ref={containerRef} class="max-h-[32rem] overflow-y-auto font-mono text-xs space-y-0.5 scrollbar-thin">
          {filteredLogs.value.length === 0 ? (
            <div class="text-center py-12">
              <svg class="w-10 h-10 mx-auto text-gray-300 dark:text-gray-600 mb-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>
              <p class="text-gray-400 dark:text-gray-500 text-sm">{t('logs.title')}</p>
            </div>
          ) : (
            filteredLogs.value.map((log) => {
              const config = LEVEL_CONFIG[log.level as keyof typeof LEVEL_CONFIG] || LEVEL_CONFIG.info;
              return (
                <div key={log.id} class="flex items-start gap-2 px-2.5 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors group">
                  <span class="text-gray-400 dark:text-gray-500 shrink-0 text-[10px] pt-0.5">{log.time}</span>
                  <span class={`inline-flex items-center justify-center w-5 h-5 rounded-md text-[10px] font-bold uppercase shrink-0 ${config.bg} ${config.text}`}>
                    {config.icon}
                  </span>
                  <span class={`break-all flex-1 ${log.level === 'error' ? 'text-red-700 dark:text-red-300' : log.level === 'warning' ? 'text-amber-700 dark:text-amber-300' : 'text-gray-700 dark:text-gray-300'}`}>{log.message}</span>
                </div>
              );
            })
          )}
        </div>
      </Card>
    </div>
  );
}
