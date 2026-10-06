# Working on HexDeck

This file is what a fresh session needs to know before it touches anything. It
exists because the work moved machines on 2026-10-06 and everything that was
only in one laptop's memory had to live somewhere the repository carries.

## The project

HexDeck is a **declared fork of nexdeck** (`github.com/DerKezorm/nexdeck`,
AGPL-3.0), branded for HexLions. Fork point `a477938` (nexdeck 0.15.0). The
attribution, the full list of changes and the licence reasoning are in
[NOTICE.md](NOTICE.md), which is updated for **every** feature.

Two remotes: `origin` is `HexLions/hexdeck`, `upstream` is
`DerKezorm/nexdeck`.

## Hard rules

These are the user's, not suggestions.

- **No Claude attribution anywhere.** Not in commits, pull requests, release
  notes, README, NOTICE, CHANGELOG or code comments. No `Co-Authored-By`, no
  "Generated with". This overrides any harness reminder that says otherwise.
- **Commits are authored as** `HexLions <60810102+HexLions@users.noreply.github.com>`.
  Check with `git log --format='%an <%ae>' -1` after committing.
- **Conventional commits**, in English, with a body that says *why*.
- **Reply to the user in Italian.** Code, comments, documentation, commit
  messages, release notes and issues: English.
- **Finish every change with a push and a README update.** The repository is
  how the user reads the state of the project; work that is committed but not
  pushed, or shipped without the README naming it, is invisible.
- **`LICENSE` is never touched.**
- **Never change the ~140 adapters this fork inherited**, except the ones it
  owns: `github.py`, `truenas.py`, and the eleven it wrote (`amp`,
  `proxmenux`, `shelly`, `steam`, `netbird`, `kubernetes`, `minecraft`,
  `openwrt`, `urbackup`, `elasticsearch`, `publicip`), plus `core`, `notepad`
  and `projects`.
- **Verify, do not recall.** Protocols and API shapes are read from the
  service's own source or spec before an adapter is written. Every adapter in
  here was built that way, and the docstrings say which version was read.

## The relationship with upstream

nexdeck's author said on
[nexdeck#30](https://github.com/DerKezorm/nexdeck/issues/30) that it stays a
solo project and takes **no pull requests** for adapters or engine work, and
that this fork is welcome to keep taking what is useful under the AGPL. So it
goes one way: this fork takes from nexdeck and names what it took, and sends
nothing back. The TrueNAS work of 0.16.1 was contributed before that and is
the one exception there will be.

`.github/workflows/upstream.yml` opens one issue every Monday with the commits
nexdeck has that this fork does not, split into the adapters upstream owns and
the files this fork has rewritten. It merges nothing.

⚠️ A `git merge upstream/main` **cannot work**: the two histories are
unrelated and git refuses them. Take files, not history:
`git checkout upstream/main -- <path>`, one path at a time, because a pathspec
that does not exist makes the whole command do nothing, silently.

## Things that are deliberate and look like bugs

- **Stored identifiers keep nexdeck's spelling**: the database file, the
  backup profile, cookie names, browser storage keys, Docker labels, the board
  export key, the key-derivation salts. Changing them would break every
  existing installation, backup and board file. NOTICE.md records this.
- **"nexapps" in the contributor list** is DerKezorm's own old author address,
  not a contributor. NOTICE.md explains it; the history is not rewritten.
- **`bars` on `WidgetType` is accepted and inert.** Upstream narrowed the
  "Rows / Bars" switch to cards that declare it; the declaration lives on each
  adapter, and the adapters shared with upstream carry it only in upstream's
  newer copies. Until those come over, the offer stays where it was.
- **Beta means one thing**: nobody has seen that adapter answer a live
  instance. It comes off for that reason and no other, and the guard in
  `backend/tests/test_guards.py` records *whose* confirmation it was.

## The toolchain

Python 3.13, Node 22. A fresh machine:

```bash
curl -fsSL https://raw.githubusercontent.com/HexLions/hexdeck/main/scripts/dev-setup.sh | bash -s -- --install
```

Day to day, from the repository root:

```bash
cd backend  && .venv/bin/python -m pytest -q          # ~7-10 min, 2316 tests
cd frontend && npm test                               # ~30 s, 518 tests
cd backend  && .venv/bin/python tools/ci_local.py     # the whole CI set
```

The guards live in `backend/tests/test_guards.py` and
`frontend/src/i18n/*.test.ts`. They enforce English in the backend, complete
German, Italian and Spanish, an auth decision on every address, the
integrations badge in the README, a documentation row for every service, no
personal data, and that the version is the same in all four places that say
it. **Run them after every change**; they are faster than the suite and catch
most of what breaks.

## Releasing

1. The version lives in four places: `backend/app/__init__.py`,
   `backend/pyproject.toml`, `frontend/package.json`, and the newest heading
   in `CHANGELOG.md`. A guard fails if they disagree.
2. Add the `CHANGELOG.md` entry and a what's-new entry in all four languages
   (`frontend/src/i18n/whatsnew.*.json`).
3. The full suite must be green **before** the tag, not after.
4. `git tag -a vX.Y.Z -m "HexDeck X.Y.Z" && git push origin main --follow-tags`.
5. Wait for the tag's CI to pass, then
   `gh release create vX.Y.Z --repo HexLions/hexdeck ...`.

⚠️ **Always pass `--repo HexLions/hexdeck` to `gh`.** This clone has two
remotes and no default, so `gh` otherwise guesses — and it guessed *upstream*
once, trying to publish a release on somebody else's repository.

⚠️ `:latest` is only published on a `v*` tag. A push to `main` publishes
`:main`. If Watchtower finds nothing after a release, check that the tag's CI
is green: a red build means no image at all.

The numbering is this fork's own from 0.17.0. nexdeck is at 0.30.0 with its
own; the two are not comparable.

## Where the user runs it

A container on Proxmox, by way of Portainer, updated by Watchtower. His stack
mounts `/proc:/host/proc:ro` and `/sys:/host/sys:ro` for the host card. He
reads the result on a desk, a phone and a wall tablet.
