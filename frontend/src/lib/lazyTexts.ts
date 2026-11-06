/**
 * Words that come with the part that needs them, not with every board: the
 * inventory and the diagnosis page. Only administrators who open those ever
 * read them.
 *
 * English comes with this module, so whatever imports it has its words at
 * once; another language follows a moment later.
 */
import i18next from 'i18next'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import diagnosisEn from '../i18n/diagnosis.en.json'
import inventoryEn from '../i18n/inventory.en.json'

i18next.addResourceBundle('en', 'translation', inventoryEn, true, true)
i18next.addResourceBundle('en', 'translation', diagnosisEn, true, true)

const loaders: Record<string, Record<string, () => Promise<{ default: unknown }>>> = {
  inventory: {
    de: () => import('../i18n/inventory.de.json'),
    es: () => import('../i18n/inventory.es.json'),
    it: () => import('../i18n/inventory.it.json'),
  },
  diagnosis: {
    de: () => import('../i18n/diagnosis.de.json'),
    es: () => import('../i18n/diagnosis.es.json'),
    it: () => import('../i18n/diagnosis.it.json'),
  },
}

/** Whether the words of ``part`` are there in the language on screen. */
export function useLazyTexts(part: 'inventory' | 'diagnosis'): boolean {
  const { i18n } = useTranslation()
  const language = i18n.language
  const load = loaders[part][language]
  const [ready, setReady] = useState<string | null>(language === 'en' || !load ? language : null)
  useEffect(() => {
    if (language === 'en' || !load) {
      setReady(language)
      return
    }
    let gone = false
    void load()
      .then((bundle) => {
        i18n.addResourceBundle(language, 'translation', bundle.default, true, true)
      })
      .catch(() => undefined)
      // In English rather than not at all, when the words of a language cannot be fetched.
      .finally(() => {
        if (!gone) setReady(language)
      })
    return () => {
      gone = true
    }
  }, [language, load, i18n])
  return ready === language
}
