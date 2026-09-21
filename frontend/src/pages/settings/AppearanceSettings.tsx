import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { ApiError, get, put } from '../../api/client'
import { Field, Toast } from '../../components/ui'
import { accentVariables, applyAppearance, type Appearance, type Theme } from '../../lib/appearance'
import { SettingsCard } from './SettingsCard'

const EMPTY: Appearance = { preset: 'hex', accent: '', css: '', colour: '#3aa0ff', presets: {}, theme: null, themes: {} }

/** A pasted theme, or the reason it is not one. */
function parseTheme(text: string): Theme | string {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return 'json'
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return 'shape'
  const candidate = parsed as Partial<Theme>
  return { name: String(candidate.name ?? ''), dark: candidate.dark ?? {}, light: candidate.light ?? {} }
}

/** Hand the theme over as a file, for whoever wants the same look. */
function download(theme: Theme) {
  const blob = new Blob([JSON.stringify(theme, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `hexdeck-theme-${(theme.name || 'theme').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.json`
  link.click()
  URL.revokeObjectURL(url)
}

/**
 * The look of the whole installation: one accent colour and, for whoever
 * wants it, a style sheet of their own.
 *
 * What is typed here is painted on at once, before it is saved, because a
 * colour is nothing to judge from a form field. Leaving the page without
 * saving puts the stored look back.
 */
export function AppearanceSettings() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const saved = useQuery({ queryKey: ['appearance'], queryFn: () => get<Appearance>('/settings/appearance') })
  const [form, setForm] = useState<Appearance>(EMPTY)
  const [toast, setToast] = useState<{ text: string; level: 'ok' | 'error' } | null>(null)
  const [pasted, setPasted] = useState('')

  useEffect(() => {
    if (saved.data) setForm(saved.data)
  }, [saved.data])

  /** Which bundled theme the form holds, if it holds one unchanged. */
  const bundled = Object.entries(form.themes ?? {}).find(([, theme]) => JSON.stringify(theme) === JSON.stringify(form.theme))?.[0] ?? (form.theme ? 'custom' : 'shipped')
  const importTheme = () => {
    const theme = parseTheme(pasted)
    if (typeof theme === 'string') {
      setToast({ text: t(`settings.appearance.themeBad.${theme}`), level: 'error' })
      return
    }
    setForm((current) => ({ ...current, theme }))
    setPasted('')
    setToast({ text: t('settings.appearance.themeImported', { name: theme.name || t('settings.appearance.themeCustom') }), level: 'ok' })
  }

  // Show it while it is being chosen, and put the stored look back on leaving.
  const preview = form.accent.trim() || form.presets?.[form.preset] || form.colour
  useEffect(() => {
    applyAppearance({ ...form, colour: preview })
    return () => {
      if (saved.data) applyAppearance(saved.data)
    }
  }, [form, preview, saved.data])

  const store = async () => {
    try {
      const answer = await put<Appearance>('/settings/appearance', { preset: form.preset, accent: form.accent, css: form.css, theme: form.theme ?? null })
      setForm(answer)
      applyAppearance(answer)
      client.setQueryData(['appearance'], answer)
      setToast({ text: t('common.saved'), level: 'ok' })
    } catch (failure) {
      setToast({ text: failure instanceof ApiError ? failure.message : t('errors.network'), level: 'error' })
    }
  }

  const presets = Object.entries(form.presets ?? {})
  const themes = Object.entries(form.themes ?? {})
  return (
    <>
      <SettingsCard title={t('settings.appearance.themeTitle')} description={t('settings.appearance.themeHelp')}>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t('settings.appearance.themeTitle')}>
          {[['shipped', null] as const, ...themes].map(([key, theme]) => {
            const chosen = bundled === key
            const swatch = theme ? [theme.dark.bg, theme.dark.accent, theme.light.bg, theme.light.accent] : ['#0a0c10', '#3aa0ff', '#f4f6f8', '#1b63bd']
            return (
              <button
                key={key}
                type="button"
                role="radio"
                aria-checked={chosen}
                className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-sm ${chosen ? 'border-accent bg-accent-soft' : 'border-line hover:bg-surface-hover'}`}
                onClick={() => setForm((current) => ({ ...current, theme: theme ? { ...theme } : null }))}
              >
                <span className="flex overflow-hidden rounded hex-clip" aria-hidden="true">
                  {swatch.map((colour, index) => (
                    <span key={index} className="h-5 w-3" style={{ background: colour }} />
                  ))}
                </span>
                {theme ? theme.name : t('settings.appearance.themeShipped')}
              </button>
            )
          })}
          {bundled === 'custom' && (
            <span className="flex items-center rounded-xl border border-accent bg-accent-soft px-3 py-2 text-sm" role="radio" aria-checked>
              {form.theme?.name || t('settings.appearance.themeCustom')}
            </span>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-start gap-2">
          <textarea
            className="input min-h-20 flex-1 font-mono text-[12px]"
            spellCheck={false}
            aria-label={t('settings.appearance.themePaste')}
            placeholder={t('settings.appearance.themePaste')}
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
          />
          <div className="flex flex-col gap-2">
            <button type="button" className="btn" disabled={!pasted.trim()} onClick={importTheme}>
              {t('settings.appearance.themeImport')}
            </button>
            <button type="button" className="btn" disabled={!form.theme} onClick={() => form.theme && download(form.theme)}>
              <Download size={14} /> {t('settings.appearance.themeExport')}
            </button>
          </div>
        </div>
        <p className="mt-2 text-xs text-faint">{t('settings.appearance.themeTokens')}</p>
      </SettingsCard>

      <SettingsCard title={t('settings.appearance.title')} description={t('settings.appearance.help')}>
        <Field label={t('settings.appearance.accent')} help={t('settings.appearance.accentHelp')}>
          <div className="flex flex-wrap gap-2">
            {presets.map(([name, colour]) => {
              const chosen = !form.accent.trim() && form.preset === name
              return (
                <button
                  key={name}
                  className={`h-9 w-9 rounded-full grid place-items-center border-2 ${chosen ? 'border-ink' : 'border-transparent'}`}
                  style={{ background: colour }}
                  aria-label={t(`settings.appearance.colours.${name}`, name)}
                  aria-pressed={chosen}
                  onClick={() => setForm((current) => ({ ...current, preset: name, accent: '' }))}
                >
                  {chosen && <Check size={16} className="text-black/70" />}
                </button>
              )
            })}
          </div>
        </Field>

        <Field label={t('settings.appearance.own')} htmlFor="a-own" help={t('settings.appearance.ownHelp')}>
          <div className="flex items-center gap-2">
            <input
              id="a-own"
              type="color"
              className="h-9 w-12 rounded-lg bg-transparent border border-line"
              value={/^#[0-9a-f]{6}$/i.test(form.accent) ? form.accent : preview}
              onChange={(e) => setForm((current) => ({ ...current, accent: e.target.value }))}
              aria-label={t('settings.appearance.own')}
            />
            <input
              className="input font-mono w-32"
              placeholder="#3aa0ff"
              maxLength={7}
              value={form.accent}
              onChange={(e) => setForm((current) => ({ ...current, accent: e.target.value }))}
              aria-label={t('settings.appearance.ownHex')}
            />
            {form.accent && (
              <button className="btn" onClick={() => setForm((current) => ({ ...current, accent: '' }))}>
                {t('settings.appearance.backToPreset')}
              </button>
            )}
          </div>
        </Field>

        <Field label={t('settings.appearance.css')} htmlFor="a-css" help={t('settings.appearance.cssHelp')}>
          <textarea
            id="a-css"
            className="input font-mono text-[12px] min-h-40"
            spellCheck={false}
            placeholder=".card { border-radius: 4px; }"
            value={form.css}
            onChange={(e) => setForm((current) => ({ ...current, css: e.target.value }))}
          />
        </Field>
        <p className="text-xs text-faint">{t('settings.appearance.cssLimits')}</p>

        <div className="mt-4 flex gap-2">
          <button className="btn btn-accent" onClick={store}>
            {t('common.save')}
          </button>
          <button
            className="btn"
            onClick={() => {
              setForm({ ...EMPTY, presets: form.presets, themes: form.themes })
            }}
          >
            {t('settings.appearance.reset')}
          </button>
        </div>
      </SettingsCard>

      <SettingsCard title={t('settings.appearance.previewTitle')} description={t('settings.appearance.previewHelp')}>
        <div className="flex flex-wrap items-center gap-3" style={accentVariables(preview) as React.CSSProperties}>
          <button className="btn btn-accent">{t('common.save')}</button>
          <button className="btn">{t('common.cancel')}</button>
          <span className="chip">
            <span className="dot" data-status="ok" /> {t('settings.appearance.previewChip')}
          </span>
          <span className="text-accent text-sm font-medium">{t('settings.appearance.previewText')}</span>
        </div>
      </SettingsCard>
      {toast && (
        <Toast level={toast.level} onClose={() => setToast(null)}>
          {toast.text}
        </Toast>
      )}
    </>
  )
}
