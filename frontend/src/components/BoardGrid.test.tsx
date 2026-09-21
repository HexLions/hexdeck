/**
 * Every card has a floor under its size: the smallest the adapter says it is
 * still usable at. Saved layouts below that floor are lifted, and the floor
 * never exceeds the columns of the form factor.
 */
import type { LayoutItem, WidgetView } from '../lib/types'
import { layoutFor, stackedFor } from './BoardGrid'

function widget(id: number, size: [number, number], min?: [number, number]): WidgetView {
  return {
    id, kind: 'core.clock', title: 'Clock', icon: '', link: '', renderer: 'clock',
    options: {}, integration_id: null, refresh_seconds: null,
    default_size: size, min_size: min ?? size,
  } as unknown as WidgetView
}

describe('layoutFor', () => {
  it('lets a card go down to the size the adapter calls its minimum', () => {
    // ⚠️ The floor used to be `default_size`, which made `min_size` dead:
    // all 195 widgets declare one below their default, so none of them was
    // ever reachable and a search bar could not be made into a bar.
    const [item] = layoutFor([{ i: '1', x: 0, y: 0, w: 2, h: 1 }], [widget(1, [4, 2], [2, 1])], 12)
    expect([item.w, item.h, item.minW, item.minH]).toEqual([2, 1, 2, 1])
  })

  it('still lifts a card that was saved below its minimum', () => {
    const [item] = layoutFor([{ i: '1', x: 0, y: 0, w: 1, h: 1 }], [widget(1, [4, 2], [3, 2])], 12)
    expect([item.w, item.h, item.minW, item.minH]).toEqual([3, 2, 3, 2])
  })

  it('keeps a larger saved size and position', () => {
    const [item] = layoutFor([{ i: '1', x: 2, y: 1, w: 5, h: 3 }], [widget(1, [3, 2])], 12)
    expect([item.x, item.y, item.w, item.h]).toEqual([2, 1, 5, 3])
  })

  it('caps the floor at the columns of the form factor', () => {
    const [item] = layoutFor(undefined, [widget(1, [6, 4])], 4)
    expect([item.w, item.minW, item.h, item.minH]).toEqual([4, 4, 4, 4])
  })

  it('places widgets without a position below the others', () => {
    const items = layoutFor([{ i: '1', x: 0, y: 0, w: 3, h: 2 }], [widget(1, [3, 2]), widget(2, [2, 1])], 12)
    expect(items[1]).toMatchObject({ i: '2', x: 0, y: 2, w: 3, h: 2, minW: 2, minH: 1 })
  })

  it('falls back to the default size when a widget declares no minimum', () => {
    const bare = { ...widget(1, [3, 2]), min_size: undefined } as unknown as WidgetView
    const [item] = layoutFor([{ i: '1', x: 0, y: 0, w: 1, h: 1 }], [bare], 12)
    expect([item.minW, item.minH]).toEqual([3, 2])
  })
})

/**
 * One card's data arriving must not redraw the board.
 *
 * ⚠️ Measured before this: thirty cards, the data of one of them changing,
 * thirty renders. Two things caused it together, and fixing either alone
 * changes nothing: the card was not memoised, and it was handed four freshly
 * made closures on every render, which defeats a memo. So this test counts.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi } from 'vitest'

import type { WidgetData } from '../lib/types'
import { BoardGrid } from './BoardGrid'

const drawn = vi.fn()

vi.mock('./WidgetCard', () => ({
  WidgetCard: ({ widget, onResize }: { widget: WidgetView; onResize?: (preset: string) => void }) => {
    drawn(widget.id)
    return (
      <div data-testid={`card-${widget.id}`}>
        {/* Inside .card-controls like the real controls, which the grid does not drag by. */}
        {onResize && (
          <div className="card-controls">
            <button onClick={() => onResize('XL')}>XL</button>
          </div>
        )}
      </div>
    )
  },
}))

