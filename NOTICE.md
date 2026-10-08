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

⚠️ **About "nexapps" in this repository's contributor list.** 103 commits in
this history are DerKezorm's own, made before he rewrote nexdeck's history on
21.09.2026, and they carry the author address he used then,
`nexapps@users.noreply.github.com`. GitHub maps that address to an unrelated
account, so it shows up here as a contributor. It is not one: that work is
DerKezorm's, he signs as `188376159+DerKezorm@users.noreply.github.com` today,
and he told us where the entry comes from himself
([nexdeck#30](https://github.com/DerKezorm/nexdeck/issues/30)). The history is
left as it is rather than rewritten, because rewriting it would change every
commit of a published repository and every tag with it; this note is the
correction.

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
- **M5 — TrueNAS.** No REST call where TrueNAS has the current API. First
  done here and sent upstream as DerKezorm/nexdeck#9; DerKezorm measured it
  on a real 25.10.7 and built the final design on nexdeck's main (0.16.1),
  which HexDeck takes over: a plain GET of `/api/current` tells the current
  API apart without a key, only the version is remembered for the hour, and
  behind a proxy the hint names the proxy. The README gains a TrueNAS SCALE section and the two
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
- **Themes.** The appearance setting takes a theme: a name and the
  fifteen colour tokens of the interface for dark and for light, checked
  on the way in. Four bundled palettes (Nord, Catppuccin, Gruvbox,
  Dracula) in `backend/app/services/themes.py`, settled so that text
  clears 4.5:1 in both brightnesses; the page previews, imports and
  exports a theme as JSON. A theme with its own accent is left alone by
  the accent preset; a colour of one's own still wins.
- **Polish.** Appearance and language moved into the account menu; a
  card's headline number is kept as history even without a declared
  metric; Ctrl+Z puts the last arrangement back; four preset sizes on
  every card; *Leave demo mode* (`POST /api/v1/settings/demo/leave`)
  removes the demo connections, the cards that read them and the boards
  that were nothing but those. README rewritten with HexDeck's own
  pictures.
- **From other dashboards.** `backend/app/services/imports/` reads
  Homepage's `services.yaml`, `bookmarks.yaml` and `widgets.yaml`, and
  Homarr's (up to 0.15) config JSON, into a plan: connections with what
  is still missing, cards with a tick to leave out, warnings for what
  has no equivalent. `POST /api/v1/imports/preview` shows it,
  `/apply` makes it, through the ordinary untrusted board import; pasted
  values are never read as environment references.
- **No sign-in at home.** `backend/app/services/auto_login.py`: an
  administrator names trusted networks and one account; a browser from
  those addresses without a session gets one as that account on
  `GET /auth/me`, and a sign-out on purpose holds it off until the next
  sign-in (`/auth/auto` offers the way back). `HEXDECK_TRUSTED_PROXIES`
  says whose `X-Forwarded-For` is believed; the whole Internet is refused
  as a network. Set under System › Sign-in providers.
- **Reset from the shell.** `python -m app.tools.reset_password <user>` sets
  a new password for an account and ends its sessions; in docs/operating.md.
- **Moving cards.** `POST /api/v1/widgets/move` puts cards on another page,
  of the same board or of another one the caller may edit; the selection
  goes together. Card menus are drawn through a portal, so they are not
  cut at the card's edge.
- **TrueNAS live numbers.** The system card subscribes to
  `reporting.realtime` for one event and shows CPU usage and memory in use
  of the total (shapes read from the middleware's source, 25.x and 24.10);
  over REST the load average and the memory size stand in.
- **A notepad for everyone.** A notepad card can say that everyone who may
  see the board may write in it; `POST /api/v1/widgets/{id}/notepad`
  writes its text and nothing else, so a viewer reaches nothing else
  through it. A percentage row of a stats card is a bar and nothing
  else: the sparkline that used to take its place read as a hundred per
  cent, and drawn over the bar it was a stretched line across the row.
- **YouTube: from your subscriptions.** A second card reads the channels an
  account follows, through the data API with a key, and their videos from
  the free channel feeds. YouTube serves no home feed to any API
  (`activities.list` says the home page data is not available), so this is
  as close as it goes without keeping somebody's Google cookies.
- **A status page and a notices card.** Two cards of what HexDeck already
  knows: every card with a reachability check, with its latency, its
  availability bars and its uptime in the window, down first; and the
  notices it has sent, newest first. The availability row the app tile
  has always drawn is now a component any list row may carry.
- **The ten adapters this fork wrote are out of beta.** AMP, ProxMenux
  Monitor, UrBackup, Kubernetes, Minecraft, Steam, Shelly, NetBird, OpenWrt
  and Elasticsearch were confirmed against live instances on 2026-10-06, and
  the guard that holds the list records that this confirmation is ours, not
  upstream's. Beta here has always meant one thing, "nobody has seen it
  answer", and it comes off for that reason and no other.
- **Cards that hold cards, and a map.** The tabs card and the group came
  over with the ``cards`` field type, the two libraries behind them and the
  four lines in the grid that let a held card leave it. The topology drawing
  came with upstream's newer Proxmox and UniFi, which are what ask for it.
  Both drawings were adapted on the way in: upstream's "showcase" mode,
  which invents names for a public demo, does not exist in this fork, so the
  calls into it were taken out rather than the mode faked.
- **Spanish.** Upstream's Spanish covers what upstream has; the 421 strings
  this fork added on top (150 interface keys, 222 adapter texts, 49 labels)
  were translated here, and the 234 keys for features this fork does not
  have were taken out rather than left to rot. Spanish is now on the same
  guard list as German and Italian: a text without its translation fails
  the build. ⚠️ The translation is this fork's own work and no native
  speaker has read it.
- **Five of upstream's drawings, each with something that uses it.** The
  heading card (a title, a line or both, without the glass ground the other
  cards have, so it stands between them rather than among them); the status
  page as one wide strip of bars per service, which is what reads from
  across a room; a pair of metrics named like traffic drawn mirrored on one
  axis, in above and out below, offered wherever a card records such a pair;
  and the energy flow and the heatmap, which came with upstream's newer evcc
  and Tautulli. ``tabs``, ``group`` and ``topology`` are deliberately left
  for later: those are not drawings but a feature, a card that holds other
  cards, and they need the ``cards`` field type with them.
- **Twenty-three adapters taken over from upstream, with their tests.**
  nexdeck had reached 0.30.0 and 241 commits this fork did not have, so the
  adapters it owns were brought over as they are: Arcane, Dockhand,
  FileBrowser Quantum, FreshRSS, IMAP, Linkding, Mailpit, NeutArr, nexbeat,
  nexcrate, nexlore, nexsift, nextrmnl, OctoPrint, openmediavault, Pangolin,
  PatchMon, Qui, ReadMeABook, Real-Debrid, rTorrent, SNMP and Tracearr, with
  their 432 tests, their fixtures, their documentation rows and their German
  and Italian texts, plus the ``nexcrate`` pairing route, ``pysnmp`` and the
  ``bars`` flag on ``WidgetType`` that several of them declare.

  ⚠️ The ``bars`` flag is an upstream fix worth naming: the "Rows / Bars"
  switch used to be offered on every list card, and upstream counted that 14
  of 157 could draw it, so on the other 143 the switch changed nothing.

  ⚠️ Twelve of the twenty-three are out of beta because **upstream**
  confirmed them against a live instance. This fork has not seen them, and
  the guard that holds the list says so where somebody will read it.

  ⚠️ Seven adapters this fork wrote first exist upstream as well, written
  independently: NetBird, Kubernetes, Minecraft, UrBackup, the public
  address, Elasticsearch and OpenWrt. This fork keeps its own, which are
  the ones its tests cover.
- **NetBird, cloud and self-hosted alike.** The management API with a
  personal access token, which goes in as ``Authorization: Token nbp_...``
  and not as Bearer: sent as Bearer it is refused and nothing says which
  word was wrong. Three cards: the mesh with the logins that have run out
  and the machines waiting for approval, the machines themselves with what
  needs attention first, and the setup keys, where one that still works but
  not for much longer is worth a colour because what stops working is the
  machine enrolled with it, days later. A peer that is merely asleep is not
  a fault, and one that was never seen carries the zero time, which would
  otherwise read as the year one.
- **GitLab, on GitLab.com or a GitLab of one's own.** REST API v4, read-only,
  with a token that goes in as ``PRIVATE-TOKEN`` and none at all for a public
  project. A project is named by its path, which goes into the address
  URL-encoded: sent with its slashes, GitLab answers 404, which reads exactly
  like a project that does not exist. Three cards: the project with the last
  pipeline on its default branch and what is open, the pipelines with how
  each ended, and the merge requests with what still stands between each and
  its target. The count of merge requests comes from the ``x-total`` header,
  because a page holds a hundred at most. Read from GitLab's own API
  documentation and checked against a public project on GitLab.com.
- **The relationship with upstream, as upstream wants it.** DerKezorm said on
  [nexdeck#30](https://github.com/DerKezorm/nexdeck/issues/30) that nexdeck
  stays a solo project and takes no pull requests for adapters or engine work,
  and that this fork is welcome to keep taking what is useful under the AGPL.
  So it goes one way: this fork takes from nexdeck and names what it took, and
  sends nothing back. The TrueNAS work of 0.16.1 was contributed before that
  and remains the one exception there will be.
- **Following upstream without merging it.** ``.github/workflows/upstream.yml``
  runs weekly, counts what nexdeck has that this fork does not, and keeps one
  issue up to date with it. The report is split in two: the adapters upstream
  owns, which come over as they are, and the files this fork has its own
  version of, where a blind merge would undo work. Nothing is merged or
  cherry-picked by the machine.
- **ProxMenux Monitor, for what the Proxmox API does not carry.** HexDeck
  already speaks the Proxmox API; the monitor on the node adds its ten
  categories of health check with a reason in words, the physical disks with
  their SMART verdict and temperature, the node's sensors, and the guests as
  it already collects them. Five read-only GETs. The verdicts are passed
  through as the monitor's own words rather than recomputed, because where
  its thresholds sit is a question for its settings page. Its API tokens,
  which it mints for exactly this purpose, stop verifying when its JWT
  secret is rotated, and the refusal says so.
- **The version where somebody is already looking.** The account menu and both
  settings navigations carry the version, with a link to the releases when a
  newer one is out. The line fetches by hand rather than through react-query,
  because the same bar is drawn on the kiosk and in the preview, where a
  component that demanded a query client would take the page down.
- **Homebox: a value that can stay off the wall, and a card per place.** The
  inventory card can leave its total out and can be told which currency to
  write, since one Homebox holds one. A second card draws what the things in
  each location are worth, or under each tag, largest first with a share of
  the whole. Homebox leaves out a place whose contents add up to nothing, so
  the card counts what it was given rather than implying that is all of them.
- **Homebox, both generations of its API.** API keys exist from Homebox 0.26
  onwards, so on an older installation the key field could not work at all
  and the card only said the key was rejected. Such an installation now
  signs in with an account instead, and the token, which the answer hands
  over with the word Bearer already on it, is kept until shortly before it
  runs out. The item export moved from ``/items/export`` to
  ``/entities/export`` in the same release: both are tried and the one that
  answers is remembered, because a 404 there is a version rather than a
  wrong address. ``AuthFailed`` may now carry a hint of its own, so the
  message can name the version that has what somebody is looking for. A
  refusal also repeats what Homebox itself said, because its two sentences
  mean different things: "authorization header or query is required" means
  no header arrived and a proxy is eating it, while "valid authorization
  token is required" means the key is unknown to that installation, which
  happens when the key has expired and when ``HBOX_AUTH_API_KEY_PEPPER``
  has been changed, since every key is stored as an HMAC under it.
- **AMP (CubeCoders), controller and single server alike.** Every call is
  ``POST /API/<Module>/<Method>`` with the session in the body. The
  instances of every target are flattened into one list; one server's
  numbers come through a login the controller proxies to it, at
  ``/API/ADSModule/Servers/<id>/API/...``. An installation without a
  controller has no ADSModule and is itself the game server, and that case
  falls back to its own status rather than showing nothing. AMP reports a
  failure as a 200 with a stack trace in it, which read as data looks like
  a server with no metrics, so it is recognised and raised.
- **A logo the collections carry only as a PNG is served anyway.** Every
  logo address the interface builds ends in ``.svg``, so a PNG-only service
  drew the grey box that means "no such logo". The proxy now falls back to
  the PNG and says what it really is in the answer.
- **Kubernetes, read-only, with a view role and nothing more.** Nodes,
  pods, deployments, the version, and the node metrics when metrics-server
  is there; when it is not, the usage rows are dropped and the card says
  why instead of drawing zeroes. Quantities are parsed as the units they
  are, so ``250m`` is a quarter of a core and ``1000M`` is not a gibibyte.
  A cordoned node is not a healthy node, a pod that has Succeeded is not a
  broken one, and a deployment scaled to nothing is not degraded.
- **OpenWrt, over the bus LuCI itself talks to.** JSON-RPC against
  ``/ubus``: the board, the system numbers, one interface's status and the
  stations on each radio, all read-only. The load averages are fixed-point
  and scaled by 65536, so a quiet router would otherwise read as a load of
  six thousand, and memory is judged by what is available rather than by
  what is free, because the page cache is not really in use. Permission
  denied arrives as ``[6]`` in a perfectly valid answer, which is also what
  an expired session looks like, so a denied call logs in again once.
- **UrBackup, through the exchange its own interface uses.** Salt, md5,
  PBKDF2 over the rounds the server names, md5 again with the random value:
  the password itself never leaves the process. An expired session is
  answered with a document that merely lacks the key that was asked for,
  so every call logs in again once before it gives up. A client whose image
  backups are switched off is not a client with a missing image backup, and
  how many days count as late is a setting, because a laptop that is away
  for a week is not a fault.
- **Elasticsearch and OpenSearch, by the colour they name themselves.**
  The health call, the index catalogue and the version, all read-only. The
  cards pass green, yellow and red through instead of inventing a verdict,
  and a one node cluster is told why it is yellow forever: a single node
  cannot place a replica anywhere. An API key and basic credentials at
  once is refused before anything is sent.
- **Shelly, both generations, on the local network.** ``/shelly`` decides
  which dialect a device speaks and the answer is kept for an hour. Gen 1
  counts energy in watt-minutes and Gen 2 in watt-hours, so the same
  device reads the same on either; a card that got that wrong would be
  sixty times out. One button that does the opposite of what the relay is
  doing, a read-only setting for a wall tablet, and clamps with nothing to
  switch get no button at all.
- **Steam, read-only and without artwork.** Three calls of the Web API:
  who the player is, what they played in the last fortnight, and how large
  the library is. A custom profile name is resolved once and kept for a
  day. No game picture is put in a row, because an address in a card is
  fetched by the browser and would tell Valve who is looking at the board.
  A private profile answers an empty list rather than an error, so the
  cards say that in words.
- **Minecraft, by the ping its own client uses.** Both editions: the Java
  server list ping over TCP, framed with VarInts written out by hand, and
  Bedrock's unconnected RakNet ping over UDP. Players, slots, version and
  the message of the day flattened out of the chat components it arrives
  in. No credential exists in either protocol, so none is asked for.
- **A host card that reads /proc itself.** Processors, memory, swap, load,
  uptime, the warmest sensor and one file system, out of the files the
  kernel writes, with no library and no agent. The processor share is the
  difference between two looks, kept in the card's cache. When
  `/host/proc` is mounted the numbers are the machine's; when it is not,
  the card says on its face that it is reading the container.
- **One updates card over several sources.** WUD, Cup and Watchtower gained an
  `updates()` hook that hands the rows they already build to a card that
  merges them with the newest release of the repositories somebody follows
  and with HexDeck's own version. Each source is asked on its own and a
  failure becomes a line of warning on the card, never the whole card.
- **A to-do card, the time of day on the agenda, and the public address.**
  The to-do list is kept on the server in the card's own options, with an
  address of its own like the notepad, and may be ticked by everyone who
  may see the board when the card says so. The iCal parser keeps the time
  of day, so the calendar card shows when, sorts inside a day and can
  leave out what is over. The public address card asks one keyless
  service once an hour and says when the address has changed.
