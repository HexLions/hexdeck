import { AlertTriangle, ArrowRightLeft, ExternalLink, Proportions, RefreshCw, Settings2, Trash2 } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { SIZE_PRESETS, type SizePreset } from '../lib/layout'
import { safeUrl } from '../lib/safeUrl'

import { tLabel } from '../i18n/texts'
import type { Action, WidgetData, WidgetView } from '../lib/types'
import { renderWidget } from './renderers'
import { ServiceIcon } from './ServiceIcon'

interface Props {
  widget: WidgetView
  data: WidgetData | undefined
  series?: Record<string, number[]>
  editing?: boolean
  canAct?: boolean
  canEdit?: boolean
  onAction?: (action: Action) => void
  onRefresh?: () => void
  onSettings?: () => void
  onRemove?: () => void
  /** Put the card to one of four sizes; offered while editing. */
  onResize?: (preset: SizePreset) => void
  /** The pages a card may be moved to, and the move itself; offered while editing. */
  moveTargets?: MoveTarget[]
  onMove?: (pageId: number) => void
}

export interface MoveTarget {
  id: number
  label: string
}

/** The other pages, of this board and of the boards one may edit, behind one button. */
function MoveMenu({ targets, onMove }: { targets: MoveTarget[]; onMove: (pageId: number) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)
  return (
    <span className="relative">
      <button ref={setAnchor} className="btn btn-icon h-6 w-6 btn-flat" onClick={() => setOpen((value) => !value)} aria-label={t('widget.move')} title={t('widget.move')} aria-expanded={open} aria-haspopup="menu">
        <ArrowRightLeft size={13} />
      </button>
      <CardMenu anchor={anchor} open={open} onClose={() => setOpen(false)} className="flex max-h-56 w-56 flex-col overflow-auto p-1">
          <span className="px-2 py-1 text-[10px] uppercase tracking-wide text-faint">{t('widget.moveTo')}</span>
          {targets.map((target) => (
            <button
              key={target.id}
              role="menuitem"
              className="rounded px-2 py-1 text-left text-xs text-muted hover:bg-surface-hover hover:text-ink"
              onClick={() => {
                setOpen(false)
                onMove(target.id)
              }}
            >
              {target.label}
            </button>
          ))}
      </CardMenu>
    </span>
  )
}

/**
 * A small menu under one of the card's buttons.
 *
 * ⚠️ Drawn on the body, not inside the card. The grid puts every card in a
 * transformed, clipped box, so a menu that hung under the button was cut at
 * the card's edge and a fixed one was placed against the card instead of the
 * window. The portal escapes both; the place is measured from the button.
 */
