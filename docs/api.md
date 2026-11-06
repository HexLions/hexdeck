# API

HexDeck's own interface uses the same API that is open to you. The
interactive documentation lives at `/api/docs` on every installation.

## Authentication

- **Browser session:** a cookie, set by `POST /api/v1/auth/login`. Unsafe methods need the header `X-Nexdeck-Request: 1`, which a cross-site form cannot send.
- **API token:** `Authorization: Bearer nd_…`. Create one under My settings > API tokens. A token has the rights of its account.
- **Kiosk token:** `X-Kiosk-Token: nk_…` or `?kiosk=nk_…`. Reads one board, nothing else.

## Useful addresses

| Address | Purpose |
|---|---|
| `GET /api/v1/boards` | Boards the caller may open. |
| `GET /api/v1/boards/{slug}` | A board with pages, widgets and the latest live data. |
| `GET /api/v1/boards/{slug}/history` | Metric history of every widget for sparklines. |
| `GET /api/v1/stream?board={slug}` | Server-Sent Events: `widget`, `health`, `board`, `layout`, `log`, `notice`. |
| `POST /api/v1/widgets/{id}/actions/{action}` | Run a widget action with `{"params": {...}}`. Needs the act permission. |
| `GET /api/v1/widgets/{id}/data` | The latest data of one widget. A `forecast` says when a volume is full, a number reaches its line or a backup gets slower; a `cause` names what it hangs on that is down. |
| `GET /api/v1/widgets/{id}/history/long` | A widget's history over `days` (up to 730) as `[[ts, avg, min, max], ...]` per metric: hours up to 30 days, days beyond. `metric` narrows it to one. |
| `POST /api/v1/widgets/{id}/refresh` | Fetch right now. |
| `GET /api/v1/widgets/{id}/music/{view}` | The library of a music player card: `albums`, `artists`, `artist`, `album`, `playlists`, `playlist`, `search`, `shuffle`, and `mix` on Jellyfin and Emby. `id`, `q`, `sort` and `offset` narrow it down. Needs the act permission. |
| `GET /api/v1/widgets/{id}/audio/{track_id}` | The sound of one track, relayed by HexDeck with `Range` passed through. `quality` is `original`, `high` or `low`, `formats` lists what the browser plays, `start` is where converted sound begins. Needs the act permission. |
| `POST /api/v1/widgets/{id}/music/playlists` | Create a playlist on the media server with `{"name": "...", "track_ids": [...]}`. `POST` and `DELETE` on `.../playlists/{playlist_id}/tracks` add tracks (`track_ids`) and take entries out (`entries`); `PATCH` and `DELETE` on `.../playlists/{playlist_id}` rename and delete. Smart playlists on Plex are refused. Needs the act permission. |
| `GET /api/v1/chronicle` | The chronicle, newest first: `days` (1, 7, 30 or 365), `group` (`all`, `cards`, `versions`, `hexdeck`), `q` for words, `before` with the id of the last line for the page after. Administrators get every line, everybody else the lines about boards they may open. |
| `GET /api/v1/inventory` | Every connection, app tile, container and guest as `nodes` (with `parent` where an engine or hypervisor said so, `down`, addresses), and every `relation` with its `origin` (`auto`, `manual`, `suggested`) and `state` (`on`, `suggested`, `rejected`). `POST /api/v1/inventory/refresh` asks the engines and hypervisors now. Administrators only. |
| `POST /api/v1/relations` | Say that `child` runs on or needs `parent`, both as refs from the inventory (`integration:3`, `widget:12`, `container:1:plex`, `guest:5:101`). A circle, the same ref twice or a relation that is there already is refused. `POST .../{id}/confirm` says yes to a proposed one, `DELETE .../{id}` removes one set by hand and says no to any other, which HexDeck then never proposes again. Administrators only. |
| `GET /api/v1/diagnosis?ref=...` | Why one service is in trouble, by its ref from the inventory: `state` (`now`, `past` or `ok`), `since` and `until`, `reason`, the `cause` it hangs on that is down, the chronicle `lines` of the two hours before (each with `via` when it is about something it runs on), `above` and `below` in the inventory, its `cards` and `checks`, the `buttons` of that container and its `runbook`. `at` (epoch seconds) asks for a trouble that has ended...}` reads the last 100 lines of its container's log with secrets masked; `PUT /api/v1/diagnosis/runbook` with `ref`, `text` and `link` keeps the runbook, an empty one removes it. Administrators only. |
| `GET /api/v1/settings/daily-report` | When the daily report goes out, as `{"time": "07:00"}`. `PUT` changes it; administrators only. |
| `GET /api/v1/adapters` | Every adapter with its fields and widgets. |
| `GET /api/v1/apps` | The app catalog: apps with their addresses (`label`, `url`), the labels for `home` and `away`, and how many `tiles` follow each. Every member may read it. |
| `POST /api/v1/apps` | Add an app. `PATCH /api/v1/apps/{id}` changes one, and every tile that follows it changes with it; `DELETE` turns its tiles into tiles of their own first. Administrators only. |
| `POST /api/v1/maintenance` | Put a connection (`integration_id`) or an app tile (`widget_id`) into maintenance, for `minutes` or until `ends_at`, a week at most. Again for the same one moves its end. Until then nothing about it is told except rejected credentials, no outage is opened, and the availability bars draw the time grey. `GET` lists the running and recent ones, `DELETE /api/v1/maintenance/{id}` ends one now. Administrators only. |
| `POST /api/v1/maintenance/plans` | Maintenance that comes back every week, for a connection (`integration_id`) or an app tile (`widget_id`): on `days` (Monday 0 to Sunday 6), from `starts` (`"03:00"`) for `minutes` (a day at most), on the clock of `zone` (such as `"Europe/Berlin"`; empty for the server's own). Shortly before each start HexDeck writes an ordinary maintenance for that window, so the window behaves like one set by hand and ending it early leaves the plan alone. `GET` lists the plans (`?integration_id=` or `?widget_id=` for one), with the next window; `PUT /api/v1/maintenance/plans/{id}` changes the times, `DELETE` stops the plan, and a window that runs at that moment runs to its end. Administrators only. |
| `GET /api/v1/boards/{slug}/export` | The board as YAML. |
| `POST /api/v1/boards/import` | Create a board from YAML. |
| `POST /api/v1/auth/me/avatar` | Upload the own profile picture as `multipart/form-data` with the field `file`. |
| `DELETE /api/v1/auth/me/avatar` | Remove it again. |
| `GET /api/v1/settings/mail` | The installation's mail server; the password comes back masked. Administrators only. |
| `PUT /api/v1/settings/mail` | Change it. Sending the mask back keeps the stored password. |
| `POST /api/v1/settings/mail/test` | Send a test message, to the given address or to the administrator's own. |
| `GET /api/v1/settings/search` | The targets the bar may hand a typed word to. Every signed-in account may read them; the bar needs them on every page. |
| `PUT /api/v1/settings/search` | Change them. Administrators only. |
| `GET /api/v1/settings/search/suggestions` | Targets built out of the connected services. Administrators only. |
| `GET /api/v1/settings/appearance` | The accent colour and the style sheet of the installation. Every signed-in account may read them; every page is painted with them. |
| `PUT /api/v1/settings/appearance` | Change them. Administrators only. |

Errors come as `{"detail": {"code": "...", "message": "..."}}` with an
English message; the interface translates known codes.

## Example: read a widget from a script

```bash
curl -H "Authorization: Bearer nd_your_token" https://deck.example.com/api/v1/widgets/12/data
```

## Example: follow the live stream

```bash
curl -N -H "Authorization: Bearer nd_your_token" "https://deck.example.com/api/v1/stream?board=home"
```
