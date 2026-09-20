/**
 * The grid of a board is changed through its own endpoint, never through
 * the look's save: the endpoint rescales every page with it. Going to fewer
 * columns rounds positions, so the sheet asks first.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import type { BoardWithLive } from '../api/types'
import { BoardSettingsSheet } from './BoardSettingsSheet'

const BOARD: BoardWithLive = {
  id: 1, slug: 'lab', name: 'Lab', icon: '', owner_id: 1,
  background: { kind: 'bundled', value: 'aurora' },
  settings: { columns: 24 },
  provisioned: false, permission: 'owner',
  pages: [{ id: 10, name: 'Overview', slug: 'overview', icon: '', position: 0, layouts: { lg: [], md: [], sm: [] }, widgets: [] }],
  live: {},
}

function answering() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/boards/lab/columns') && init?.method === 'PUT') {
      return new Response(JSON.stringify({ columns: 12, pages: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    return new Response('[]', { status: 200, headers: { 'content-type': 'application/json' } })
  })
}

async function openLook() {
  const fetch = answering()
  vi.stubGlobal('fetch', fetch)
  const changed = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <BoardSettingsSheet open board={BOARD} boards={[]} canEdit onClose={() => undefined} onChanged={changed} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: /^(Look|Aussehen)$/ }))
  return { user, fetch, changed }
}

describe('BoardSettingsSheet grid', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('asks before shrinking the grid and then calls the endpoint', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const { user, fetch, changed } = await openLook()
    await user.selectOptions(document.querySelector('#board-columns') as HTMLSelectElement, '12')
    expect(confirm).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(changed).toHaveBeenCalled())
    const call = fetch.mock.calls.find(([input]) => String(input).endsWith('/boards/lab/columns'))
    expect(call).toBeDefined()
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ columns: 12 })
  })

  it('does nothing when the person says no', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { user, fetch } = await openLook()
    await user.selectOptions(document.querySelector('#board-columns') as HTMLSelectElement, '12')
    expect(fetch.mock.calls.some(([input]) => String(input).endsWith('/boards/lab/columns'))).toBe(false)
  })

  it('grows without asking', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const { user, fetch } = await openLook()
    await user.selectOptions(document.querySelector('#board-columns') as HTMLSelectElement, '36')
    await waitFor(() => expect(fetch.mock.calls.some(([input]) => String(input).endsWith('/boards/lab/columns'))).toBe(true))
    expect(confirm).not.toHaveBeenCalled()
  })
})
