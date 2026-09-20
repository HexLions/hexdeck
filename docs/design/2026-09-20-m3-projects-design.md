# M3 — Projects and roadmap: design

## Goal

Track what is being built, in HexDeck itself: projects with milestones and
items, optionally linked to GitHub repositories, shown as cards on any
board. A roadmap card answers "what is due in the next four weeks" at a
glance. Not everything tracked is software: a project works with no
repository at all.

## Decisions

- **The truth is the local database.** GitHub is read, never written; an
  item may reference an issue and shows its link (and, once M4 lands, its
  state). No synchronisation in either direction.
- **Projects are global** to the installation, not per account.
  Administrators and users create and change them; guests only see the
  cards. Card visibility follows the board, as for every other card.
- **Management lives in a settings page**, "Projects", under the person's
  own settings. The cards allow the quick edits that make sense on a board:
  cycling an item's state and reordering items by drag. Everything else
  (names, dates, notes, repositories) is done on the page.
- **Items stay minimal**: title, notes, state (`todo`, `doing`, `done`),
  optional milestone, optional issue reference. Dates belong to
  milestones; the roadmap is the view of what is due.
- **A new adapter, not the core adapter.** `backend/app/adapters/projects.py`
  declares the three widgets and reads the local tables inside `fetch`,
  the way `core.py`'s "problems" card reads the live state. The `Adapter`
  contract and the 140 upstream adapters are untouched.

## Data

Migration step 13 in `backend/app/migrations.py`, models in `models.py`:

| Table | Columns |
|---|---|
| `projects` | id, name (120), slug (80, unique), description (text), status (`active` / `paused` / `done`), colour (7, `#rrggbb` or empty), position, created_at, updated_at |
| `project_repos` | id, project_id (FK, cascade), repo (`owner/name`, 200), position |
| `milestones` | id, project_id (FK, cascade), title (200), target_date (date, nullable), status (`open` / `done`), position |
| `project_items` | id, project_id (FK, cascade), milestone_id (FK, set null), title (200), notes (text), status (`todo` / `doing` / `done`), issue (`owner/name#123`, 240, empty allowed), position |

Deleting a project deletes its repositories, milestones and items. Deleting
a milestone leaves its items without one.

## API

Router `backend/app/routers/projects.py`, prefix `/api/v1`. Every route
carries an auth dependency: reads need `current_user` (guests included,
they may look), writes need `not_guest`.

| Method and path | Body / effect |
|---|---|
| `GET /projects` | every project with its repos, milestones and items, in position order |
| `POST /projects` | `{name, description?, status?, colour?}` → 201 project |
| `PATCH /projects/{id}` | any of name, description, status, colour, position |
| `DELETE /projects/{id}` | 204 |
| `POST /projects/{id}/repos` | `{repo}`; `owner/name` only, 400 otherwise |
| `DELETE /projects/{id}/repos/{repo_id}` | 204 |
| `POST /projects/{id}/milestones` | `{title, target_date?, status?}` |
| `PATCH /milestones/{id}` | any of title, target_date (null clears), status, position |
| `DELETE /milestones/{id}` | 204 |
| `POST /projects/{id}/items` | `{title, notes?, status?, milestone_id?, issue?}` |
| `PATCH /items/{id}` | any of title, notes, status, milestone_id (null clears), issue |
| `DELETE /items/{id}` | 204 |
| `PUT /projects/{id}/items/order` | `{ids: [...]}`: the new order; ids not in the list keep their relative order after the listed ones |

Every write publishes one SSE event `projects` on the global topic (the
one boards use for "board changed"), and the collector marks every
`projects.*` widget for a refetch, so the cards move as the page does.

## Adapter and widgets

`backend/app/adapters/projects.py`: `kind = "projects"`, `label =
"Projects"`, `category = "basics"`, `needs_integration = False`,
`beta = False`, `icon = "lucide:map"`.

| Widget | Renderer | Options | `WidgetData` |
|---|---|---|---|
| `roadmap` | `roadmap` | `weeks` (4 / 8 / 12, default 4), `projects` (choices, empty = all), `done` (bool, show done milestones) | `items`: one per milestone: `{id, title, project, colour, date, days, status: open/done/late/soon}`, sorted by date, undated last; `primary`: how many are due within the horizon; `meta.today`, `meta.weeks` |
| `project` | `project` | `project` (choices, required) | `primary`: completion percent; `secondary`: state, next milestone (title, date, days), counts (items done / total, open milestones); `items`: repos `{repo, url}`; `meta`: name, colour, description, status |
| `items` | `items` | `project` (choices, required), `milestone` (choices, empty = all), `hide_done` (bool) | `items`: `{id, title, notes, status, milestone, issue, url}` in position order; `actions`: `cycle` (id) and `reorder` (ids) |

Milestone status in the roadmap: `done` as stored, `late` when open and
the target date is before today, `soon` when open and within the horizon,
`open` otherwise. Days are whole days from today, negative when late.

Actions: `cycle` moves an item `todo → doing → done → todo`; `reorder`
takes the ordered ids. Both go through the normal action path, so they
need the board's act permission and are logged like any action. Demo
mode returns one invented project ("HexDeck", three milestones, six
items) so the demo board can show the cards.

`RENDERER_MIN` gains `roadmap: (4, 2)`, `project: (3, 2)`, `items: (3, 2)`.

## Renderers

Three new components in `frontend/src/components/renderers.tsx`, registered
in `RENDERERS`:

- **`RoadmapCard`**: a horizontal time line from today to today + horizon,
  today marked, one hexagonal dot per milestone at its date, the project
  colour on the dot, the title under it; late milestones sit at the left
  edge in the bad colour, undated ones in a row at the end. The header
  chip says how many are due within the horizon. Below 4 columns of width
  (the container query the cards already use) it degrades to a sorted
  list with the same dots.
- **`ProjectCard`**: name in the project colour, state chip, next
  milestone with date and days, a completion bar (done / total items) and
  the repositories as link chips. With no repositories the row is absent,
  not empty.
- **`ItemsCard`**: a flat list; each row a state mark (click cycles it
  through the `cycle` action), title, milestone in small type, an issue
  link when set. Drag handle per row; a drop calls the `reorder` action
  with the new order. Without act permission the marks and handles are
  drawn but inert, as other cards' actions are.

## Settings page

`frontend/src/pages/settings/ProjectsSettings.tsx`, entry "Projects" in the
person's settings navigation (not under System). The list shows every
project with its colour, state and counts; selecting one opens its
detail: name, description, state, colour; repositories (add `owner/name`,
remove); milestones (title, date, state; reorder by arrows); items (title,
notes, state, milestone, issue; reorder by arrows). Built from the same
`Field`, `Switch`, `Sheet` and button classes the other settings pages use.

## Testing

- Backend: CRUD of each table, cascade on delete, a guest is refused on
  every write and allowed on the read, item order endpoint, the auth guard
  finds a dependency on every new route, every new adapter text has a
  German and an Italian entry.
- Adapter: roadmap statuses (late / soon / open / done) against a fixed
  "today"; a project with no repos; demo data fits the renderers' floors.
- Frontend: `RoadmapCard` places a late, a soon and an open milestone in
  the right buckets and colours; `ItemsCard` cycles a state through
  `onAction` and reports a new order after a drop; the settings page
  creates a project and adds an item through the mocked API; i18n
  completeness for en, de, it.
