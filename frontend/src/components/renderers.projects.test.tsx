/**
 * The three project cards: a roadmap of milestones, one project's state, and
 * the items to tick off. The items card writes through the projects API,
 * not through card actions, and only for a viewer who may act.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import type { WidgetData, WidgetView } from '../lib/types'
import { ItemsCard, ProjectCard, RoadmapCard } from './renderers'

const calls = vi.hoisted(() => ({ patch: [] as [string, unknown][], put: [] as [string, unknown][] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  patch: vi.fn(async (path: string, json: unknown) => {
    calls.patch.push([path, json])
    return {}
  }),
  put: vi.fn(async (path: string, json: unknown) => {
    calls.put.push([path, json])
    return {}
  }),
}))

const widget = { id: 1, kind: 'projects.roadmap', title: 'Roadmap', options: {} } as unknown as WidgetView
const roadmap = {
  status: 'bad', primary: { label: 'Due in 4 weeks', value: 1 },
  items: [
    { id: 1, title: 'Late', project: 'HexDeck', colour: '#3aa0ff', date: '2026-09-17', days: -3, status: 'late' },
    { id: 2, title: 'Soon', project: 'HexDeck', colour: '#3aa0ff', date: '2026-09-30', days: 10, status: 'soon' },
    { id: 3, title: 'Far', project: 'Garden', colour: '', date: '2026-11-19', days: 60, status: 'open' },
    { id: 4, title: 'Someday', project: 'HexDeck', colour: '', date: null, days: null, status: 'open' },
  ],
  meta: { today: '2026-09-20', weeks: 4 },
} as unknown as WidgetData

describe('RoadmapCard', () => {
  it('draws every milestone with its grade, the undated ones apart', () => {
    render(<RoadmapCard widget={widget} data={roadmap} canAct={false} />)
    expect(screen.getAllByText('Late')[0].closest('[data-grade]')?.getAttribute('data-grade')).toBe('late')
    expect(screen.getAllByText('Soon')[0].closest('[data-grade]')?.getAttribute('data-grade')).toBe('soon')
    expect(screen.getByText('Someday').closest('[data-undated]')).toBeTruthy()
    expect(screen.getByText('1')).toBeInTheDocument()
  })
})

describe('ProjectCard', () => {
  it('shows the state, the next milestone, the completion and the repositories', () => {
    const data = {
      status: 'ok', primary: { label: 'Done', value: 33, unit: '%' },
      secondary: [{ label: 'Items', value: '1 / 3' }, { label: 'Milestones open', value: 2 }],
      items: [{ repo: 'HexLions/hexdeck', url: 'https://github.com/HexLions/hexdeck' }],
      meta: { name: 'HexDeck', colour: '#3aa0ff', status: 'active', next: { title: 'Soon', date: '2026-09-30', days: 10, status: 'soon' } },
    } as unknown as WidgetData
    render(<ProjectCard widget={widget} data={data} canAct={false} />)
    expect(screen.getByText('HexDeck')).toBeInTheDocument()
    expect(screen.getByText('Soon')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'HexLions/hexdeck' })).toHaveAttribute('href', 'https://github.com/HexLions/hexdeck')
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '33')
  })

  it('leaves the repository row out when there is none', () => {
    const data = { status: 'ok', primary: { label: 'Done', value: 0, unit: '%' }, secondary: [], items: [], meta: { name: 'Garden', colour: '', status: 'paused', next: null } } as unknown as WidgetData
    render(<ProjectCard widget={widget} data={data} canAct={false} />)
    expect(screen.queryByRole('link')).toBeNull()
    expect(screen.getByText('Paused')).toBeInTheDocument()
  })
})

describe('ItemsCard', () => {
  const data = {
    status: 'ok',
    items: [
      { id: 1, title: 'Roadmap card', notes: '', status: 'done', milestone: 'M3', issue: '', url: '' },
      { id: 2, title: 'Items card', notes: '', status: 'doing', milestone: 'M3', issue: 'HexLions/hexdeck#7', url: 'https://github.com/HexLions/hexdeck/issues/7' },
      { id: 3, title: 'Docs', notes: '', status: 'todo', milestone: '', issue: '', url: '' },
    ],
    meta: { project_id: 5, name: 'HexDeck' },
  } as unknown as WidgetData

  beforeEach(() => {
    calls.patch.length = 0
    calls.put.length = 0
  })

  it('cycles the state of an item through the API when the viewer may act', async () => {
    render(<ItemsCard widget={widget} data={data} canAct />)
    fireEvent.click(screen.getByRole('button', { name: /Items card/ }))
    await waitFor(() => expect(calls.patch).toEqual([['/items/2', { status: 'done' }]]))
  })

  it('reports a new order after a drop', async () => {
    render(<ItemsCard widget={widget} data={data} canAct />)
    const rows = screen.getAllByRole('listitem')
    fireEvent.dragStart(rows[2])
    fireEvent.dragOver(rows[0])
    fireEvent.drop(rows[0])
    await waitFor(() => expect(calls.put).toEqual([['/projects/5/items/order', { ids: [3, 1, 2] }]]))
  })

  it('draws the marks inert without the right to act', () => {
    render(<ItemsCard widget={widget} data={data} canAct={false} />)
    expect(screen.queryByRole('button', { name: /Items card/ })).toBeNull()
    expect(screen.getByRole('link', { name: /#7/ })).toHaveAttribute('href', 'https://github.com/HexLions/hexdeck/issues/7')
  })
})