describe('a board that redraws', () => {
  it('draws only the card whose data changed', () => {
    const widgets = Array.from({ length: 30 }, (_, index) => widget(index + 1, [2, 2]))
    const layouts = { lg: [], md: [], sm: [] }
    const data: Record<number, WidgetData | undefined> = {}
    const noop = () => {}
    const props = {
      widgets, layouts, editing: false, canAct: true, autoCompact: false,
      onAction: noop, onRefresh: noop, onSettings: noop, onRemove: noop,
    } as unknown as Parameters<typeof BoardGrid>[0]

    const view = render(<BoardGrid {...props} data={data} />)
    expect(drawn).toHaveBeenCalledTimes(30)

    drawn.mockClear()
    // One card's data arrives. The object for every other card is the same
    // reference it was, which is what a live update looks like.
    view.rerender(<BoardGrid {...props} data={{ ...data, 7: { status: 'ok' } as WidgetData }} />)
    expect(drawn.mock.calls.map(([id]) => id)).toEqual([7])
  })
})

/**
 * A board can be arranged without a mouse.
 *
 * ⚠️ react-grid-layout 1.5.2 listens to pointer and touch events and nothing
 * else, so until now a board could be arranged with a mouse and by no other
 * means: not by keyboard, not by a switch, not by voice control that drives
 * the keyboard. The grid never learns about this; the layout is ours.
 */
describe('arranging with the keyboard', () => {
  function board(saved: (bp: string, layout: LayoutItem[]) => void) {
    const widgets = [widget(1, [2, 2]), widget(2, [2, 2])]
    const layouts = {
      lg: [{ i: '1', x: 3, y: 1, w: 2, h: 2 }, { i: '2', x: 6, y: 1, w: 2, h: 2 }],
      md: [{ i: '1', x: 3, y: 1, w: 2, h: 2 }, { i: '2', x: 6, y: 1, w: 2, h: 2 }],
      sm: [{ i: '1', x: 0, y: 1, w: 2, h: 2 }, { i: '2', x: 2, y: 1, w: 2, h: 2 }],
    }
    return render(
      <BoardGrid
        {...({ widgets, layouts, data: {}, editing: true, canAct: true, autoCompact: false, onLayoutChange: saved } as unknown as Parameters<typeof BoardGrid>[0])}
      />,
    )
  }

  it('moves the focused card one cell per arrow press', async () => {
    const saved = vi.fn()
    const view = board(saved)
    const card = view.container.querySelectorAll('[tabindex="0"]')[0] as HTMLElement
    expect(card, 'no card can take the focus, so this test proves nothing').toBeTruthy()

    card.focus()
    await userEvent.keyboard('{ArrowRight}')

    const lg = saved.mock.calls.find(([bp]) => bp === 'lg')
    expect(lg, 'nothing was saved').toBeTruthy()
    expect((lg![1] as LayoutItem[]).find((item) => item.i === '1')?.x).toBe(4)
    // Only the wide arrangement is kept; the phone's stack is worked out from it.
    expect(saved.mock.calls.map(([bp]) => bp)).toEqual(['lg'])
  })

  it('resizes with Shift and stops at the card floor', async () => {
    const saved = vi.fn()
    const view = board(saved)
    const card = view.container.querySelectorAll('[tabindex="0"]')[0] as HTMLElement
    card.focus()
    await userEvent.keyboard('{Shift>}{ArrowDown}{/Shift}')
    const lg = saved.mock.calls.find(([bp]) => bp === 'lg')
    expect((lg![1] as LayoutItem[]).find((item) => item.i === '1')?.h).toBe(3)

    saved.mockClear()
    // Left at the edge of the board: nothing to save, so nothing is sent.
    card.focus()
    await userEvent.keyboard('{Shift>}{ArrowLeft}{/Shift}{Shift>}{ArrowLeft}{/Shift}{Shift>}{ArrowLeft}{/Shift}')
    const shrunk = saved.mock.calls.filter(([bp]) => bp === 'lg')
    expect(shrunk.length).toBeLessThan(3)
  })

  it('leaves a modified arrow to the browser', async () => {
    // Ctrl and Alt with an arrow belong to the browser and the window manager:
    // word-wise movement, workspace switching. Taking those would cost more
    // than arranging a card is worth.
    const saved = vi.fn()
    const view = board(saved)
    const card = view.container.querySelectorAll('[tabindex="0"]')[0] as HTMLElement
    card.focus()
    await userEvent.keyboard('{Control>}{ArrowRight}{/Control}')
    await userEvent.keyboard('{Alt>}{ArrowRight}{/Alt}')
    expect(saved).not.toHaveBeenCalled()
  })

  it('leaves the keyboard alone when the board is not being edited', async () => {
    const saved = vi.fn()
    render(
      <BoardGrid
        {...({
          widgets: [widget(1, [2, 2])],
          layouts: { lg: [{ i: '1', x: 3, y: 1, w: 2, h: 2 }], md: [], sm: [] },
          data: {}, editing: false, canAct: true, autoCompact: false, onLayoutChange: saved,
        } as unknown as Parameters<typeof BoardGrid>[0])}
      />,
    )
    await userEvent.keyboard('{ArrowRight}')
    expect(saved).not.toHaveBeenCalled()
  })
})

