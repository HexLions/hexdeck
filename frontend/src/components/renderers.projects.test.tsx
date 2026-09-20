/**
 * The three project cards: a roadmap of milestones, one project's state, and
 * the items to tick off. The items card writes through the projects API,
 * not through card actions, and only for a viewer who may act.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import type { WidgetData, WidgetView } from '../lib/types'
import { ItemsCard, ProjectCard, RoadmapCard } from './renderers'

const calls = vi.hoisted(() => ({ patch: [] as [string, unknown][], put: [] as [string, unknown][], post: [] as [string, unknown][], del: [] as string[] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  get: vi.fn(async () => [{ id: 5, name: 'HexDeck', slug: 'hexdeck', status: 'active', colour: '', position: 0, repos: [], milestones: [], items: [] }]),
  patch: vi.fn(async (path: string, json: unknown) => {
    calls.patch.push([path, json])
    return {}
  }),
  put: vi.fn(async (path: string, json: unknown) => {
    calls.put.push([path, json])
    return {}
  }),
  post: vi.fn(async (path: string, json: unknown) => {
    calls.post.push([path, json])
    return { id: 9, name: (json as { name?: string }).name ?? '', slug: 'new', status: 'active', colour: '', position: 1, repos: [], milestones: [], items: [] }
  }),
  del: vi.fn(async (path: string) => {
    calls.del.push(path)
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
    { id: 7, kind: 'item', title: 'Test the backups', project: 'Homelab', colour: '', date: '2026-09-20', days: 0, status: 'soon', repeat_days: 30 },
  ],
  meta: { today: '2026-09-20', weeks: 4, projects: [{ id: 5, name: 'HexDeck' }, { id: 6, name: 'Garden' }] },
} as unknown as WidgetData

describe('RoadmapCard', () => {
  it('draws every milestone with its grade, the undated ones apart', () => {
    render(<RoadmapCard widget={widget} data={roadmap} canAct={false} />)
    expect(screen.getAllByText('Late')[0].closest('[data-grade]')?.getAttribute('data-grade')).toBe('late')
    expect(screen.getAllByText('Soon')[0].closest('[data-grade]')?.getAttribute('data-grade')).toBe('soon')
    expect(screen.getByText('Someday').closest('[data-undated]')).toBeTruthy()
    expect(screen.getByText('1')).toBeInTheDocument()
  })

  it('adds a milestone and ticks one off when the viewer may act', async () => {
    calls.post.length = 0
    calls.patch.length = 0
    render(<RoadmapCard widget={widget} data={roadmap} canAct />)
    fireEvent.change(screen.getByLabelText('New milestone'), { target: { value: 'Ship it' } })
    fireEvent.change(screen.getByLabelText('Target date'), { target: { value: '2026-10-15' } })
    fireEvent.change(screen.getByLabelText('Pick a project'), { target: { value: '6' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add milestone' }))
    await waitFor(() => expect(calls.post).toEqual([['/projects/6/milestones', { title: 'Ship it', target_date: '2026-10-15' }]]))
    fireEvent.click(screen.getByRole('button', { name: /Mark as done: Soon/ }))
    await waitFor(() => expect(calls.patch).toEqual([['/milestones/2', { status: 'done' }]]))
  })

  it('lists a dated item among the milestones and ticks a recurring one for now', async () => {
    calls.patch.length = 0
    render(<RoadmapCard widget={widget} data={roadmap} canAct />)
    const legend = screen.getByRole('button', { name: 'Done for now: Test the backups' })
    expect(legend.getAttribute('data-kind')).toBe('item')
    expect(legend.textContent).toContain('today')
    expect(legend.textContent).toContain('every 30 days')
    fireEvent.click(legend)
    await waitFor(() => expect(calls.patch).toEqual([['/items/7', { status: 'done' }]]))
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

  it('offers to pick or create a project when the card has none', async () => {
    calls.post.length = 0
    calls.patch.length = 0
    const data = { status: 'warn', meta: { empty: 'Pick a project in the card settings' } } as unknown as WidgetData
    render(<ProjectCard widget={widget} data={data} canEdit />)
    await screen.findByRole('option', { name: 'HexDeck' })
    fireEvent.change(screen.getByLabelText('Name of a new project'), { target: { value: 'Garden' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))
    await waitFor(() => expect(calls.post).toEqual([['/projects', { name: 'Garden' }]]))
    await waitFor(() => expect(calls.patch).toEqual([['/widgets/1', { options: { project: '9' } }]]))
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
      { id: 4, title: 'Test the backups', notes: '', status: 'todo', milestone: '', issue: '', url: '', due_on: '2026-09-18', days: -2, repeat_days: 30, due: 'late' },
    ],
    meta: { project_id: 5, name: 'HexDeck' },
  } as unknown as WidgetData

  beforeEach(() => {
    calls.patch.length = 0
    calls.put.length = 0
    calls.post.length = 0
    calls.del.length = 0
  })

  it('cycles the state of an item through the API when the viewer may act', async () => {
    render(<ItemsCard widget={widget} data={data} canAct />)
    fireEvent.click(screen.getByRole('button', { name: 'Doing: Items card' }))
    await waitFor(() => expect(calls.patch).toEqual([['/items/2', { status: 'done' }]]))
  })

  it('adds, renames and deletes items from the card', async () => {
    render(<ItemsCard widget={widget} data={data} canAct />)
    fireEvent.change(screen.getByLabelText('Add an item'), { target: { value: 'Docs page' } })
    fireEvent.keyDown(screen.getByLabelText('Add an item'), { key: 'Enter' })
    await waitFor(() => expect(calls.post).toEqual([['/projects/5/items', { title: 'Docs page' }]]))
    fireEvent.click(screen.getByRole('button', { name: 'Docs' }))
    const field = screen.getByLabelText('Click to rename')
    fireEvent.change(field, { target: { value: 'Documentation' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    await waitFor(() => expect(calls.patch).toEqual([['/items/3', { title: 'Documentation' }]]))
    fireEvent.click(screen.getByRole('button', { name: 'Delete: Roadmap card' }))
    await waitFor(() => expect(calls.del).toEqual(['/items/1']))
  })

  it('reports a new order after a drop', async () => {
    render(<ItemsCard widget={widget} data={data} canAct />)
    const rows = screen.getAllByRole('listitem')
    fireEvent.dragStart(rows[2])
    fireEvent.dragOver(rows[0])
    fireEvent.drop(rows[0])
    await waitFor(() => expect(calls.put).toEqual([['/projects/5/items/order', { ids: [3, 1, 2, 4] }]]))
  })

  it('says when an item is due and lets the viewer who may act change the date and the interval', async () => {
    render(<ItemsCard widget={widget} data={data} canAct />)
    const due = screen.getByRole('button', { name: 'Due: Test the backups' })
    expect(due.textContent).toContain('2 days late')
    expect(due.textContent).toContain('every 30 days')
    expect(due.querySelector('[data-due]')?.getAttribute('data-due')).toBe('late')
    fireEvent.click(due)
    fireEvent.change(screen.getByLabelText('Due'), { target: { value: '2026-10-01' } })
    fireEvent.change(screen.getByLabelText('Every N days, 0 for once'), { target: { value: '7' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(calls.patch).toEqual([['/items/4', { due_on: '2026-10-01', repeat_days: 7 }]]))
    // A row without a date offers to set one, and an emptied date clears it.
    fireEvent.click(screen.getByRole('button', { name: 'Set a due date: Docs' }))
    fireEvent.change(screen.getByLabelText('Due'), { target: { value: '2026-10-02' } })
    fireEvent.keyDown(screen.getByLabelText('Due'), { key: 'Enter' })
    await waitFor(() => expect(calls.patch[1]).toEqual(['/items/3', { due_on: '2026-10-02' }]))
  })

  it('draws the marks inert without the right to act', () => {
    render(<ItemsCard widget={widget} data={data} canAct={false} />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText(/2 days late/).getAttribute('data-due')).toBe('late')
    expect(screen.getByRole('link', { name: /#7/ })).toHaveAttribute('href', 'https://github.com/HexLions/hexdeck/issues/7')
  })
})
