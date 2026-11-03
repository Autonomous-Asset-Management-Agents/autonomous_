import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import translationEN from '../locales/en/translation.json';
import translationDE from '../locales/de/translation.json';

import { GERMAN_ENABLED } from './languageGate';

const resources = {
  en: {
    translation: translationEN,
  },
  de: {
    translation: translationDE,
  },
};

// #4604: the gate owns the LANGUAGE, not just the toggle. Until this, GERMAN_ENABLED only
// hid the LanguageSwitcher, while the detector below still resolved navigator 'de-DE' ->
// 'de' and served the un-reviewed German bundle to every German visitor. Appliance.tsx
// patched that per page with a post-mount changeLanguage('en'); the landing carried no such
// guard, so the un-reviewed copy shipped. The resources stay registered either way — the
// gate closes the door, it does not throw away the room, so the DE launch is a gate flip.
if (!GERMAN_ENABLED && typeof window !== 'undefined') {
  // A visitor who already carries 'de' in localStorage must not keep it while the gate is
  // closed — the cache outlives the detector and would otherwise pin German forever.
  try {
    window.localStorage.removeItem('i18nextLng');
  } catch {
    // private mode / blocked storage: nothing was cached, so there is nothing to clear
  }
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    // #2869 review: bound the language space to what we actually ship and collapse
    // region variants (de-DE -> de) so i18n.language matches the LanguageSwitcher's
    // 'en'/'de' values and no missing region-namespace is fetched.
    // #4604: with the gate closed the language space is English alone, so neither an
    // explicit changeLanguage('de') nor a cached locale can resolve to German.
    supportedLngs: GERMAN_ENABLED ? ['en', 'de'] : ['en'],
    load: 'languageOnly',
    // #4604: gate closed -> pin English outright and never ask the browser.
    ...(GERMAN_ENABLED ? {} : { lng: 'en' }),
    // Options for language detector
    detection: GERMAN_ENABLED
      ? {
          order: ['localStorage', 'navigator'],
          caches: ['localStorage'],
        }
      : { order: [], caches: [] },
    interpolation: {
      escapeValue: false, // not needed for react as it escapes by default
    },
  });

export default i18n;
