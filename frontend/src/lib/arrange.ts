/**
 * Arranging cards by hand, several at once, and by machine.
 *
 * Both functions work on the wide layout only and never change a card's
 * size: the sizes are what the person chose, the places are what they asked
 * to have done for them.
 */
export interface Placed {
  i: string
  x: number
  y: number
  w: number
  h: number
}

/** Whether two cards share a cell. */
function collide(a: Placed, b: Placed): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

/**
 * Every card put back in reading order, first-fit, no gaps.
 *
 * The cards are taken row by row and left to right as they stand, and each
 * one goes to the first cell, scanning rows from the top and columns from
 * the left, where it fits without touching a card already placed. A hole
 * under a short card is filled by the next card that fits into it.
 */
export function tidy<T extends Placed>(items: T[], cols: number): T[] {
  const ordered = [...items].sort((a, b) => a.y - b.y || a.x - b.x)
  const placed: Placed[] = []
  const result: T[] = []
  for (const item of ordered) {
    const w = Math.min(cols, item.w)
    let spot: { x: number; y: number } | null = null
    for (let y = 0; spot === null; y += 1) {
      for (let x = 0; x + w <= cols; x += 1) {
        const candidate = { i: item.i, x, y, w, h: item.h }
        if (!placed.some((other) => collide(candidate, other))) {
          spot = { x, y }
          break
        }
      }
    }
    const moved = { ...item, x: spot.x, y: spot.y, w }
    placed.push(moved)
    result.push(moved)
  }
  return result
}

/**
 * The selected cards moved together by `dx`, `dy` cells, or null when the
 * step would take one of them off the grid or onto a card that is not part
 * of the selection. All or nothing: a group that half moves is worse than
 * one that stays.
 */
export function moveGroup<T extends Placed>(items: T[], selected: readonly string[], dx: number, dy: number, cols: number): T[] | null {
  const chosen = new Set(selected)
  const moved = items.map((item) => (chosen.has(item.i) ? { ...item, x: item.x + dx, y: item.y + dy } : item))
  const group = moved.filter((item) => chosen.has(item.i))
  const rest = moved.filter((item) => !chosen.has(item.i))
  for (const item of group) {
    if (item.x < 0 || item.y < 0 || item.x + item.w > cols) return null
    if (rest.some((other) => collide(item, other))) return null
  }
  return moved
}
