/**
 * System > Card status: the reason behind an unexpected failure is shown to
 * the administrator, and the list can be cut down to the cards with trouble.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import { CardRunsSettings, type CardRun } from './CardRunsSettings'

const now = Date.now() / 1000
const ROWS: CardRun[] = [
  { id: 1, title: 'Sonarr', kind: 'sonarr.queue', board: 'Home', board_slug: 'home', page: 'Main', integration: 'Sonarr', status: 'unknown', error: 'This card could not be read. The server log says why.', code: 'unexpected', started: now - 12, seconds: 0.25, interval: 60, next: now + 48, failures: 2, reason: 'RuntimeError: socket closed' },
  { id: 2, title: 'Weather', kind: 'weather.now', board: 'Home', board_slug: 'home', page: 'Main', integration: '', status: 'ok', error: '', code: '', started: now - 3, seconds: 0.8, interval: 600, next: now + 597, failures: 0, reason: '' },
]

vi.mock('../../api/client', () => ({
  get: vi.fn(async () => ROWS),
}))

function renderPage() {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter>
        <CardRunsSettings />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CardRunsSettings', () => {
  it('shows every card read, with the reason of an unexpected failure', async () => {
    renderPage()
    expect(await screen.findByText('RuntimeError: socket closed')).toBeInTheDocument()
    expect(screen.getAllByTestId('card-run')).toHaveLength(2)
    expect(screen.getByRole('link', { name: 'Weather' })).toHaveAttribute('href', '/b/home')
  })

  it('cuts the list down to the cards with trouble', async () => {
    renderPage()
    await screen.findByText('RuntimeError: socket closed')
    await userEvent.click(screen.getByRole('checkbox'))
    expect(screen.getAllByTestId('card-run')).toHaveLength(1)
    expect(screen.queryByRole('link', { name: 'Weather' })).not.toBeInTheDocument()
  })
})