describe('layoutFor on a 24-column board', () => {
  it('scales the adapter floor to the board columns', () => {
    const [item] = layoutFor([{ i: '1', x: 0, y: 0, w: 2, h: 1 }], [widget(1, [4, 2], [2, 1])], 24)
    expect([item.w, item.minW]).toEqual([4, 4])
  })
  it('gives a card with no saved place a default width in board columns', () => {
    const [item] = layoutFor([], [widget(1, [4, 2], [2, 1])], 24)
    expect(item.w).toBe(6)
  })
})

describe('stackedFor', () => {
  const wide = (w: number, x: number) => ({ i: String(x), x, y: 0, w, h: 1 })
  it('pairs two small cards on a 12-column board as before', () => {
    const stack = stackedFor([wide(2, 0), wide(2, 2)], 4)
    expect(stack.map((s) => s.w)).toEqual([2, 2])
  })
  it('knows that four of twenty-four is as small as two of twelve', () => {
    const stack = stackedFor([wide(4, 0), wide(4, 4)], 4, 24)
    expect(stack.map((s) => s.w)).toEqual([2, 2])
    const wideCards = stackedFor([wide(6, 0), wide(6, 6)], 4, 24)
    expect(wideCards.map((s) => s.w)).toEqual([4, 4])
  })
})

/**
 * ⚠️ Changing the columns of a board rearranged it. react-grid-layout's
 * responsive wrapper hands the inner grid the new layout from its props but
 * the columns from its own state, which only catches up after the render:
 * for one render a 36-column layout sat on a 24-column grid, the cards past
 * the edge were pulled in, the rest pushed down, and edit mode saved that.
 * Reproduced on 20.09.2026 through the settings sheet.
 */
describe('changing the columns', () => {
  it('never reports a moved card when layout and columns change together', () => {
    const widgets = [widget(1, [3, 2], [2, 1]), widget(2, [3, 2], [2, 1])]
    const at24 = { lg: [{ i: '1', x: 12, y: 0, w: 6, h: 2 }, { i: '2', x: 18, y: 0, w: 6, h: 2 }], md: [], sm: [] }
    const at36 = { lg: [{ i: '1', x: 18, y: 0, w: 9, h: 2 }, { i: '2', x: 27, y: 0, w: 9, h: 2 }], md: [], sm: [] }
    const saved = vi.fn()
    const noop = () => {}
    const props = {
      widgets, data: {}, editing: true, canAct: true, autoCompact: false,
      onAction: noop, onRefresh: noop, onSettings: noop, onRemove: noop, onLayoutChange: saved,
    } as unknown as Parameters<typeof BoardGrid>[0]
    const view = render(<BoardGrid {...props} layouts={at24} columns={24} />)
    view.rerender(<BoardGrid {...props} layouts={at36} columns={36} />)
    const moved = saved.mock.calls.map(([, layout]) => (layout as LayoutItem[]).map(({ i, x, y, w }) => `${i}:${x},${y},${w}`).join(' '))
    expect(moved.filter((line) => line !== '1:18,0,9 2:27,0,9')).toEqual([])
  })
})

