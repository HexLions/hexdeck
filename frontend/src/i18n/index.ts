import i18next from 'i18next'
import { initReactI18next } from 'react-i18next'

import en from './en.json'
import { registerTexts, type TextBundle } from './texts'

/**
 * English ships in the bundle; every other language is fetched when chosen.
 * Adding a language means adding two JSON files here and one line in LANGUAGES:
 * the interface texts, and the server texts translated by their English wording.
 */
export const LANGUAGES: Record<string, string> = { en: 'English', de: 'Deutsch', it: 'Italiano' }

const loaders: Record<string, () => Promise<{ default: Record<string, unknown> }>> = {
  de: () => import('./de.json'),
  it: () => import('./it.json'),
}

const textLoaders: Record<string, () => Promise<{ default: unknown }>> = {
  de: () => import('./texts.de.json'),
  it: () => import('./texts.it.json'),
}

void i18next.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: en } },
  interpolation: { escapeValue: false },
  returnNull: false,
})

export async function setLanguage(code: string): Promise<void> {
  const language = code in LANGUAGES ? code : 'en'
  if (!i18next.hasResourceBundle(language, 'translation') && loaders[language]) {
    const bundle = await loaders[language]()
    i18next.addResourceBundle(language, 'translation', bundle.default, true, true)
    if (textLoaders[language]) {
      const texts = await textLoaders[language]()
      registerTexts(language, texts.default as TextBundle)
    }
  }
  await i18next.changeLanguage(language)
  document.documentElement.lang = language
  try {
    localStorage.setItem('nexdeck.language', language)
  } catch {
    // storage may be unavailable
  }
}

/** The browser's language when it is one HexDeck speaks, else English. */
function browserLanguage(): string {
  const code = navigator.language.slice(0, 2).toLowerCase()
  return code in LANGUAGES ? code : 'en'
}

export function storedLanguage(): string {
  try {
    return localStorage.getItem('nexdeck.language') || browserLanguage()
  } catch {
    return 'en'
  }
}

export default i18next
