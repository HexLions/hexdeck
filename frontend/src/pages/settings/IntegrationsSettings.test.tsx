/**
 * The connection sheet says when the address being entered is already taken.
 *
 * ⚠️ A second Proxmox entered with the address of the first one is refused
 * with a token that is correct. The remark stands under the address before
 * anything is tested, and it is a remark only: two connections to one service
 * are allowed, so Save stays where it is.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuth } from '../../stores/auth'
import { IntegrationsSettings } from './IntegrationsSettings'

const FIELDS = [
  { name: 'url', label: 'URL', type: 'url', required: true, secret: false, default: null, help: '', placeholder: '', options: [] },
  { name: 'token_secret', label: 'Token secret', type: 'password', required: true, secret: true, default: null, help: '', placeholder: '', options: [] },
]
const ADAPTER = { kind: 'proxmox', label: 'Proxmox VE', category: 'virtualisation', description: '', icon: '', beta: false, docs_url: '', needs_integration: true, fields: FIELDS, widgets: [] }
const FIRST = { id: 11, kind: 'proxmox', label: 'Proxmox VE', icon: '', beta: false, name: 'First host', config: { url: 'https://192.0.2.199:8006', token_secret: '********' }, enabled: true, demo: false, admin_only: false, last_ok_at: null, last_error: '', widget_count: 0, created_at: '' }

vi.mock('../../api/client', () => ({
  ApiError: class ApiError extends Error {},
  get: vi.fn(async (path: string) => {
    if (path === '/adapters') return [ADAPTER]
    if (path === '/integrations') return [FIRST]
    if (path === '/integrations/versions') return {}
    return { version: '0', demo: false, public_url: '', update_check: false }
  }),
  post: vi.fn(async () => ({})),
  patch: vi.fn(async () => ({})),
  del: vi.fn(async () => ({})),
}))

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <IntegrationsSettings />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('an address another connection already has', () => {
  beforeEach(() => {
    useAuth.setState({ user: { id: 1, username: 'admin', role: 'admin' } as never })
  })

  it('is remarked on under the field, and goes quiet once the address differs', async () => {
    const user = userEvent.setup()
    show()
    await user.click(await screen.findByRole('button', { name: 'Proxmox VE' }))
    const address = screen.getByLabelText(/^URL/)
    expect(screen.queryByText(/already has this address/)).toBeNull()

    await user.type(address, 'https://192.0.2.199:8006/')
    expect(screen.getByText('The connection “First host” already has this address. Check it if this one is another machine.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()

    await user.clear(address)
    await user.type(address, 'https://192.0.2.198:8006')
    expect(screen.queryByText(/already has this address/)).toBeNull()
  })

  it('is not found in the connection being edited', async () => {
    const user = userEvent.setup()
    show()
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    expect(screen.getByLabelText(/^URL/)).toHaveValue('https://192.0.2.199:8006')
    expect(screen.queryByText(/already has this address/)).toBeNull()
  })
})
