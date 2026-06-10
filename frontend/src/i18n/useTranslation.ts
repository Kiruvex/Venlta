import { useState, useEffect, useCallback } from 'preact/hooks';
import { i18next } from './index';

/**
 * Preact-compatible useTranslation hook.
 *
 * Replaces react-i18next's useTranslation, which relies on React 18 internals
 * (useSyncExternalStore, etc.) that Preact compat does not fully implement.
 *
 * Returns the same { t, i18n } interface so existing call sites work unchanged.
 */
export function useTranslation() {
  // Re-render on every language change
  const [, setLang] = useState(i18next.language);

  useEffect(() => {
    const onChanged = (lng: string) => setLang(lng);
    i18next.on('languageChanged', onChanged);
    return () => i18next.off('languageChanged', onChanged);
  }, []);

  const t = useCallback(
    (key: string, options?: Record<string, unknown>) => i18next.t(key, options),
    // t identity changes when language changes (via setLang re-render)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [i18next.language],
  );

  return { t, i18n: i18next };
}
