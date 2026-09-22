/**
 * The notepad writes through an address of its own, which writes the text and
 * nothing else: whoever may edit the board, and, where the card says so,
 * everyone who may see it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import type { WidgetData, WidgetView } from '../lib/types'
import { NotepadCard } from './renderers'

const calls = vi.hoisted(() => ({ post: [] as [string, unknown][] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  post: vi.fn(async (path: string, json: unknown) => {
    calls.post.push([path, json])
    return {}
  }),
}))

const widget = { id: 7, kind: 'notepad.pad', title: 'Rack', options: { content: 'old', mono: false } } as unknown as WidgetView
const data = { status: 'ok', meta: { content: 'old', mono: false } } as unknown as WidgetData
const open = { status: 'ok', meta: { content: 'old', mono: false, open: true } } as unknown as WidgetData

describe('NotepadCard', () => {
  beforeEach(() => {
    calls.post.length = 0
  })

  it('saves what was typed, once the typing pauses', async () => {
    render(<NotepadCard widget={widget} data={data} canEdit />)
    const pad = screen.getByRole('textbox', { name: 'Rack' })
    fireEvent.change(pad, { target: { value: 'old\nnew line' } })
    expect(calls.post).toEqual([])
    await waitFor(() => expect(calls.post).toEqual([['/widgets/7/notepad', { content: 'old\nnew line' }]]), { timeout: 2000 })
  })

  it('saves at once when the writer leaves', async () => {
    render(<NotepadCard widget={widget} data={data} canEdit />)
    const pad = screen.getByRole('textbox', { name: 'Rack' })
    fireEvent.change(pad, { target: { value: 'left' } })
    fireEvent.blur(pad)
    await waitFor(() => expect(calls.post.length).toBe(1))
    expect(calls.post[0]).toEqual(['/widgets/7/notepad', { content: 'left' }])
  })

  it('is read-only without the right to edit the board', () => {
    render(<NotepadCard widget={widget} data={data} canEdit={false} />)
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('old')).toBeInTheDocument()
  })

  it('is written in by a viewer where the card is open to everyone', async () => {
    render(<NotepadCard widget={widget} data={open} canEdit={false} />)
    const pad = screen.getByRole('textbox', { name: 'Rack' })
    fireEvent.change(pad, { target: { value: 'the milk' } })
    fireEvent.blur(pad)
    await waitFor(() => expect(calls.post).toEqual([['/widgets/7/notepad', { content: 'the milk' }]]))
  })
})
