/** Leaving demo mode asks once, then calls the one address that takes the demo's data along. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { LeaveDemo } from './LeaveDemo'

const calls = vi.hoisted(() => ({ post: [] as string[] }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  post: vi.fn(async (path: string) => {
    calls.post.push(path)
    return { integrations_removed: 7, widgets_removed: 21, boards_removed: 1 }
  }),
}))

it('asks first, then leaves and says what went', async () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={['/system/integrations']}>
        <Routes>
          <Route path="/system/integrations" element={<LeaveDemo />} />
          <Route path="/" element={<p>home</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Leave demo mode' }))
  expect(calls.post).toEqual([])
  expect(screen.getByText(/A board with a real connection keeps everything else/)).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: 'Leave demo mode' }).at(-1)!)
  await waitFor(() => expect(calls.post).toEqual(['/settings/demo/leave']))
  expect(await screen.findByText('home')).toBeInTheDocument()
})
