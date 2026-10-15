/**
 * The quiet hours and the daily summary of a channel: what the editor sends.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChannelsSettings } from './ChannelsSettings'

const calls = vi.hoisted(() => ({ posted: [] as { path: string; body: Record<string, unknown> }[] }))

vi.mock('../../lib/push', () => ({
  currentSubscription: vi.fn(async () => null),
  pushSupported: () => false,
  subscribePush: vi.fn(),
  unsubscribePush: vi.fn(),
}))

vi.mock('../../api/client', () => ({
  ApiError: class ApiError extends Error {},
  get: vi.fn(async (path: string) => {
    if (path === '/channel-kinds') return [{ kind: 'ntfy', label: 'ntfy', help: '', fields: [] }]
    if (path === '/events') return [{ event: 'outage', label: 'A service stopped answering' }]
    return []
  }),
  post: vi.fn(async (path: string, body: Record<string, unknown>) => {
    calls.posted.push({ path, body })
    return { id: 9, kind: 'ntfy', name: 'ntfy', config: {}, enabled: true, events: [], last_error: '', created_at: '', schedule: body.schedule }
  }),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
}))

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ChannelsSettings />
    </QueryClientProvider>,
  )
}

describe('the schedule of a channel', () => {
  beforeEach(() => {
    calls.posted.length = 0
  })

  it('sends quiet hours with a night to start from, and the summary time', async () => {
    const user = userEvent.setup()
    show()
    await user.click(await screen.findByRole('button', { name: /Add/ }))
    await user.click(screen.getByRole('switch', { name: 'Quiet hours' }))
    expect(screen.getByLabelText('From')).toHaveValue('22:30')
    expect(screen.getByLabelText('Until')).toHaveValue('07:00')
    await user.type(screen.getByLabelText('Daily summary at'), '08:15')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(calls.posted[0].body.schedule).toEqual({ quiet_from: '22:30', quiet_to: '07:00', quiet_urgent: true, summary_at: '08:15' })
  })

  it('sends no quiet hours while the switch is off', async () => {
    const user = userEvent.setup()
    show()
    await user.click(await screen.findByRole('button', { name: /Add/ }))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(calls.posted[0].body.schedule).toEqual({ quiet_from: '', quiet_to: '', quiet_urgent: true, summary_at: '' })
  })
})
