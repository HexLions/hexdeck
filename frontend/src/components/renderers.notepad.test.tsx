/**
 * The notepad writes into the card's own options, so it saves through the
 * widget patch and only for a viewer who may edit the board.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import type { WidgetData, WidgetView } from '../lib/types'
import { NotepadCard } from './renderers'

const calls = vi.hoisted(() => ({ patch: [] as [string, unknown][] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  patch: vi.fn(async (path: string, json: unknown) => {
    calls.patch.push([path, json])
    return {}
  }),
}))

const widget = { id: 7, kind: 'notepad.pad', title: 'Rack', options: { content: 'old', mono: false } } as unknown as WidgetView
const data = { status: 'ok', meta: { content: 'old', mono: false } } as unknown as WidgetData

describe('NotepadCard', () => {
  beforeEach(() => {
    calls.patch.length = 0
  })

  it('saves what was typed, once the typing pauses', async () => {
    render(<NotepadCard widget={widget} data={data} canEdit />)
    const pad = screen.getByRole('textbox', { name: 'Rack' })
    fireEvent.change(pad, { target: { value: 'old\nnew line' } })
    expect(calls.patch).toEqual([])
    await waitFor(() => expect(calls.patch).toEqual([['/widgets/7', { options: { content: 'old\nnew line', mono: false } }]]), { timeout: 2000 })
  })

  it('saves at once when the writer leaves', async () => {
    render(<NotepadCard widget={widget} data={data} canEdit />)
    const pad = screen.getByRole('textbox', { name: 'Rack' })
    fireEvent.change(pad, { target: { value: 'left' } })
    fireEvent.blur(pad)
    await waitFor(() => expect(calls.patch.length).toBe(1))
    expect(calls.patch[0][1]).toEqual({ options: { content: 'left', mono: false } })
  })

  it('is read-only without the right to edit the board', () => {
    render(<NotepadCard widget={widget} data={data} canEdit={false} />)
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('old')).toBeInTheDocument()
  })
})
