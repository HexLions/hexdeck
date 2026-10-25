/**
 * Key figures: the number cards a page put in here, drawn as tiles of one
 * grid with their value, unit and colour. Each tile is still its card: its
 * data, its settings, its yellow and red lines and its link are its own.
 *
 * While the board is arranged, each tile offers the settings of its card.
 */
import { Settings2 } from 'lucide-react'
import { useRef, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import { tLabel } from '../i18n/texts'
import { figuresLayout, tileMetric, toneOf } from '../lib/figures'
import { cardsOf, usePageCards } from '../lib/groups'
import { formatValue } from '../lib/format'
import { safeUrl } from '../lib/safeUrl'
import type { WidgetData, WidgetView } from '../lib/types'
import { useBoxSize } from '../lib/useBoxSize'
import { useLive } from '../stores/live'
import type { RenderProps } from './renderers'
import { ServiceIcon } from './ServiceIcon'
import { Sparkline } from './Sparkline'

function Tile({ card, data, points, history }: { card: WidgetView; data: WidgetData | undefined; points: number[] | undefined; history: boolean }) {
  const { t } = useTranslation()
  const page = usePageCards()
  const tone = toneOf(card, data)
  const primary = data?.primary
  const label = primary?.label ? tLabel(primary.label) : ''
  const title = card.title || label || tLabel(card.kind)
  const value = formatValue(primary?.value)
  const unit = primary?.unit ? tLabel(primary.unit) : ''
  const reason = data?.error ? String(data.error) : tone === 'warn' || tone === 'bad' ? String(data?.meta?.status_reason ?? '') : ''
  const why = reason ? tLabel(reason) : label && label !== title ? label : (card.integration_name ?? '')
  // ⚠️ A link from the service is checked like every other; one that is not http(s) is none.
  const link = page.editing ? undefined : safeUrl(card.link || data?.link || card.service_link) || undefined
  const body = (
    <>
      {history && points && points.length >= 5 && new Set(points).size > 1 && (
        <span className="nd-tile-history" aria-hidden="true">
          <Sparkline values={points} height={40} fill={false} className="!h-full" />
        </span>
      )}
      <span className="nd-tile-head">
        {card.icon ? <ServiceIcon icon={card.icon} size={14} /> : null}
        <span className="truncate">{title}</span>
        <span className="dot nd-tile-dot" data-status={tone} aria-hidden="true" />
      </span>
      <span className="nd-tile-value num">
        {value}
        {unit && <small>{unit}</small>}
      </span>
      {why && <span className="nd-tile-why">{why}</span>}
      {page.editing && page.onSettings && (
        <button type="button" className="btn btn-icon btn-flat h-6 w-6 nd-tile-settings" onClick={() => page.onSettings?.(card.id)} aria-label={t('widget.settings')} title={t('widget.settings')}>
          <Settings2 size={13} />
        </button>
      )}
    </>
  )
  return link ? (
    <a className="nd-tile" data-tone={tone} href={link} target="_blank" rel="noopener noreferrer" title={title}>
      {body}
    </a>
  ) : (
    <div className="nd-tile" data-tone={tone} title={title}>
      {body}
    </div>
  )
}

export function FiguresCard({ widget, data }: RenderProps) {
  const { t } = useTranslation()
  const page = usePageCards()
  const live = useLive((state) => state.data)
  const series = useLive((state) => state.series)
  const box = useRef<HTMLDivElement>(null)
  const size = useBoxSize(box)
  const cards = cardsOf(widget, page.widgets)
  const columns = String(data?.meta?.columns ?? widget.options?.columns ?? 'auto')
  const look = String(data?.meta?.tone ?? widget.options?.tone ?? 'fill')
  const history = Boolean(data?.meta?.history ?? widget.options?.history)
  const { across, down } = figuresLayout(cards.length, size.width, size.height, columns)
  return (
    <div ref={box} className="nd-figures" data-look={look} style={{ '--across': across, '--down': down } as CSSProperties} data-testid="figures">
      {cards.map((card) => {
        const cardData = live[card.id]
        const metric = tileMetric(cardData)
        return <Tile key={card.id} card={card} data={cardData} points={metric ? series[card.id]?.[metric] : undefined} history={history} />
      })}
      {!cards.length && <p className="nd-figures-empty">{page.editing ? t('figures.emptyEditing') : t('figures.empty')}</p>}
    </div>
  )
}
