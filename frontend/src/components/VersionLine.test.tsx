/** The version line: what it says, what it links to, and what it does without an answer. */
import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import type { About } from '../api/types'
import { VersionLine } from './VersionLine'

const answer = vi.hoisted(() => ({ about: null as Partial<About> | null, fail: false }))
vi.mock('../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/client')>()),
  get: vi.fn(async () => {
    if (answer.fail) throw new Error('no answer')
    return answer.about
  }),
}))

beforeEach(() => {
  answer.fail = false
})

it('names the version it is running', async () => {
  answer.about = { version: '0.17.0', latest_version: null, release_url: 'https://example.com/releases' }
  render(<VersionLine />)
  await waitFor(() => expect(screen.getByText('0.17.0')).toBeInTheDocument())
  expect(screen.queryByRole('link')).toBeNull()
})

it('links to the releases when a newer one is out', async () => {
  answer.about = { version: '0.17.0', latest_version: '0.18.0', release_url: 'https://example.com/releases' }
  render(<VersionLine />)
  const link = await screen.findByRole('link')
  expect(link).toHaveAttribute('href', 'https://example.com/releases')
  expect(link.textContent).toContain('0.18.0')
})

it('says nothing while there is no answer, and nothing when there never is', async () => {
  answer.about = {}
  const { container } = render(<VersionLine />)
  expect(container.textContent).toBe('')

  answer.fail = true
  const failed = render(<VersionLine />)
  await waitFor(() => expect(failed.container.textContent).toBe(''))
})
