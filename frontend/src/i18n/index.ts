import i18next from 'i18next';
import en from './en.json';
import zh from './zh.json';

const DEFAULT_LANGUAGE = navigator.language.startsWith('zh') ? 'zh' : 'en';

export async function initI18n(): Promise<void> {
  try {
    await i18next.init({
      lng: DEFAULT_LANGUAGE,
      fallbackLng: 'en',
      resources: {
        en: { translation: en },
        zh: { translation: zh },
      },
      interpolation: {
        escapeValue: false,
      },
    });
  } catch (e) {
    console.error('i18n initialization failed:', e);
    // Fallback: use English only
    await i18next.init({
      lng: 'en',
      resources: { en: { translation: en } },
      interpolation: { escapeValue: false },
    });
  }
}

export { i18next };
