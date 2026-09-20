import { moveGroup, tidy } from './arrange'

const item = (i: string, x: number, y: number, w: number, h: number) => ({ i, x, y, w, h })

describe('tidy', () => {
  it('packs the cards in reading order without gaps, sizes untouched', () => {
    const scattered = [item('a', 5, 4, 3, 2), item('b', 0, 0, 6, 2), item('c', 9, 0, 3, 1), item('d', 0, 9, 6, 1)]
    expect(tidy(scattered, 12)).toEqual([item('b', 0, 0, 6, 2), item('c', 6, 0, 3, 1), item('a', 9, 0, 3, 2), item('d', 0, 2, 6, 1)])
  })

  it('opens a new row when a card does not fit beside the others', () => {
    const wide = [item('a', 0, 0, 8, 1), item('b', 8, 0, 8, 1)]
    expect(tidy(wide, 12)).toEqual([item('a', 0, 0, 8, 1), item('b', 0, 1, 8, 1)])
  })

  it('fills a hole left under a short card', () => {
    // a is tall on the left, b short on the right: c fits under b, not under a.
    const holes = [item('a', 0, 0, 6, 3), item('b', 6, 0, 6, 1), item('c', 0, 5, 6, 1)]
    expect(tidy(holes, 12)).toEqual([item('a', 0, 0, 6, 3), item('b', 6, 0, 6, 1), item('c', 6, 1, 6, 1)])
  })

  it('keeps a card wider than the grid at the full width', () => {
    expect(tidy([item('a', 0, 0, 30, 1)], 12)).toEqual([item('a', 0, 0, 12, 1)])
  })

  it('leaves an empty board empty', () => {
    expect(tidy([], 12)).toEqual([])
  })
})

describe('moveGroup', () => {
  const board = [item('a', 0, 0, 2, 1), item('b', 2, 0, 2, 1), item('c', 8, 0, 2, 1)]

  it('moves every selected card by the same step', () => {
    expect(moveGroup(board, ['a', 'b'], 1, 2, 12)).toEqual([item('a', 1, 2, 2, 1), item('b', 3, 2, 2, 1), item('c', 8, 0, 2, 1)])
  })

  it('refuses a step that would push a card off the grid', () => {
    expect(moveGroup(board, ['a', 'b'], -1, 0, 12)).toBeNull()
    expect(moveGroup(board, ['c'], 3, 0, 12)).toBeNull()
    expect(moveGroup(board, ['a'], 0, -1, 12)).toBeNull()
  })

  it('refuses a step that would land on a card outside the selection', () => {
    expect(moveGroup(board, ['a', 'b'], 6, 0, 12)).toBeNull()
  })

  it('lets the selection slide over its own members', () => {
    expect(moveGroup(board, ['a', 'b'], 2, 0, 12)).toEqual([item('a', 2, 0, 2, 1), item('b', 4, 0, 2, 1), item('c', 8, 0, 2, 1)])
  })
})
