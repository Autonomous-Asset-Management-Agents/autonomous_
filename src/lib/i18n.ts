import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import translationEN from '../locales/en/translation.json';
import translationDE from '../locales/de/translation.json';

const resources = {
  en: {
    translation: translationEN,
  },
  de: {
    translation: translationDE,
  },
};

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    // #2869 review: bound the language space to what we actually ship and collapse
    // region variants (de-DE -> de) so i18n.language matches the LanguageSwitcher's
    // 'en'/'de' values and no missing region-namespace is fetched.
    supportedLngs: ['en', 'de'],
    load: 'languageOnly',
    // Options for language detector
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
    },
    interpolation: {
      escapeValue: false, // not needed for react as it escapes by default
    },
  });

export default i18n;
