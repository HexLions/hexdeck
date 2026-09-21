/**
 * The right-hand end of the bar: the bell with its count and the account
 * menu, which holds dark and light as two segments, the language, and keeps
 * the own settings apart from the system. Signed out, the two pills stand in
 * the bar itself.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'

import i18n from '../i18n'
import { HeaderTools, type HeaderUser } from './HeaderTools'

const USER: HeaderUser = { display_name: 'Ada Lovelace', username: 'ada', role: 'admin', avatar_url: null }

function show(user: HeaderUser | null = USER, unread = 0) {
  return render(
    <MemoryRouter>
      <HeaderTools user={user} unread={unread} onNotices={() => undefined} />
    </MemoryRouter>,
  )
}

afterEach(async () => {
  document.documentElement.removeAttribute('data-theme')
  if (i18n.language !== 'en') await i18n.changeLanguage('en')
})

describe('HeaderTools', () => {
  it('counts unread notices on the bell and caps the number', () => {
    const { unmount } = show(USER, 3)
    expect(screen.getByRole('button', { name: 'Notices' })).toHaveTextContent('3')
    unmount()
    show(USER, 42)
    expect(screen.getByRole('button', { name: 'Notices' })).toHaveTextContent('9+')
  })

  it('says which appearance is on, not what a click would do', async () => {
    show()
    await userEvent.click(screen.getByRole('button', { name: 'Ada Lovelace' }))
    const dark = screen.getByRole('button', { name: 'Dark' })
    const light = screen.getByRole('button', { name: 'Light' })
    expect(dark).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(light)
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(light).toHaveAttribute('aria-pressed', 'true')
    expect(dark).toHaveAttribute('aria-pressed', 'false')
  })

  it('opens the account menu with both areas and the way out', async () => {
    show()
    expect(screen.queryByRole('menu')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Ada Lovelace' }))
    const menu = screen.getByRole('menu')
    expect(menu).toHaveTextContent('Administrator')
    expect(screen.getByRole('menuitem', { name: 'My settings' })).toHaveAttribute('href', '/settings')
    expect(screen.getByRole('menuitem', { name: 'System' })).toHaveAttribute('href', '/system')
    expect(screen.getByRole('menuitem', { name: 'Sign out' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('offers a sign-in, not a sign-out, to a browser that was signed in by itself', async () => {
    show({ ...USER, auth_kind: 'auto' })
    await userEvent.click(screen.getByRole('button', { name: 'Ada Lovelace' }))
    expect(screen.getByRole('menuitem', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: 'Sign out' })).toBeNull()
  })

  it('leaves out the account menu where nobody is signed in, and shows the pills instead', () => {
    show(null)
    expect(screen.queryByRole('button', { name: 'Ada Lovelace' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Notices' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Dark' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'English' })).toBeInTheDocument()
  })

  it('keeps the bar to the bell and the account once somebody is signed in', () => {
    show()
    expect(screen.queryByRole('button', { name: 'Dark' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'English' })).toBeNull()
  })

  it('switches the language from the menu and marks the one that is on', async () => {
    show()
    await userEvent.click(screen.getByRole('button', { name: 'Ada Lovelace' }))
    expect(screen.getByRole('button', { name: 'English' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Deutsch' }))
    // The texts of a language are fetched when it is first chosen, so the rest
    // of the bar follows a moment later.
    await screen.findByRole('button', { name: 'Hinweise' })
    expect(i18n.language).toBe('de')
    expect(screen.getByRole('button', { name: 'Deutsch' })).toHaveAttribute('aria-pressed', 'true')
  })
})
