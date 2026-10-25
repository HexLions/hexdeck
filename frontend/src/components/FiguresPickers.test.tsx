/**
 * How a tile is chosen for key figures: the picker in its settings offers
 * cards that show a number and nothing else.
 */
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { FieldSpec } from '../api/types'
import { DEMO_VIEWS } from '../demo/board'
import { useEditedPage } from '../lib/groups'
import type { WidgetView } from '../lib/types'
import { FieldInput } from './FieldInput'

afterEach(() => useEditedPage.setState({ widgets: [] }))

describe('the picker in the settings of key figures', () => {
  const view = (id: number, title: string, renderer: string, extra: Partial<WidgetView> = {}) =>
    ({ ...DEMO_VIEWS[0], id, title, renderer, kind: `demo.${renderer}`, options: {}, ...extra }) as WidgetView
  const spec = (helper: string) => ({ name: 'cards', label: 'Tiles', type: 'cards', required: false, secret: false, default: [], help: '', placeholder: '', options: [], helper }) as FieldSpec

  it('lists the number cards of the page only', () => {
    useEditedPage.setState({ widgets: [view(1, 'CPU', 'stats'), view(2, 'Top domains', 'list'), view(3, 'Clock', 'clock', { client_only: true }), view(4, 'Wall', 'figures', { kind: 'core.figures' })] })
    render(<FieldInput spec={spec('numbers')} value={[]} onChange={() => undefined} />)
    expect(screen.getByRole('checkbox', { name: 'CPU' })).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
  })

  it('lists every card that is no holder for a group', () => {
    useEditedPage.setState({ widgets: [view(1, 'CPU', 'stats'), view(2, 'Top domains', 'list'), view(4, 'Wall', 'figures', { kind: 'core.figures' })] })
    render(<FieldInput spec={spec('')} value={[]} onChange={() => undefined} />)
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
  })
})
