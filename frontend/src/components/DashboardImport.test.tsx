/** Another dashboard's files become a plan to look over, then a board. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { DashboardImport } from './DashboardImport'

const calls = vi.hoisted(() => ({ post: [] as [string, unknown][] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  post: vi.fn(async (path: string, body: unknown) => {
    calls.post.push([path, body])
    if (path === '/imports/preview') {
      return {
        source: 'homepage', name: 'Homepage', warnings: ['Something Odd: no adapter'],
        connections: [
          { key: 'c1', kind: 'sonarr', label: 'Sonarr', icon: 'sonarr', name: 'Sonarr', config: { url: 'http://sonarr', api_key: 'k' }, missing: [] },
          { key: 'c2', kind: 'pihole', label: 'Pi-hole', icon: 'pihole', name: 'Pi-hole', config: { url: 'http://pi.hole' }, missing: ['password'] },
        ],
        pages: [{ name: 'Media', cards: [
          { key: 'k1', kind: 'sonarr.status', label: 'Status', title: 'Sonarr', icon: 'sonarr', link: '', connection: 'c1', enabled: true },
          { key: 'k2', kind: 'core.app', label: 'App tile', title: 'Sonarr', icon: 'sonarr', link: 'http://sonarr', connection: null, enabled: true },
          { key: 'k3', kind: 'pihole.summary', label: 'Summary', title: 'Pi-hole', icon: 'pihole', link: '', connection: 'c2', enabled: true },
        ] }],
      }
    }
    return { slug: 'homepage' }
  }),
}))

function show() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={['/settings/boards']}>
        <Routes>
          <Route path="/settings/boards" element={<DashboardImport />} />
          <Route path="/b/:slug" element={<p>board page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

it('reads the files into a plan, takes a missing secret and a left-out card, and makes the board', async () => {
  show()
  fireEvent.change(screen.getByLabelText('services.yaml'), { target: { value: '- Media:\n    - Sonarr:\n        href: http://sonarr\n' } })
  fireEvent.click(screen.getByRole('button', { name: 'Read the files' }))
  expect(await screen.findByText('2 connections to make')).toBeInTheDocument()
  expect(calls.post[0]).toEqual(['/imports/preview', { source: 'auto', files: { services: '- Media:\n    - Sonarr:\n        href: http://sonarr\n' } }])
  expect(screen.getByText('Something Odd: no adapter')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Pi-hole: password'), { target: { value: 'app-pass' } })
  fireEvent.click(screen.getAllByRole('checkbox')[1])
  fireEvent.change(screen.getByLabelText('Name of the new board'), { target: { value: 'Lab' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create the board' }))
  await waitFor(() => expect(calls.post).toHaveLength(2))
  const [, body] = calls.post[1] as [string, { plan: { name: string; connections: { config: Record<string, string>; missing: string[] }[]; pages: { cards: { enabled: boolean }[] }[] } }]
  expect(body.plan.name).toBe('Lab')
  expect(body.plan.connections[1].config.password).toBe('app-pass')
  expect(body.plan.connections[1].missing).toEqual([])
  expect(body.plan.pages[0].cards.map((c) => c.enabled)).toEqual([true, false, true])
  expect(await screen.findByText('board page')).toBeInTheDocument()
})
