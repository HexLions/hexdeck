/**
 * Settings > Backups says where snapshots are copied off this disk, and
 * whether the last copy worked, so a share that stopped being mounted is
 * noticed before the day the copy is needed.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CopiesLine, type Copies } from './BackupsSettings'

const COPIES: Copies = { folder: '/backup', keep: 14, password_set: true, last: { at: '2026-10-20T03:00:00+00:00', name: 'hexdeck-automatic-0.31.0-2026-10-20_030000-scheduled.zip', error: '' } }

describe('CopiesLine', () => {
  it('names the folder and the last copy', () => {
    render(<CopiesLine copies={COPIES} />)
    expect(screen.getByTestId('copies')).toHaveTextContent('/backup')
    expect(screen.getByTestId('copies')).toHaveTextContent('hexdeck-automatic-0.31.0-2026-10-20_030000-scheduled.zip')
  })

  it('says why the last copy failed', () => {
    render(<CopiesLine copies={{ ...COPIES, last: { at: COPIES.last.at, name: '', error: '/backup is not a folder HexDeck can see. Is the share mounted?' } }} />)
    expect(screen.getByTestId('copies')).toHaveTextContent('Is the share mounted?')
  })

  it('says when there is no password, and when there is no folder at all', () => {
    const { unmount } = render(<CopiesLine copies={{ ...COPIES, password_set: false, last: { at: null, name: '', error: '' } }} />)
    expect(screen.getByTestId('copies')).toHaveTextContent('HEXDECK_BACKUP_COPY_PASSWORD')
    unmount()
    render(<CopiesLine copies={{ ...COPIES, folder: '' }} />)
    expect(screen.getByTestId('copies')).toHaveTextContent('HEXDECK_BACKUP_COPY_DIR')
  })
})
