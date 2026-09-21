/**
 * A board from another dashboard's files: Homepage's YAML or a Homarr config.
 *
 * Two steps. The files are read into a plan that says what would be made:
 * every connection with what is still missing, every card with a tick to
 * leave it out, and what could not be carried over. Then the plan is made
 * into connections and a board, in one call. Values pasted here are data;
 * the server never reads them as environment references.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { ApiError, post } from '../api/client'
import { tAdapter } from '../i18n/texts'
import { ServiceIcon } from './ServiceIcon'
import { Field } from './ui'

type Source = 'auto' | 'homepage' | 'homarr'

interface Connection {
  key: string
  kind: string
  label: string
  icon: string
  name: string
  config: Record<string, string | number | boolean>
  missing: string[]
}

interface Card {
  key: string
  kind: string
  label: string
  title: string
  icon: string
  link: string
  connection: string | null
  enabled: boolean
}

interface Plan {
  source: 'homepage' | 'homarr'
  name: string
  connections: Connection[]
  pages: { name: string; cards: Card[] }[]
  warnings: string[]
}

const FILES: Record<Exclude<Source, 'auto'>, string[]> = { homepage: ['services', 'bookmarks', 'widgets'], homarr: ['config'] }

/** Read a picked file into the matching text area. */
function readInto(file: File, set: (text: string) => void) {
  const reader = new FileReader()
  reader.onload = () => set(String(reader.result ?? ''))
  reader.readAsText(file)
}

export function DashboardImport() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [source, setSource] = useState<Source>('auto')
  const [files, setFiles] = useState<Record<string, string>>({})
  const [plan, setPlan] = useState<Plan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const slots = source === 'auto' ? ['services', 'bookmarks', 'config'] : FILES[source]
  const preview = async () => {
    setBusy(true)
    setError('')
    try {
      setPlan(await post<Plan>('/imports/preview', { source, files }))
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : t('errors.network'))
    } finally {
      setBusy(false)
    }
  }
  const apply = async () => {
    if (!plan) return
    setBusy(true)
    setError('')
    try {
      const board = await post<{ slug: string }>('/imports/apply', { plan })
      await client.invalidateQueries()
      navigate(`/b/${board.slug}`)
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : t('errors.network'))
    } finally {
      setBusy(false)
    }
  }
  const setCard = (key: string, enabled: boolean) =>
    setPlan((current) => current && { ...current, pages: current.pages.map((p) => ({ ...p, cards: p.cards.map((c) => (c.key === key ? { ...c, enabled } : c)) })) })
  const setSecret = (key: string, field: string, value: string) =>
    setPlan((current) => current && {
      ...current,
      connections: current.connections.map((c) => (c.key === key ? { ...c, config: { ...c.config, [field]: value }, missing: value ? c.missing.filter((m) => m !== field) : c.missing } : c)),
    })
  const used = new Set(plan?.pages.flatMap((p) => p.cards.filter((c) => c.enabled && c.connection).map((c) => c.connection)) ?? [])

  return (
    <div>
      {!plan && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <label htmlFor="di-source" className="text-muted">{t('dashboardImport.source')}</label>
            <select id="di-source" className="input h-8 w-48 text-xs" value={source} onChange={(e) => setSource(e.target.value as Source)}>
              <option value="auto">{t('dashboardImport.sources.auto')}</option>
              <option value="homepage">Homepage</option>
              <option value="homarr">Homarr</option>
            </select>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {slots.map((slot) => (
              <Field key={slot} label={t(`dashboardImport.files.${slot}`)} htmlFor={`di-${slot}`} help={t(`dashboardImport.filesHelp.${slot}`)}>
                <textarea
                  id={`di-${slot}`}
                  className="input min-h-28 font-mono text-[12px]"
                  spellCheck={false}
                  value={files[slot] ?? ''}
                  onChange={(e) => setFiles({ ...files, [slot]: e.target.value })}
                />
                <input type="file" className="mt-1 text-xs text-muted" aria-label={t('dashboardImport.pick', { name: slot })} onChange={(e) => e.target.files?.[0] && readInto(e.target.files[0], (text) => setFiles((f) => ({ ...f, [slot]: text })))} />
              </Field>
            ))}
          </div>
          {error && <p className="mb-2 text-sm text-bad" role="alert">{error}</p>}
          <button type="button" className="btn btn-accent" disabled={busy || !Object.values(files).some((v) => v.trim())} onClick={() => void preview()}>
            {t('dashboardImport.preview')}
          </button>
        </>
      )}
      {plan && (
        <div data-testid="import-plan">
          <Field label={t('board.newName')} htmlFor="di-name">
            <input id="di-name" className="input" value={plan.name} onChange={(e) => setPlan({ ...plan, name: e.target.value })} />
          </Field>
          {plan.connections.length > 0 && (
            <>
              <h3 className="mb-1 text-sm font-semibold">{t('dashboardImport.connections', { count: plan.connections.length })}</h3>
              <ul className="mb-3 space-y-1">
                {plan.connections.map((c) => (
                  <li key={c.key} className={`flex flex-wrap items-center gap-2 rounded-xl border border-line p-2 text-sm ${used.has(c.key) ? '' : 'opacity-50'}`}>
                    <ServiceIcon icon={c.icon} size={16} />
                    <span className="font-medium">{c.name}</span>
                    <span className="text-xs text-muted">{c.label} · {String(c.config.url ?? c.config.host ?? '')}</span>
                    {c.missing.map((field) => (
                      <input
                        key={field}
                        className="input h-7 w-44 text-xs"
                        type="password"
                        placeholder={t('dashboardImport.missing', { field })}
                        aria-label={`${c.name}: ${field}`}
                        onChange={(e) => setSecret(c.key, field, e.target.value)}
                      />
                    ))}
                    {c.missing.length > 0 && <span className="text-xs text-warn">{t('dashboardImport.disabledUntilFilled')}</span>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {plan.pages.map((page) => (
            <div key={page.name} className="mb-3">
              <h3 className="mb-1 text-sm font-semibold">{page.name} <span className="text-xs font-normal text-muted">{t('dashboardImport.cards', { count: page.cards.length })}</span></h3>
              <ul className="grid gap-1 sm:grid-cols-2">
                {page.cards.map((card) => (
                  <li key={card.key}>
                    <label className="flex items-center gap-2 rounded-lg border border-line px-2 py-1 text-sm">
                      <input type="checkbox" className="accent-accent" checked={card.enabled} onChange={(e) => setCard(card.key, e.target.checked)} />
                      <ServiceIcon icon={card.icon || 'lucide:box'} size={14} />
                      <span className="truncate">{card.title}</span>
                      <span className="ml-auto text-[11px] text-faint">{card.kind === 'core.app' ? t('dashboardImport.tile') : tAdapter(card.label)}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {plan.warnings.length > 0 && (
            <ul className="mb-3 list-disc space-y-0.5 pl-5 text-xs text-muted">
              {plan.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          {error && <p className="mb-2 text-sm text-bad" role="alert">{error}</p>}
          <div className="flex gap-2">
            <button type="button" className="btn btn-accent" disabled={busy} onClick={() => void apply()}>
              {t('dashboardImport.create')}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => setPlan(null)}>
              {t('common.back')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
