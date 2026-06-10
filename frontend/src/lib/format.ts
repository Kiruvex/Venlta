import { i18next } from '../i18n';

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return '0 B';
  if (bytes < 0) return '-' + formatBytes(-bytes);
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function formatRate(bytesPerSec: number): string {
  return formatBytes(bytesPerSec) + '/s';
}

export function formatSpeed(bytesPerSec: number): string {
  // 带宽速度格式化（与 formatRate 类似，但用于节点速度测试结果显示）
  if (!Number.isFinite(bytesPerSec) || bytesPerSec < 0) return '-';
  return formatBytes(bytesPerSec) + '/s';
}

export function formatLatency(ms: number | null): string {
  const { t } = useI18nDirect();
  if (ms === null || ms < 0) return t('status.timeout');
  if (ms === 0) return t('status.less_than_1ms');
  return t('status.latency_ms', { ms });
}

function useI18nDirect() {
  return { t: i18next.t.bind(i18next) };
}

export function formatDate(dateStr: string | Date | null | undefined, format: 'full' | 'date' | 'relative' = 'full'): string {
  if (!dateStr) return '-';
  const date = dateStr instanceof Date ? dateStr : new Date(dateStr);
  if (isNaN(date.getTime())) return '-';
  if (format === 'relative') {
    const now = Date.now();
    const diff = now - date.getTime();
    if (i18next.isInitialized) {
      if (diff < 60000) return i18next.t('time.just_now');
      if (diff < 3600000) return i18next.t('time.minutes_ago', { count: Math.floor(diff / 60000) });
      if (diff < 86400000) return i18next.t('time.hours_ago', { count: Math.floor(diff / 3600000) });
      if (diff < 604800000) return i18next.t('time.days_ago', { count: Math.floor(diff / 86400000) });
    } else {
      if (diff < 60000) return 'just now';
      if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
      if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
      if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`;
    }
    return date.toLocaleDateString();
  }
  if (format === 'date') {
    return date.toLocaleDateString();
  }
  return date.toLocaleString();
}
