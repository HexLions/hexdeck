import { columnsOf, maxWidthOf, rowHeightFor, rowsOf, scaleFloor } from './layout'

describe('columnsOf', () => {
  it('is twelve for a board from before, and what the setting says otherwise', () => {
    expect(columnsOf(undefined)).toBe(12)
    expect(columnsOf({})).toBe(12)
    expect(columnsOf({ columns: 24 })).toBe(24)
    expect(columnsOf({ columns: 36 })).toBe(36)
    expect(columnsOf({ columns: 13 })).toBe(12)
    expect(columnsOf({ columns: '24' })).toBe(12)
  })
})

describe('maxWidthOf', () => {
  it('keeps the old width by default, lifts it for full and takes a number', () => {
    expect(maxWidthOf(undefined)).toBe('1480px')
    expect(maxWidthOf({ max_width: 'full' })).toBeUndefined()
    expect(maxWidthOf({ max_width: 1920 })).toBe('1920px')
    expect(maxWidthOf({ max_width: 'nonsense' })).toBe('1480px')
    expect(maxWidthOf({ max_width: 10 })).toBe('1480px')
  })
})

describe('rowHeightFor', () => {
  it('divides the space among the rows less the gaps', () => {
    expect(rowHeightFor(1000, 10, 12)).toBe(89)
  })
  it('never goes under 48 so every renderer can still draw', () => {
    expect(rowHeightFor(300, 20, 12)).toBe(48)
  })
  it('never goes over 160 so three rows do not become billboards', () => {
    expect(rowHeightFor(2000, 3, 12)).toBe(160)
  })
})

describe('rowsOf', () => {
  it('treats an empty board as one row', () => {
    expect(rowsOf([])).toBe(1)
    expect(rowsOf([{ y: 2, h: 3 }, { y: 0, h: 1 }])).toBe(5)
  })
})

describe('scaleFloor', () => {
  it('turns an adapter minimum in twelfths into the board columns', () => {
    expect(scaleFloor([3, 2], 12)).toEqual([3, 2])
    expect(scaleFloor([3, 2], 24)).toEqual([6, 2])
  })
  it('never shrinks a floor on a grid narrower than twelve', () => {
    expect(scaleFloor([6, 4], 4)).toEqual([6, 4])
  })
})
