# M2 — Fill the screen: board layout design

## Goal

A board fills the monitor it is shown on: no empty bands on wide screens,
finer placement than a quarter of the width, and a mode that scales the rows
so the whole board fits the window without scrolling. A board arranged
before this change looks exactly the same after it.

## Decisions

### Columns per board, not a global migration

The number of grid columns is a board setting, `settings.columns`, one of
12, 24 or 36. A board without the key has 12 columns, which is what every
existing board was arranged in, so nothing moves. New boards are created
with 24. The demo board is written with 24.

The layouts of a page are expressed in the columns of its board. Every place
that reads or writes a layout takes the column count from the board:

- `frontend/src/components/BoardGrid.tsx`: `COLUMNS.lg` becomes a prop
  `columns`; `layoutFor`, `stackedFor`, `moved` and `floorOf` receive it.
- `backend/app/services/boards.py`: `COLUMNS["lg"]` is replaced by the
  board's columns in `place_widget` and its helper class; `md` stays 8 and
  `sm` stays 4 (the phone stacks, the tablet layout is not read).
- Board export already carries `board.settings`, so `settings.columns` travels
  with the file; a file without the key is a 12-column file. A file
  whose layouts overflow its declared columns is clamped, as today.
- `backend/app/services/demo_board.py` writes its layouts in 24 columns and
  sets `settings.columns = 24`.
- Backups carry the database as is; nothing to convert.

Adapter sizes (`WidgetType.default_size`, `min_size`) and `RENDERER_MIN` in
`backend/app/adapters/base.py` stay in 12-column units. They are scaled by
`columns / 12` where they meet a layout: in `place_widget` on the server and
in `floorOf` in the grid. The adapters and the guard that checks
`RENDERER_MIN` against them are untouched.

**Changing the columns of an existing board** is an action in the board
settings sheet ("Grid: 12 / 24 / 36 columns"). Going from fewer to more
columns multiplies `x` and `w` of every item on every page by the ratio and
is exact. Going to fewer divides and rounds, so the sheet warns that cards
may shift. The change is saved through the normal layout save, so
`layout_version` counts up and a second browser reloads.

### Width per board

`settings.max_width` is `"1480"` (default: today's `max-w-[1480px]`),
`"full"` (no limit; the side padding stays) or a number of pixels.
`BoardPage` and `PreviewPage` apply it to `<main>` as an inline
`max-width`. The board settings sheet offers "As before (1480 px)", "Whole
screen" and "Custom".

### Adaptive row height, per board

`settings.fit_screen` (default false) turns on fit-to-screen. Off, rows are
68 px as today (60 in compact mode). On, the grid measures the vertical
space it has (the window height minus the top of the grid and the bottom
padding) and computes

    rowHeight = clamp((space − gap × (rows − 1)) / rows, 48, 160)

where `rows` is the lowest occupied row of the page's layout
(`max(y + h)`), at least 1. The floor of 48 px keeps every renderer above
the size it can draw at; when the rows do not fit at 48 px the board
scrolls rather than squashing the cards. The cap of 160 px keeps a
three-row board from turning into billboards on a 4K display. The height
is recomputed on window resize through a `ResizeObserver` on the grid's
parent, debounced to one animation frame.

The kiosk page uses the same grid and therefore the same setting; wall
displays are where the mode matters most.

### What does not change

The phone layout (`sm`, four columns, stacked in board order) is untouched.
The `md` layout the server still keeps is still not read. The `Adapter`
contract and the 140 adapters are untouched.

## Data shape

`board.settings` (JSON, already free-form):

```json
{ "columns": 24, "max_width": "full", "fit_screen": true, "compact": false }
```

All keys optional. Unknown or out-of-range values fall back to the default
(12, "1480", false). The board schema in `backend/app/schemas.py` validates
`columns ∈ {12, 24, 36}`, `max_width ∈ {"1480", "full"} ∪ [320, 10000]`,
`fit_screen: bool`.

## Testing

- Unit, frontend: `rowHeightFor(space, rows, gap)` (floor, cap, one row,
  zero rows), `scaleLayout(items, from, to)` (12→24 exact, 24→12 rounds and
  keeps `w ≥ 1`), `floorOf` scaled by columns.
- Unit, backend: `place_widget` on a 24-column board doubles the adapter
  size; export writes `columns`; import without `columns` treats the file as
  12 and keeps items as they are; import with `columns: 24` keeps them too;
  a guard that the demo board fits its columns.
- Visual, Playwright: the demo board at 1920×1080, 2560×1440 and 3440×1440
  with 12 and 24 columns and fit on and off; every card readable, no card
  over another, no empty band with `max_width: full`. Screenshots reviewed
  by eye, especially the weather card.
- Regression: a board created before the change (a fixture exported from
  0.16.0) renders with the same positions and sizes after it.
