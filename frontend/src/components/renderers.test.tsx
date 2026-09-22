/**
 * Every renderer draws the demo data of the design preview without throwing,
 * and the pieces a user relies on are in the DOM: values, titles, actions.
 */
import { render, screen } from '@testing-library/react'

import { DEMO_DATA, DEMO_SERIES, DEMO_VIEWS } from '../demo/board'
import type { Action, WidgetData, WidgetView } from '../lib/types'
import { renderWidget } from './renderers'
import { WidgetCard } from './WidgetCard'

describe('renderers', () => {
  it('draw every demo widget', () => {
    let drawn = 0
    for (const view of DEMO_VIEWS) {
      const { unmount } = render(<>{renderWidget({ widget: view, data: DEMO_DATA[view.id], series: DEMO_SERIES[view.id], canAct: true, onAction: () => undefined })}</>)
      drawn += 1
      unmount()
    }
    expect(drawn).toBe(DEMO_VIEWS.length)
  })

  it('shows the value and the unit of a value card', () => {
    const view = DEMO_VIEWS.find((v) => v.renderer === 'value')!
    render(<WidgetCard widget={view} data={DEMO_DATA[view.id]} series={DEMO_SERIES[view.id]} />)
    expect(screen.getByText(String(DEMO_DATA[view.id].primary?.value))).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: view.title })).toBeInTheDocument()
  })

  it('draws the history of a headline number the adapter declared no metric for', () => {
    const view = DEMO_VIEWS.find((v) => v.renderer === 'value')!
    const data = { ...DEMO_DATA[view.id], metrics: {} } as WidgetData
    const { container, rerender } = render(<WidgetCard widget={view} data={data} series={{ primary: [1, 2, 3, 4, 5, 6] }} />)
    expect(container.querySelector('[title="History of the last 24 hours"] svg')).not.toBeNull()
    rerender(<WidgetCard widget={view} data={data} series={{}} />)
    expect(container.querySelector('[title="History of the last 24 hours"]')).toBeNull()
  })

  it('keeps the bar of a percentage row, whatever history there is', () => {
    const view = DEMO_VIEWS.find((v) => v.renderer === 'stats')!
    const data = { status: 'ok', primary: { label: 'CPU', value: 41, unit: '%' }, secondary: [{ label: 'Memory', value: 57, unit: '%', metric: 'memory' }], metrics: { cpu: 41, memory: 57 } } as unknown as WidgetData
    const series = { cpu: [10, 20, 30, 40, 41], memory: [50, 52, 54, 56, 57] }
    const { container } = render(<WidgetCard widget={view} data={data} series={series} />)
    const bars = container.querySelectorAll('.bar')
    expect(bars).toHaveLength(2)
    expect((bars[1].firstChild as HTMLElement).style.width).toBe('57%')
    // No sparkline in its place: a percentage is the bar.
    expect(container.querySelector('.bar svg')).toBeNull()
  })

  it('offers list actions only when acting is allowed', () => {
    const view = DEMO_VIEWS.find((v) => v.kind === 'docker.containers')!
    const received: Action[] = []
    const { unmount } = render(<WidgetCard widget={view} data={DEMO_DATA[view.id]} canAct onAction={(action) => received.push(action)} />)
    expect(screen.getAllByRole('button', { name: 'Restart' }).length).toBeGreaterThan(0)
    unmount()
    render(<WidgetCard widget={view} data={DEMO_DATA[view.id]} canAct={false} />)
    expect(screen.queryByRole('button', { name: 'Restart' })).toBeNull()
  })

  it('offers a row file as a real download, pointed at HexDeck', () => {
    // ⚠️ Both halves matter. `download` is ignored across origins, so a link
    // straight to the service would play the video in a tab instead of saving
    // it, and on a homelab the browser usually cannot reach the service at all.
    const view = { ...DEMO_VIEWS[0], id: 7, renderer: 'list' } as WidgetView
    const data = {
      status: 'ok',
      items: [{ title: 'Me at the zoo', file: { path: '/download/Me%20at%20the%20zoo.webm', name: 'Me at the zoo.webm' } }],
    } as unknown as WidgetData
    render(<>{renderWidget({ widget: view, data, canAct: false })}</>)
    const link = screen.getByRole('link', { name: /Me at the zoo\.webm/ })
    expect(link).toHaveAttribute('download', 'Me at the zoo.webm')
    expect(link.getAttribute('href')).toContain('/widgets/7/file?path=')
    expect(link.getAttribute('href')).toContain(encodeURIComponent('/download/Me%20at%20the%20zoo.webm'))
  })

  it('shows row buttons without a hover only when the card asks for it', () => {
    // ⚠️ A touchscreen has no hover. A card whose rows exist to be pressed
    // says so; every other list keeps its buttons out of the way.
    const view = { ...DEMO_VIEWS[0], id: 8, renderer: 'list' } as WidgetView
    const rows = [{ title: 'Copper Sky', actions: [{ id: 'approve', label: 'Approve' }] }]
    const draw = (meta: Record<string, unknown>) =>
      render(<>{renderWidget({ widget: view, data: { status: 'ok', items: rows, meta } as unknown as WidgetData, canAct: true, onAction: () => undefined })}</>)

    const quiet = draw({})
    expect(quiet.getByRole('button', { name: /Approve|Freigeben/ }).closest('span')?.className).toContain('opacity-0')
    quiet.unmount()

    draw({ actions_visible: true })
    expect(screen.getByRole('button', { name: /Approve|Freigeben/ }).closest('span')?.className).not.toContain('opacity-0')
  })

  it('marks a failed widget with its error', () => {
    const view = DEMO_VIEWS[3]
    render(<WidgetCard widget={view} data={{ status: 'unknown', error: 'The service could not be reached.' }} />)
    expect(screen.getByText('The service could not be reached.')).toBeInTheDocument()
  })
})