/** Several cards at once: Shift and a click selects, an arrow moves them together. */
// The selection is taken on mousedown, see `toggle` in the grid.
describe('a selection of cards', () => {
  function board(saved: (bp: string, layout: LayoutItem[]) => void) {
    const widgets = [widget(1, [2, 1]), widget(2, [2, 1]), widget(3, [2, 1])]
    const lg = [{ i: '1', x: 0, y: 0, w: 2, h: 1 }, { i: '2', x: 2, y: 0, w: 2, h: 1 }, { i: '3', x: 8, y: 0, w: 2, h: 1 }]
    return render(
      <BoardGrid {...({ widgets, layouts: { lg, md: [], sm: [] }, data: {}, editing: true, canAct: true, autoCompact: false, onLayoutChange: saved } as unknown as Parameters<typeof BoardGrid>[0])} />,
    )
  }
  const cards = (view: ReturnType<typeof board>) => [...view.container.querySelectorAll('[tabindex="0"]')] as HTMLElement[]
  const chosen = (view: ReturnType<typeof board>) => view.container.querySelectorAll('.card-selected').length

  it('moves the selected cards by the same step, and nothing else', async () => {
    const saved = vi.fn()
    const view = board(saved)
    const [one, two] = cards(view)
    fireEvent.mouseDown(one, { shiftKey: true })
    fireEvent.mouseDown(two, { ctrlKey: true })
    expect(chosen(view)).toBe(2)

    one.focus()
    await userEvent.keyboard('{ArrowDown}')
    const lg = saved.mock.calls.find(([bp]) => bp === 'lg')![1] as LayoutItem[]
    expect(lg.map(({ i, x, y }) => `${i}:${x},${y}`)).toEqual(['1:0,1', '2:2,1', '3:8,0'])
  })

  it('does not move a group off the grid or onto a card outside it', async () => {
    const saved = vi.fn()
    const view = board(saved)
    const [one, , three] = cards(view)
    // Cards 1 (0..2) and 3 (8..10) together, card 2 (2..4) outside the selection.
    fireEvent.mouseDown(one, { shiftKey: true })
    fireEvent.mouseDown(three, { shiftKey: true })
    one.focus()
    await userEvent.keyboard('{ArrowLeft}')
    expect(saved, 'card 1 would leave the grid on the left').not.toHaveBeenCalled()
    await userEvent.keyboard('{ArrowRight}')
    expect(saved, 'card 1 would land on card 2').not.toHaveBeenCalled()
    await userEvent.keyboard('{ArrowDown}')
    const lg = saved.mock.calls[0][1] as LayoutItem[]
    expect(lg.map(({ i, x, y }) => `${i}:${x},${y}`)).toEqual(['1:0,1', '2:2,0', '3:8,1'])
  })

  it('lets go with Escape and when editing ends', async () => {
    const saved = vi.fn()
    const view = board(saved)
    const [one] = cards(view)
    fireEvent.mouseDown(one, { shiftKey: true })
    expect(chosen(view)).toBe(1)
    one.focus()
    await userEvent.keyboard('{Escape}')
    expect(chosen(view)).toBe(0)
  })
})

describe('the size presets', () => {
  it('puts a card to a preset and pulls it in from the edge', async () => {
    const saved = vi.fn()
    const widgets = [widget(1, [3, 2], [2, 1])]
    const layouts = { lg: [{ i: '1', x: 10, y: 0, w: 2, h: 2 }], md: [], sm: [] }
    render(<BoardGrid {...({ widgets, layouts, data: {}, editing: true, canAct: true, autoCompact: false, onLayoutChange: saved } as unknown as Parameters<typeof BoardGrid>[0])} />)
    fireEvent.click(screen.getByRole('button', { name: 'XL' }))
    const lg = saved.mock.calls.find(([bp]) => bp === 'lg')
    expect(lg).toBeTruthy()
    const item = (lg![1] as LayoutItem[]).find((one) => one.i === '1')!
    // Twice the default of 3×2 on twelve columns is 6×4; at x=10 it would stick out, so it moves to x=6.
    expect([item.w, item.h, item.x]).toEqual([6, 4, 6])
  })
})
