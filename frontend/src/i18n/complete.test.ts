/**
 * Every language knows every key, and none is empty. Since only English ships
 * in the bundle and other languages arrive later, a missing key would show up
 * as its path on screen.
 */
import de from './de.json'
import diagnosisDe from './diagnosis.de.json'
import diagnosisEn from './diagnosis.en.json'
import diagnosisEs from './diagnosis.es.json'
import diagnosisIt from './diagnosis.it.json'
import inventoryDe from './inventory.de.json'
import inventoryEn from './inventory.en.json'
import inventoryEs from './inventory.es.json'
import inventoryIt from './inventory.it.json'
import en from './en.json'
import spanish from './es.json'
import italian from './it.json'

function paths(value: unknown, prefix = ''): string[] {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return prefix ? [prefix] : []
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => paths(child, prefix ? `${prefix}.${key}` : key))
}

function lookup(data: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((node, key) => (node as Record<string, unknown> | undefined)?.[key], data)
}

const english = new Set(paths(en))
const OTHERS = [['de', de], ['it', italian], ['es', spanish]] as const

describe('language files', () => {
  it.each(OTHERS)('know the same keys: %s', (language, data) => {
    const known = new Set(paths(data))
    expect([...known].filter((p) => !english.has(p)).sort(), `only in ${language}.json`).toEqual([])
    expect([...english].filter((p) => !known.has(p)).sort(), 'only in en.json').toEqual([])
  })

  it('are not empty by accident', () => {
    // Two empty sets are equal too; the floor keeps the test honest.
    expect(english.size).toBeGreaterThan(200)
  })

  it('have no empty texts', () => {
    for (const [language, data] of [['en', en], ...OTHERS] as const) {
      const empty = paths(data).filter((p) => String(lookup(data, p)).trim() === '')
      expect(empty, `${language}: empty texts`).toEqual([])
    }
  })

  it('carry the same placeholders', () => {
    const placeholders = (text: string) => (text.match(/\{\{\w+\}\}/g) ?? []).sort().join(',')
    const different = [...english].filter((p) => placeholders(String(lookup(en, p))) !== placeholders(String(lookup(de, p))))
    expect(different, 'placeholders differ between en and de').toEqual([])
  })

  it('use no em dashes', () => {
    for (const [language, data] of [['en', en], ...OTHERS] as const) {
      const offenders = paths(data).filter((p) => String(lookup(data, p)).includes('—'))
      expect(offenders, `${language}: em dash`).toEqual([])
    }
  })
})

/**
 * The words of the inventory and the diagnosis come in files of their own (`lib/lazyTexts.ts`);
 * each language carries every key and placeholder of the English one.
 */
const LAZY = {
  inventory: { en: inventoryEn, de: inventoryDe, es: inventoryEs, it: inventoryIt },
  diagnosis: { en: diagnosisEn, de: diagnosisDe, es: diagnosisEs, it: diagnosisIt },
}
for (const [part, bundles] of Object.entries(LAZY)) {
  describe(`the ${part} words in every language`, () => {
    const english = new Set(paths(bundles.en))
    it('are not empty by accident', () => {
      expect(english.size).toBeGreaterThan(20)
    })
    for (const [language, data] of Object.entries(bundles).filter(([code]) => code !== 'en')) {
      it(`${language}: knows the same keys and placeholders as English, none empty`, () => {
        const known = new Set(paths(data))
        expect([...english].filter((p) => !known.has(p)).sort(), `missing from ${part}.${language}.json`).toEqual([])
        expect([...known].filter((p) => !english.has(p)).sort(), `only in ${part}.${language}.json`).toEqual([])
        const placeholders = (text: string) => (text.match(/\{\{\w+\}\}/g) ?? []).sort().join(',')
        expect([...english].filter((p) => placeholders(String(lookup(bundles.en, p))) !== placeholders(String(lookup(data, p))))).toEqual([])
        expect(paths(data).filter((p) => !String(lookup(data, p)).trim() || String(lookup(data, p)).includes('—'))).toEqual([])
      })
    }
  })
}
