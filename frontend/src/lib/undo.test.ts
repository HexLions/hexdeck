import type { LayoutItem } from './types'
import { remember, sameArrangement, UNDO_DEPTH } from './undo'

const at = (i: string, x: number, y: number, w = 2, h = 2): LayoutItem => ({ i, x, y, w, h })

describe('remembering arrangements', () => {
  it('keeps the arrangement a change started from', () => {
    const before = [at('1', 0, 0), at('2', 2, 0)]
    const after = [at('1', 0, 0), at('2', 2, 2)]
    expect(remember([], before, after)).toEqual([before])
  })

  it('does not keep a change that moved nothing', () => {
    const before = [at('1', 0, 0)]
    expect(remember([], before, [at('1', 0, 0)])).toEqual([])
    expect(sameArrangement(before, [at('1', 0, 0, 3, 2)])).toBe(false)
    expect(sameArrangement(before, [])).toBe(false)
  })

  it('does not keep the same arrangement twice in a row', () => {
    const before = [at('1', 0, 0)]
    const stack = remember([before], before, [at('1', 1, 0)])
    expect(stack).toHaveLength(1)
  })

  it('forgets the oldest past the depth', () => {
    let stack: LayoutItem[][] = []
    for (let step = 0; step < UNDO_DEPTH + 5; step += 1) stack = remember(stack, [at('1', step, 0)], [at('1', step + 1, 0)])
    expect(stack).toHaveLength(UNDO_DEPTH)
    expect(stack[0][0].x).toBe(5)
  })
})
