/**
 * Ready-made boards. A tile per template; the chosen one asks which of the
 * installation's connections stand in for its placeholders, a placeholder
 * left empty takes its cards with it, and the board is made in one call.
 */
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

import { ApiError, get, post } from '../api/client'
import { tAdapter } from '../i18n/texts'
import { ServiceIcon } from './ServiceIcon'
import { Field } from './ui'

export interface TemplateSummary {
  id: string
  name: string
  description: string
  tags: string[]
  icon: string
  cards: number
  pages: number
  integrations: { name: string; kind: string; label: string; icon: string }[]
}

export interface TemplateDetail extends TemplateSummary {
  available: { id: number; name: string; kind: string }[]
}

const LEAVE_OUT = ''

export function TemplatePicker({ onCreated }: { onCreated?: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => get<TemplateSummary[]>('/templates') })
  const [chosen, setChosen] = useState<string | null>(null)
  const detail = useQuery({ queryKey: ['templates', chosen], queryFn: () => get<TemplateDetail>(`/templates/${chosen}`), enabled: chosen !== null })
  const [name, setName] = useState('')
  const [connections, setConnections] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  // A fresh choice starts from the template's own name and, for every
  // placeholder, the first connection of its kind, so the usual case is one click.
  useEffect(() => {
    if (!detail.data) return
    setName(tAdapter(detail.data.name))
    const first: Record<string, string> = {}
    for (const placeholder of detail.data.integrations) {
      const match = detail.data.available.find((a) => a.kind === placeholder.kind)
      first[placeholder.name] = match ? String(match.id) : LEAVE_OUT
    }
    setConnections(first)
    setError('')
  }, [detail.data])

  const create = () => {
    if (!chosen) return
    setBusy(true)
    setError('')
    const mapped: Record<string, number | null> = {}
    for (const [placeholder, value] of Object.entries(connections)) mapped[placeholder] = value === LEAVE_OUT ? null : Number(value)
    void post<{ slug: string }>(`/templates/${chosen}`, { name: name.trim() || undefined, connections: mapped })
      .then((created) => {
        onCreated?.()
        navigate(`/b/${created.slug}`)
      })
      .catch((failure) => setError(failure instanceof ApiError ? failure.message : t('errors.network')))
      .finally(() => setBusy(false))
  }

  const rows = templates.data ?? []
  return (
    <div>
      <ul className="grid gap-2 sm:grid-cols-2" aria-label={t('templates.title')}>
        {rows.map((template) => (
          <li key={template.id}>
            <button
              type="button"
              className={`flex w-full items-start gap-3 rounded-xl border p-3 text-left ${template.id === chosen ? 'border-accent bg-accent-soft' : 'border-line hover:bg-surface-hover'}`}
              aria-pressed={template.id === chosen}
              onClick={() => setChosen(template.id === chosen ? null : template.id)}
            >
              <span className="mt-0.5 flex h-8 w-8 flex-none items-center justify-center hex-clip bg-surface-hover text-accent">
                <ServiceIcon icon={`lucide:${template.icon}`} size={16} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-2">
                  <span className="font-medium">{tAdapter(template.name)}</span>
                  <span className="text-[11px] text-faint">{t('templates.cards', { count: template.cards })}</span>
                </span>
                <span className="mt-0.5 block text-xs text-muted">{tAdapter(template.description)}</span>
                <span className="mt-1.5 flex flex-wrap items-center gap-1">
                  {(template.integrations ?? []).map((c) => (
                    <span key={c.name} className="flex items-center gap-1 text-[11px] text-faint" title={c.label}>
                      <ServiceIcon icon={c.icon} size={12} />
                    </span>
                  ))}
                  {(template.tags ?? []).map((tag) => (
                    <span key={tag} className="chip !py-0 text-[10px]">{tAdapter(tag)}</span>
                  ))}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
      {chosen && detail.data && (
        <div className="mt-3 rounded-xl border border-line p-3" data-testid="template-detail">
          <Field label={t('board.newName')} htmlFor="tp-name">
            <input id="tp-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          {detail.data.integrations.length > 0 && (
            <p className="mb-2 text-xs text-muted">{t('templates.connectionsHelp')}</p>
          )}
          {detail.data.integrations.map((placeholder) => {
            const options = detail.data!.available.filter((a) => a.kind === placeholder.kind)
            return (
              <div key={placeholder.name} className="mb-2 flex items-center gap-2 text-sm">
                <span className="flex w-40 flex-none items-center gap-2 truncate">
                  <ServiceIcon icon={placeholder.icon} size={14} />
                  {placeholder.label}
                </span>
                <select
                  className="input h-8 text-xs"
                  aria-label={placeholder.label}
                  value={connections[placeholder.name] ?? LEAVE_OUT}
                  onChange={(e) => setConnections({ ...connections, [placeholder.name]: e.target.value })}
                >
                  <option value={LEAVE_OUT}>{options.length ? t('templates.leaveOut') : t('templates.none')}</option>
                  {options.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </div>
            )
          })}
          {error && (
            <p className="mb-2 text-sm text-bad" role="alert">{error}</p>
          )}
          <button type="button" className="btn btn-accent" disabled={busy} onClick={create}>
            {t('templates.create')}
          </button>
        </div>
      )}
    </div>
  )
}
