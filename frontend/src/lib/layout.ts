/**
 * The grid a board is arranged on: how many columns, how wide, how tall a row.
 *
 * All three are settings of the board. A board without them is drawn as
 * every board was before: twelve columns, 1480 px wide, rows of 68 px.
 * Adapters declare sizes in twelfths and are not touched; the scaling
 * happens here.
 */
export type Columns = 12 | 24 | 36
export const ROW_HEIGHT = 68
export const ROW_HEIGHT_COMPACT = 60
/** Under this a card cannot draw its title and a line; the board scrolls instead. */
export const ROW_HEIGHT_MIN = 48
/** Over this a three-row board on a 4K display turns into billboards. */
export const ROW_HEIGHT_MAX = 160
const DEFAULT_WIDTH = '1480px'
const CUSTOM_WIDTH = [320, 10000]

export function columnsOf(settings?: Record<string, unknown>): Columns {
  const value = settings?.columns
  return value === 24 || value === 36 ? value : 12
}

/** The CSS max-width of the board, or undefined when it may use it all. */
export function maxWidthOf(settings?: Record<string, unknown>): string | undefined {
  const value = settings?.max_width
  if (value === 'full') return undefined
  if (typeof value === 'number' && value >= CUSTOM_WIDTH[0] && value <= CUSTOM_WIDTH[1]) return `${value}px`
  return DEFAULT_WIDTH
}

/** The lowest row a card reaches; one for an empty page. */
export function rowsOf(items: { y: number; h: number }[]): number {
  return Math.max(1, ...items.map((item) => item.y + item.h))
}

/** The row height that makes `rows` rows fill `space` pixels, within what cards can bear. */
export function rowHeightFor(space: number, rows: number, gap: number): number {
  const raw = (space - gap * (rows - 1)) / Math.max(1, rows)
  return Math.floor(Math.min(ROW_HEIGHT_MAX, Math.max(ROW_HEIGHT_MIN, raw)))
}

/** How many board columns one twelfth is; never under one, so a phone's four columns cap rather than shrink. */
export function unitOf(columns: number): number {
  return Math.max(1, columns / 12)
}

/** An adapter's floor, declared in twelfths, on a board of `columns`. */
export function scaleFloor(size: [number, number], columns: number): [number, number] {
  return [size[0] * unitOf(columns), size[1]]
}

export type SizePreset = 'S' | 'M' | 'L' | 'XL'
export const SIZE_PRESETS: SizePreset[] = ['S', 'M', 'L', 'XL']

/**
 * Four sizes a card can be put to without dragging its corner: the smallest
 * the adapter says is usable, the size it was created with, and one and a
 * half and twice that. Width is capped at the grid; nothing goes below the
 * floor.
 */
export function presetSizes(widget: { default_size?: [number, number]; min_size?: [number, number] }, columns: number): Record<SizePreset, [number, number]> {
  const [floorW, floorH] = scaleFloor(widget.min_size ?? widget.default_size ?? [1, 1], columns)
  const [baseW, baseH] = scaleFloor(widget.default_size ?? widget.min_size ?? [3, 2], columns)
  const clamp = (w: number, h: number): [number, number] => [Math.max(1, Math.min(columns, Math.max(floorW, Math.round(w)))), Math.max(1, Math.max(floorH, Math.round(h)))]
  return {
    S: clamp(floorW, floorH),
    M: clamp(baseW, baseH),
    L: clamp(baseW * 1.5, baseH * 1.5),
    XL: clamp(baseW * 2, baseH * 2),
  }
}
