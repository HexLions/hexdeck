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
