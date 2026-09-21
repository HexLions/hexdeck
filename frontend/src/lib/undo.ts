/**
 * The arrangements a page had before each change, so a move that went
 * wrong is one keystroke away from undone.
 *
 * Only the wide arrangement is kept: the phone stacks it, and the server
 * derives the rest. A change that leaves every card where it was is not
 * remembered, so undo never appears to do nothing.
 */
import type { LayoutItem } from './types'

export const UNDO_DEPTH = 30

/** Whether two arrangements put every card in the same place at the same size. */
export function sameArrangement(a: LayoutItem[], b: LayoutItem[]): boolean {
  if (a.length !== b.length) return false
  const byId = new Map(b.map((item) => [item.i, item]))
  return a.every((item) => {
    const other = byId.get(item.i)
    return other !== undefined && other.x === item.x && other.y === item.y && other.w === item.w && other.h === item.h
  })
}

/** The stack with `before` on top, unless it is what is already there or what comes next. */
export function remember(stack: LayoutItem[][], before: LayoutItem[], after: LayoutItem[]): LayoutItem[][] {
  if (sameArrangement(before, after)) return stack
  const top = stack[stack.length - 1]
  if (top && sameArrangement(top, before)) return stack
  return [...stack, before].slice(-UNDO_DEPTH)
}
