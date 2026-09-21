# Notice

HexDeck is a fork of **nexdeck** by DerKezorm, licensed under the GNU Affero
General Public License, version 3 (see [LICENSE](LICENSE)). HexDeck keeps the
same licence: every copy of it, and every service built on it, must make its
source available under the AGPL-3.0.

## Origin

- Upstream project: <https://github.com/DerKezorm/nexdeck>
- Forked at: commit `a47793865dfebfeda150100342f80461b147722a` (release 0.15.0, 2026-09-18)
- HexDeck repository: <https://github.com/HexLions/hexdeck>

## Copyright

- Copyright (C) DerKezorm — nexdeck, the original work.
- Copyright (C) 2026 Cosimo Leoni (HexLions) — the changes listed below.

The original copyright and attribution are kept in full. Nothing here
replaces them.

## What HexDeck changes

Updated at every milestone.

- **M1 — Fork and rebranding.** Product name, container image
  (`ghcr.io/hexlions/hexdeck`), environment prefix (`HEXDECK_`, with the
  original `NEXDECK_` still read as a fallback) and CI target. No functional
  change. Identifiers stored in data (database file name, backup profile,
  cookie names, browser storage keys, Docker labels, board export key,
  key-derivation salts) keep their nexdeck spelling so existing
  installations, backups and board files work unchanged. The adapters under
  `backend/app/adapters/` are as upstream has them.
- **M2 — Fill the screen.** Columns (12, 24 or 36), maximum width and
  fit-to-screen row height are settings of each board. No stored layout of
  an existing board changes; new boards and the demo are created on 24
  columns. Adapter sizes stay in twelfths and are scaled at the grid.
- **Restyle.** HexDeck's own look as a theme layer: default colour tokens
  in both brightnesses, opaque panels instead of glass, a hexagonal mark,
  favicon and app icons, hexagonal status marks, avatars and spinner, a
  hexagonal tessellation behind the board, Manrope for the wordmark, and
  "HexDeck blue" as the default accent preset. Components, layout and the
  operator's own accent and stylesheet are unchanged; nexdeck's cyan stays
  as a preset.
- **Italian.** The interface, the adapters' texts and the cards' labels in
  Italian, next to English and German. Release notes stay in English.
- **Arranging.** Several cards can be selected (Shift or Ctrl and a click)
  and moved together by drag or arrow keys; "Tidy up" in edit mode puts
  every card of a page back in reading order without gaps, sizes kept.
- **M3 — Projects and roadmap.** Projects, milestones, items and linked
  repositories in HexDeck's own database, a settings page to manage them,
  and three cards (`projects.roadmap`, `projects.project`,
  `projects.items`) for any board. GitHub is read only.
- **On the cards.** Projects are created from a project or items card
  that has none, or from the card's settings; items are added, renamed,
  ticked and deleted on the items card; milestones are added and ticked
  on the roadmap card. A notepad card (`notepad.pad`) is typed into on the
  board itself.
- **M4 — GitHub.** The GitHub adapter reads issues, pull requests with
  their review state, workflow runs and milestones next to releases, from
  a list of repositories or from a HexDeck project's linked ones. Every
  answer is kept with its ETag and asked for again with If-None-Match; the
  rate limit headers are honoured, a used-up limit is a clear message with
  the reset time and the last answer is shown meanwhile. An optional
  personal access token raises the limit from sixty to five thousand.
- **M5 — TrueNAS.** The REST API fallback is refused on TrueNAS 25.04 and
  later, where it is deprecated, raises an alert on every call from 25.10.1
  and is removed in 26; the card says to use https:// and a user-linked
  read-only key. The README gains a TrueNAS SCALE section and the two
  warnings that must not be buried: the secret key and the Docker socket.
- **Maintenance.** An item takes a due date and an interval in days.
  Ticking a recurring item moves its date on by the interval, counted
  from the date that was due, and it comes back to do; a one-off with a
  date finishes. The roadmap lists the dated items among the milestones,
  the items card says when each is due and lets the date be set on the
  board, and what is due or late is announced once a day through the
  notification channels (`maintenance_due`).
- **Templates.** Five board templates under `backend/app/templates/`, in
  the export's own YAML with a `template` section on top, offered under
  Settings › Boards. Installing one maps its placeholder connections to
  the installation's own (or leaves them out with their cards) and goes
  through the ordinary import with the caller's rights; the wide layout is
  drawn, the narrower screens follow.
