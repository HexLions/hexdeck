/**
 * How every card's last read went: when, how long, when next, and why not.
 *
 * A card that fails unexpectedly says only "the server log says why", because
 * everybody who may look at the board sees it. The reason is shown here, to
 * the administrator, next to the cards that are slow or backing off.
 */
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import { get } from '../../api/client'
import { timeAgo } from '../../lib/format'
import { SettingsCard } from './SettingsCard'

export interface CardRun {
  id: number
  title: string
  kind: string
  board: string
  board_slug: string
  page: string
  integration: string
  status: string
  error: string
  code: string
  started: number
  seconds: number
  interval: number | null
  next: number | null
  failures: number
  reason: string
}

function seconds(value: number): string {
  if (value < 1) return `${Math.round(value * 1000)} ms`
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} s`
}

export function CardRunsSettings() {
  const { t } = useTranslation()
  const [onlyTrouble, setOnlyTrouble] = useState(false)
  const runs = useQuery({ queryKey: ['card-runs'], queryFn: () => get<CardRun[]>('/system/cards'), refetchInterval: 10_000 })
  const rows = (runs.data ?? []).filter((row) => !onlyTrouble || row.error || row.failures > 0)
  const troubled = (runs.data ?? []).filter((row) => row.error || row.failures > 0).length
  // When the answer came, not the clock at render: the list is as old as the answer.
  const now = runs.dataUpdatedAt / 1000

  return (
    <SettingsCard title={t('settings.cards.title')} description={t('settings.cards.intro')}>
      <div className="flex flex-wrap items-center gap-3 mb-3 text-sm">
        <span className="text-muted">{t('settings.cards.summary', { count: runs.data?.length ?? 0, troubled })}</span>
        <label className="flex items-center gap-2 ml-auto">
          <input type="checkbox" checked={onlyTrouble} onChange={(event) => setOnlyTrouble(event.target.checked)} />
          {t('settings.cards.onlyTrouble')}
        </label>
      </div>
      {runs.isLoading && <p className="text-sm text-muted">{t('common.loading')}</p>}
      {runs.data && rows.length === 0 && <p className="text-sm text-muted">{t('settings.cards.none')}</p>}
      <ul className="divide-y divide-line">
        {rows.map((row) => (
          <li key={row.id} className="py-2.5" data-testid="card-run">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              <Link to={`/b/${row.board_slug}`} className="font-medium hover:text-accent truncate max-w-full">{row.title}</Link>
              <span className="text-xs text-faint truncate">
                {[row.board, row.page, row.integration].filter(Boolean).join(' · ')}
              </span>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted mt-0.5">
              <span>{t('settings.cards.read', { ago: timeAgo(row.started, now) })}</span>
              <span className={row.seconds >= 10 ? 'text-warn' : undefined}>{t('settings.cards.took', { time: seconds(row.seconds) })}</span>
              {row.next !== null && <span>{t('settings.cards.next', { time: seconds(Math.max(0, row.next - now)) })}</span>}
              {row.failures > 0 && <span className="text-warn">{t('settings.cards.backoff', { count: row.failures })}</span>}
            </div>
            {row.error && <p className="text-xs text-bad mt-1 break-words">{row.error}</p>}
            {row.reason && <p className="text-xs font-mono text-muted mt-0.5 break-words">{row.reason}</p>}
          </li>
        ))}
      </ul>
    </SettingsCard>
  )
}
