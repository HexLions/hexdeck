/**
 * The template picker: tiles, then the placeholders of the chosen one mapped
 * to the connections this installation has, then one call that makes the board.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { TemplatePicker } from './TemplatePicker'

const calls = vi.hoisted(() => ({ post: [] as [string, unknown][] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  get: vi.fn(async (path: string) => {
    const media = {
      id: 'media', name: 'Media stack', description: 'Plex with the *arr family.', tags: ['media'], icon: 'clapperboard', cards: 11, pages: 1,
      integrations: [
        { name: 'Plex', kind: 'plex', label: 'Plex', icon: 'plex' },
        { name: 'Sonarr', kind: 'sonarr', label: 'Sonarr', icon: 'sonarr' },
      ],
    }
    if (path === '/templates') return [media, { id: 'projects', name: 'Projects', description: 'The roadmap.', tags: ['projects'], icon: 'map', cards: 5, pages: 1, integrations: [] }]
    if (path === '/templates/media') return { ...media, available: [{ id: 7, name: 'Living room', kind: 'plex' }, { id: 8, name: 'Bedroom', kind: 'plex' }] }
    throw new Error(path)
  }),
  post: vi.fn(async (path: string, body: unknown) => {
    calls.post.push([path, body])
    return { slug: 'cinema' }
  }),
}))

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/settings/boards']}>
        <Routes>
          <Route path="/settings/boards" element={<TemplatePicker />} />
          <Route path="/b/:slug" element={<p>board page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

it('lists the templates and makes a board from one with its connections mapped', async () => {
  show()
  fireEvent.click(await screen.findByRole('button', { name: /Media stack/ }))
  const plex = await screen.findByLabelText('Plex')
  expect(plex).toHaveValue('7')
  expect(screen.getByLabelText('Sonarr')).toHaveValue('')
  expect(screen.getByLabelText('Name of the new board')).toHaveValue('Media stack')
  fireEvent.change(plex, { target: { value: '8' } })
  fireEvent.change(screen.getByLabelText('Name of the new board'), { target: { value: 'Cinema' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create the board' }))
  await waitFor(() => expect(calls.post).toEqual([['/templates/media', { name: 'Cinema', connections: { Plex: 8, Sonarr: null } }]]))
  expect(await screen.findByText('board page')).toBeInTheDocument()
})
