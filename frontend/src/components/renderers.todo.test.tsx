/** The to-do card: ticking, adding and removing write the whole list through the card's own address. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import type { WidgetData, WidgetView } from '../lib/types'
import { TodoCard } from './renderers'

const calls = vi.hoisted(() => ({ post: [] as [string, unknown][] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  post: vi.fn(async (path: string, json: unknown) => {
    calls.post.push([path, json])
    return {}
  }),
}))

const widget = { id: 5, kind: 'core.todo', title: 'Shopping', options: { items: 'Milk\nBread' } } as unknown as WidgetView
const data = {
  status: 'ok',
  items: [
    { id: 0, title: 'Milk', status: 'unknown', done: false },
    { id: 1, title: 'Bread', status: 'ok', done: true },
  ],
  meta: { empty: 'Nothing to do', open: false },
} as unknown as WidgetData
const open = { ...data, meta: { empty: 'Nothing to do', open: true } } as unknown as WidgetData

beforeEach(() => {
  calls.post.length = 0
})

it('ticks an item and sends the whole list', async () => {
  render(<TodoCard widget={widget} data={data} canEdit />)
  fireEvent.click(screen.getByRole('checkbox', { name: 'Milk' }))
  await waitFor(() => expect(calls.post).toEqual([['/widgets/5/todo', { items: [{ title: 'Milk', done: true }, { title: 'Bread', done: true }] }]]))
})

it('adds an item with the Enter key and removes one with its cross', async () => {
  render(<TodoCard widget={widget} data={data} canEdit />)
  const field = screen.getByRole('textbox', { name: 'Add an item' })
  fireEvent.change(field, { target: { value: 'Eggs' } })
  fireEvent.keyDown(field, { key: 'Enter' })
  await waitFor(() => expect((calls.post[0][1] as { items: unknown[] }).items).toHaveLength(3))
  fireEvent.click(screen.getByRole('button', { name: 'Delete: Bread' }))
  await waitFor(() => expect(calls.post).toHaveLength(2))
  expect((calls.post[1][1] as { items: { title: string }[] }).items.map((one) => one.title)).toEqual(['Milk', 'Eggs'])
})

it('is read-only for a viewer unless the card is open to everyone', () => {
  const { rerender } = render(<TodoCard widget={widget} data={data} canEdit={false} />)
  expect(screen.getByRole('checkbox', { name: 'Milk' })).toBeDisabled()
  expect(screen.queryByRole('textbox', { name: 'Add an item' })).toBeNull()
  rerender(<TodoCard widget={widget} data={open} canEdit={false} />)
  expect(screen.getByRole('checkbox', { name: 'Milk' })).toBeEnabled()
  expect(screen.getByRole('textbox', { name: 'Add an item' })).toBeInTheDocument()
})