function CardMenu({ anchor, open, onClose, className, children }: { anchor: HTMLElement | null; open: boolean; onClose: () => void; className: string; children: ReactNode }) {
  const box = useRef<HTMLSpanElement>(null)
  const [at, setAt] = useState<{ top: number; right: number }>({ top: 0, right: 0 })
  useLayoutEffect(() => {
    if (!open || !anchor) return
    const rect = anchor.getBoundingClientRect()
    setAt({ top: rect.bottom + 4, right: Math.max(8, window.innerWidth - rect.right) })
  }, [open, anchor])
  useEffect(() => {
    if (!open) return
    const onDown = (event: globalThis.MouseEvent) => {
      const target = event.target as Node
      if (!box.current?.contains(target) && !anchor?.contains(target)) onClose()
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, anchor, onClose])
  if (!open) return null
  return createPortal(
    <span ref={box} role="menu" className={`glass-strong fixed z-50 rounded-lg shadow-xl ${className}`} style={{ top: at.top, right: at.right }}>
      {children}
    </span>,
    document.body,
  )
}

/** S, M, L, XL behind one button, so a card is sized without dragging its corner. */
function SizeMenu({ onResize }: { onResize: (preset: SizePreset) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null)
  return (
    <span className="relative">
      <button ref={setAnchor} className="btn btn-icon h-6 w-6 btn-flat" onClick={() => setOpen((value) => !value)} aria-label={t('widget.size')} title={t('widget.size')} aria-expanded={open} aria-haspopup="menu">
        <Proportions size={13} />
      </button>
      <CardMenu anchor={anchor} open={open} onClose={() => setOpen(false)} className="flex gap-0.5 p-0.5">
          {SIZE_PRESETS.map((preset) => (
            <button
              key={preset}
              role="menuitem"
              className="btn btn-flat h-6 min-w-7 px-1.5 text-[11px] font-semibold"
              aria-label={t(`widget.sizes.${preset}`)}
              onClick={() => {
                setOpen(false)
                onResize(preset)
              }}
            >
              {preset}
            </button>
          ))}
      </CardMenu>
    </span>
  )
}

const INTERACTIVE = 'a, button, input, select, textarea, [role="button"], .no-click'

/** The frame every widget shares: header, floating controls, body, error strip. */
export function WidgetCard({ widget, data, series, editing, canAct, canEdit, onAction, onRefresh, onSettings, onRemove, onResize, moveTargets, onMove }: Props) {
  const { t, i18n } = useTranslation()
  // A failed fetch is an error state of its own: red, with the server's reason.
  // The server names the reason by code; other languages translate the code,
  // English shows the server's own sentence with its specifics.
  const failed = Boolean(data?.error)
  // With findings switched off, the card keeps its numbers and drops the alarm:
  // a Findings card next to it says what is wrong, once instead of on every card.
  // ⚠️ Switched on, not switched off. A card that shouts by default
  // means a board full of colour nobody reads any more; a card that is
  // quiet until asked means the colour still means something. Cards made
  // before this carry the old answer, written down by migration 8.
  const showFindings = widget.options?.show_findings === true
  const reported = data?.status ?? 'unknown'
  const status = failed ? 'bad' : !showFindings && (reported === 'warn' || reported === 'bad') ? 'ok' : reported
  const errorCode = String(data?.meta?.code ?? '')
  const errorText = failed ? (i18n.language.split('-')[0] === 'en' ? String(data?.error) : t(`errors.widget.${errorCode}`, { defaultValue: String(data?.error) })) : ''
  // ⚠️ data?.link comes from the service, not from the operator. A
  // javascript: address here would run as part of HexDeck.
  const link = safeUrl(widget.link || data?.link || widget.service_link) || undefined
  // Clocks and app tiles draw themselves without a header; the player's cover runs to the edge.
  const bare = ['app', 'clock', 'button', 'image', 'player'].includes(widget.renderer)
  // With a link, the whole card is the link; app tiles are anchors already.
  // ⚠️ Not the player: every gap between its buttons would open the media
  // server in a new tab, which is not what a miss next to "pause" should do.
  const clickable = Boolean(link) && !editing && widget.renderer !== 'app' && widget.renderer !== 'player'
  // The dot says what it means: the state in words, and the reason when the
  // service gives one (Nexview names its findings, for example).
  const urgent = Array.isArray(data?.meta?.urgent) ? (data?.meta?.urgent as unknown[]).map(String) : []
  const reasons = (showFindings || failed ? [errorText, data?.meta?.status_reason ? String(data.meta.status_reason) : '', ...urgent] : [errorText]).filter(Boolean).map((text) => tLabel(text))
  const statusTitle = [t(`status.${status}`), ...reasons].join(' · ')

  const open = (event: MouseEvent<HTMLElement>) => {
    if (!clickable || !link) return
    const target = event.target as HTMLElement
    if (target.closest(INTERACTIVE)) return
    if (window.getSelection()?.toString()) return
    window.open(link, '_blank', 'noopener,noreferrer')
  }

  const controls = (
    <>
      {onRefresh && !editing && (
        <button className="btn btn-icon h-6 w-6 btn-flat" onClick={onRefresh} aria-label={t('widget.refreshNow')} title={t('widget.refreshNow')}>
          <RefreshCw size={13} />
        </button>
      )}
      {link && !editing && (
        <a className="btn btn-icon h-6 w-6 btn-flat" href={link} target="_blank" rel="noopener noreferrer" aria-label={t('widget.openLink')} title={t('widget.openLink')}>
          <ExternalLink size={13} />
        </a>
      )}
      {editing && onResize && <SizeMenu onResize={onResize} />}
      {editing && onMove && moveTargets && moveTargets.length > 0 && <MoveMenu targets={moveTargets} onMove={onMove} />}
      {editing && onSettings && (
        <button className="btn btn-icon h-6 w-6 btn-flat" onClick={onSettings} aria-label={t('widget.settings')} title={t('widget.settings')}>
          <Settings2 size={13} />
        </button>
      )}
      {editing && onRemove && (
        <button className="btn btn-icon h-6 w-6 btn-flat btn-danger" onClick={onRemove} aria-label={t('widget.remove.title')} title={t('widget.remove.title')}>
          <Trash2 size={13} />
        </button>
      )}
    </>
  )
  const showControls = editing ? Boolean(onSettings || onRemove || onResize || onMove) : Boolean(onRefresh || link)

  return (
    <section
      className={`card glass ${editing ? 'is-editing' : ''} ${clickable ? 'has-link' : ''} ${failed ? 'has-error' : ''}`}
      data-status={status}
      data-widget={widget.id}
      aria-label={widget.title || widget.kind}
      onClick={clickable ? open : undefined}
    >
      {!bare && (
        <header className="flex items-center gap-2 px-3 pt-2.5 pb-1 min-h-9">
          <ServiceIcon icon={widget.icon} size={18} />
          <h3 className="text-[13px] font-medium truncate flex-1 text-ink/90" title={widget.title}>
            {widget.title}
          </h3>
          {/* Why yellow or red, right on the card; the veil already says it for failures. */}
          {!failed && (status === 'warn' || status === 'bad') && reasons.length > 0 && (
            <span className={`hidden @min-[300px]:inline text-[10px] truncate max-w-[45%] ${status === 'bad' ? 'text-bad' : 'text-warn'}`} title={statusTitle}>
              {reasons[0]}
            </span>
          )}
          <span className="dot" data-status={status} role="img" aria-label={statusTitle} title={statusTitle} />
          {widget.beta && (
            <span className="chip !py-0 text-[10px] cursor-help hidden @min-[240px]:inline-flex" title={t('widget.betaHelp')}>
              beta
            </span>
          )}
        </header>
      )}
      {/* The controls float over the corner: always while editing, on hover otherwise. */}
      {showControls && <div className="card-controls glass">{controls}</div>}
      <div className="flex-1 min-h-0 flex flex-col">
        {renderWidget({ widget, data, series, canAct, canEdit, onAction, link, editing })}
      </div>
      {/* The failure lies over the body: the layout underneath stays as it is, the
          last good values show through, and the red is impossible to miss. */}
      {failed && (
        <div className={`card-error ${bare ? 'inset-0' : 'inset-x-0 bottom-0 top-9'}`} title={String(data?.meta?.hint ?? '')} data-testid="card-error">
          <AlertTriangle size={22} className="text-bad flex-none" aria-hidden="true" />
          <p className="text-[12px] font-medium text-bad leading-snug line-clamp-3">{errorText}</p>
          {data?.meta?.stale_since ? <p className="text-[10px] text-muted">{t('card.staleData')}</p> : null}
        </div>
      )}
    </section>
  )
}

export function CardBody({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`flex-1 min-h-0 px-3 pb-3 ${className}`}>{children}</div>
}
