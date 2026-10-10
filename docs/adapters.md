# Integrations and widgets

An **integration** is one configured connection to a service: an address and
credentials. A **widget** shows one view of it on a board. Many widgets can
share one integration; the server asks the service once per widget interval
and caches identical requests for a few seconds.

Every adapter lives in one file under `backend/app/adapters/`. It declares
its connection fields, its widgets, and how to fetch, act and fake data.
The frontend never knows a service: every widget returns a `WidgetData`
that one of thirty-one renderers draws.

## Adapters in 0.12.0

| Adapter | Widgets | Actions | Credentials |
|---|---|---|---|
| Docker | containers, summary, load, logs | start, stop, restart, pause, resume | socket or TCP |
| Proxmox VE | node, guests, summary | start, shutdown, reboot | API token |
| Portainer | containers, summary | container actions | access token |
| Nomad | jobs, nodes, cluster | stop a job, scale a task group up or down | none on a cluster without ACLs, else an ACL token with node:read and namespace:read-job; the buttons need namespace:scale-job or namespace:submit-job |
| Cup | image updates, updates waiting | check now | none; Cup has no sign-in, so its port stays inside the network |
| Coolify | applications, deployments, status | | API token; the API has to be switched on in Coolify |
| Gitea, Forgejo | open issues or pull requests, Actions jobs, repositories | | access token with read access to repositories, issues and the user |
| GitLab | project, pipelines, merge requests | | a personal, project or group access token with read_api, sent as `PRIVATE-TOKEN`; none for a public project on GitLab.com. The project is its path, group/name, which goes into the address URL-encoded; a card can name another project than the connection's |
| Semaphore UI | last runs, automation | | API token of a user; the cards see the projects that user sees |
| Meilisearch | indexes, search | | a key of its own with stats.get, indexes.get, tasks.get and version |
| Synology DSM | system, volumes, disks, containers, vms | start, stop, restart; power on, shut down, reboot | user and password; containers and VM details through DSM's own interface calls |
| Unraid | system, array, guests | | API key (GraphQL) |
| Nextcloud | overview, active users, free space | | serverinfo token, or an administrator account |
| TrueNAS | system, pools, alerts | | API key; use https, where a Read-Only Administrator is enough; over http only a TrueNAS before 25.04 is read, with a full administrator's key, because later versions deprecate the REST API that http is limited to |
| Proxmox Backup Server | datastores, host, tasks | | API token; DatastoreAudit on /datastore and Sys.Audit on /system |
| Kopia | snapshots, backups | | the server's user and password; the CSRF token of its start page is fetched and kept |
| Duplicati | backup jobs, backups | | the password of the web interface; Duplicati 2.1 or newer |
| Syncthing | folders, status | | API key |
| Backrest | backup plans, backups | back up a plan now | user and password of Backrest's own sign-in, or nothing when it is switched off |
| Komodo | stacks, deployments, overview | restart a stack | API key and secret of a user, best a service user, with Read on stacks, deployments and servers and Execute on a stack to restart it; Komodo 2 |
| Netdata | raised alerts, load per node, alerts | | none; the agent's API is open, and a parent answers for every node that streams to it |
| Ollama | loaded models, installed models, models | unload a model | none; Ollama has no sign-in, so its port stays inside the network |
| Open WebUI | users, models, accounts | | API key of an administrator from Settings > Account > API keys; API keys have to be switched on in the admin settings, they are off by default |
| Watchtower | last update run | run now | API token (WATCHTOWER_HTTP_API_TOKEN), with metrics and update switched on in WATCHTOWER_HTTP_API_ENDPOINTS; the maintained fork nickfedor/watchtower |
| What's Up Docker | container updates, updates waiting | update a container WUD has a Docker trigger for, check now | API token from My Profile, WUD 9 or newer; read for the cards, write for the buttons |
| Zabbix | problems, open problems | acknowledge | API token of a user with read access to the host groups; the plain User role may acknowledge |
| Pi-hole | summary, top blocked | pause 5 min, enable | app password (v6) |
| AdGuard Home | summary, top blocked | pause 5 min, enable | user and password |
| Public address | address | | none; one call to ipapi.co or ipwho.is, kept for an hour |
| UniFi Network | network, console, devices, findings, wlans | | API key (Network 9.0+), or a local account without two-factor |
| Speedtest Tracker | latest, history | | API token |
| nexpulse | latest result (a running test live, a button to start one), history (speed or ping idle and under load), period summary, recent tests, latency under load (graded A+ to F) | | API key from nexpulse under Settings > API keys; a key that may only read fills every card, one with "Read and start tests" adds the button. nexpulse 0.1.1 or newer tells nexdeck which kind of key it is; on 0.1.0 the button is always shown |
| Traefik | overview, routers | | none, or basic authentication |
| Caddy | overview, sites, upstreams | | none, the admin API has no sign-in; a user name and password only behind a proxy with basic authentication. The admin API listens on localhost:2019 unless the Caddyfile says `admin 0.0.0.0:2019`, and it can rewrite the configuration, so keep it on an internal network; this adapter only sends GET. Upstreams report active requests and remembered failures, not availability |
| Nginx Proxy Manager | proxy hosts, certificates, status | | an account; the token is fetched and kept |
| OPNsense | system, gateways | | API key and secret |
| pfSense | system, interfaces | | API key of the package pfSense-pkg-RESTAPI |
| MikroTik | system, interfaces | | user with the read policy; needs RouterOS 7 with the REST service on |
| FRITZ!Box | connection, line | | none; TR-064 on port 49000, the part of it that answers without credentials |
| Tailscale | devices, status | | API access token from the admin console |
| Headscale | nodes, status | | API key from `headscale apikeys create` |
| Cloudflare | tunnels, zones, traffic | | a custom API token with Zone: Zone Read, Account: Cloudflare Tunnel Read and, for the traffic card, Account Analytics Read. The account ID is taken from the first zone when it is left empty. The traffic card reads the last 24 hours from the GraphQL Analytics API; its counts are sampled estimates |
| wg-easy | WireGuard clients, VPN | | user and password; wg-easy 15 or newer |
| NetBox | devices, prefixes, inventory | | API token, read-only is enough; a v2 token is pasted whole, starting with nbt_ |
| Gluetun | tunnel | | none, or the API key if the control server has roles |
| Technitium DNS | blocking, top blocked | | API token |
| NextDNS | blocking, top blocked | | API key and the profile ID |
| authentik | status, failed sign-ins | | API token of a service account with read access |
| CrowdSec | blocked addresses, blocked | | bouncer key from `cscli bouncers add`; it may read decisions and nothing else |
| Shlink | short URLs, visits | | API key from `shlink api-key:generate` |
| Blocky | blocking, top blocked | pause 5 min, enable | none; Blocky's API has no sign-in, so its HTTP port stays inside the network, and statistics.enable has to be on (Blocky 0.35 or newer) |
| Gatus | endpoints, endpoint health | | user and password of security.basic, or nothing when Gatus has no security section |
| NetAlertX | new devices, offline devices, devices | mark as known | API token from Settings > General, sent to the API port (20212); save the settings once so the token stays the same |
| Pocket ID | recent sign-ins, users | | an administrator's API key from Settings > API keys, or STATIC_API_KEY; without a browser, a login code from `pocket-id one-time-access-token` opens the session that makes a key |
| Reolink | cameras, camera (snapshot or live video), findings | | user and password of a device account; HTTP or HTTPS switched on in the device's port settings |
| Frigate | cameras, detections, status | | none on port 5000, the internal API; a user and a password on port 8971, the authenticated one, and behind a proxy in front of it. A viewer is enough. The token is fetched and kept |
| Plex | now playing, library, libraries, recently added (covers), findings, server load, users and devices, top of the week, music player | scan a library; play music in the browser, make and change playlists (smart ones stay read-only) | Sign in with Plex (PIN at plex.tv fills token and server address), or the owner's token |
| Jellyfin, Emby | now playing, library, libraries, recently added (covers), findings, users and devices, top of the week, music player | scan every library (one alone does nothing on these, measured); play music in the browser, instant mixes, make and change playlists | API key |
| Nexview | requests, library, instances, requests to approve | approve with target folder and profile, turn down | API key; approving needs an approver's key that may write |
| nexmail | unread mail (total and per mailbox, a mailbox whose sign-in fails is marked), latest mail (sender and subject, unread ones highlighted, each row opens the message in nexmail) | | API key from nexmail 0.17.0 or newer under Settings > API keys, with the mailboxes shared on it; the latest mail card needs the scope "Count, sender and subject" and is not offered for a key that may only count. The operator of nexmail has to allow API keys first |
| Seerr | requests, counts | approve, decline | API key |
| Overseerr, Jellyseerr | requests, counts | approve, decline | API key; same API as Seerr, listed under their own names |
| Ombi | overview, latest requests | | the API key under Settings > Configuration > General, in an `ApiKey` header. The request count answers without a key, so the connection test asks the issue count, which checks it |
| Tautulli | now playing, streams, most watched | | API key |
| RomM | platforms, recently added games, game library | | client API token with roms.read and platforms.read |
| Immich | archive, storage, users | | API key of an administrator |
| Bazarr | status, missing subtitles, recently fetched | | API key |
| Audiobookshelf | library, listening now | | API key |
| Navidrome | library, playing now | | account; the Subsonic API signs each request with a salted token |
| Komga | library, recently added | | API key, or the account on older versions |
| Kavita | library, recently added | | API key; the token is fetched once and kept |
| Calibre-Web | library, recently added | | account; it has no API, so the address of its own table view is used |
| Tdarr | queue, nodes | | optional API key |
| Unmanic | workers, queue | | none |
| FileFlows | status, running | | optional access token |
| Maintainerr | collections, status | | none |
| Jellystat | libraries, most watched | | API key |
| Radarr, Sonarr, Lidarr, Readarr | queue, status, calendar | search missing | API key |
| Prowlarr | indexers, status | | API key |
| Jackett | overview, indexers | | the API key from the dashboard, for the Torznab list of indexers; which indexers fail and why sits behind Jackett's login, which the key does not open, so it needs the admin password, or nothing when Jackett has none. A wrong key is answered with HTTP 200 and Torznab error 100 |
| slskd | status, downloads (or uploads) | | an API key listed under web > authentication > api_keys, sent as `X-API-Key` (a Bearer header is refused). Without Soulseek credentials slskd answers but is offline, and the status says so |
| autobrr | recent releases, grabbed | | API key from Settings > API keys |
| SABnzbd, NZBGet, qBittorrent, Transmission, Deluge | queue, speed | pause, resume | key or password |
| pyLoad | status, downloads | | an API key from Settings > Users, sent as `X-API-Key`, on pyLoad from 2026 on; older versions take the user name and password as basic auth instead, which newer ones refuse |
| MeTube | fetch a video, downloads, download count | fetch an address you type in, save the file to your own machine, remove, try again | none; MeTube has no login of its own, so whoever reaches it may queue and delete |
| Sportarr | upcoming, missing events, events | | API key from Settings > General > Security |
| Tube Archivist | download queue, latest videos, video archive | start downloads | API token from Settings > Application, sent as Token |
| Home Assistant | entity, entity list | turn on/off, scenes, scripts, covers, locks | long-lived token; live over WebSocket |
| Homebridge | status, bridges, updates | | a user of the Homebridge UI without two-factor login (the UI has no API keys), or nothing when its login is off. The token is kept for the connection. Whether Homebridge runs is known only when the UI runs it under hb-service, as in the official images. The child bridges' HomeKit setup codes are never read out |
| Uptime Kuma | monitors, summary | | API key (metrics endpoint) |
| Healthchecks | checks, checks up | | API key from the project settings; the read-only one is enough, and healthchecks.io works too |
| UptimeRobot | overview, monitors, open incidents | | the read-only API key from Integrations > API, sent as Bearer to API v3; an account key would also be able to change monitors. The free plan allows ten requests a minute for the whole account. A monitor's HTTP password, headers and key are never read |
| ChangeDetection.io | recent changes, watches | check all now | API key from Settings > API |
| n8n | workflows, last runs, summary | publish, unpublish | API key from Settings > n8n API |
| Beszel | hosts, host | | user and password |
| Glances | system, file systems, sensors | | optional password |
| Scrutiny | disks, disk health | | none |
| UPS (PeaNUT) | UPS, UPS details | | optional sign-in |
| Gotify | messages, message count | | client token (an application token may only write) |
| ntfy | messages | | topic, and a token for a protected one |
| Prometheus | query value, query list | | optional basic auth |
| Alertmanager | alerts, firing alerts, silences | | none, or basic auth when its web configuration or a proxy asks for it. API v2; only the version and the cluster are read from /api/v2/status, never the configuration it carries |
| Grafana | alerts, status | | service account token; needs unified alerting, so Grafana 9.0 or newer |
| JSON API | value, list | | optional bearer token |
| iCal feed | events | | feed address |
| Paperless-ngx | archive, latest documents | | API token from the user profile |
| Mealie | meal plan, shopping list, kitchen | | API token from Profile > Manage your API tokens |
| Grocy | stock to watch, pantry | | API key from Manage API keys |
| Vikunja | due tasks, tasks | | API token with read_all for tasks and projects; the time zone for "today" is a field, because a token cannot read the user's own |
| Kimai | time entries, booked time | stop a running timer | API token from Profile > API access |
| Dawarich | distance, distance by month | | API key of the account; distances follow Dawarich's hourly calculation |
| wger | weight, weigh-ins | | API key from the profile, sent as Token |
| Firefly III | money, subscriptions, budgets | | personal access token from Profile > OAuth |
| evcc | energy, charging | | none; the state is readable without a password |
| BookOrbit | reading now, recently added, library | | user and password; BookOrbit has no API keys and allows five sign-ins a minute, so the token is kept |
| Ghostfolio | portfolio, holdings | | security token of the account from Settings > Access; it is exchanged for a JWT that lasts 180 days |
| Homebox | inventory, value by place, warranties | | API key from Profile > API Keys, on Homebox 0.26 and later; Homebox shows it only once. On 0.25 and older, which has no API keys, a user name and password. The item export is at /entities/export from 0.26 and /items/export before it, and whichever answers is remembered |
| PhotoPrism | photo library, recently added | start indexing | app password from Settings > Account > Apps and Devices; a client access token cannot list photos |
| Tandoor Recipes | meal plan, shopping list | tick an entry off | API token from Settings > API; the scope read is enough to look, ticking off needs read write |
| Wallos | next payments, subscription costs | | API key from the profile; it goes in the body of a POST, never in the address |
| Weather (Open-Meteo), RSS feeds, Calendar, Basics | current, headlines, upcoming, clock, notes, bookmarks, iframe, app tile, host, updates, status, notices, to-do | | none; the host card reads /proc, and the machine's own when /proc and /sys are mounted under /host |
| AMP (CubeCoders) | servers, server, panel | start, stop or restart a server | a panel account's user name and password; AMP has no API tokens, and an account with two-factor cannot be used unattended. One server's numbers come through a login proxied by the controller |
| Arcane | overview, environments, projects, containers, image updates | start, stop and restart a container; start, stop, redeploy and update a project; update a container to its newer image | API key from Settings > API keys; environments:list, containers:list and projects:list for the cards, images:list, volumes:list and networks:list for the overview's counts, and containers:start, containers:stop, containers:restart, projects:deploy, projects:down, projects:update and image-updates:check for the buttons; Arcane 2.14 |
| Dockhand | overview, environments, stacks, containers, image updates | | API token from Profile > API tokens, or nothing when Dockhand's sign-in is switched off; a token may do everything its user may, and in the free edition every user may do everything, though the cards only read; Dockhand 1.0 |
| FileBrowser Quantum | storage | | an API token with the permission api, made in FileBrowser's settings; shares are counted when the token may read them |
| FreshRSS | feed reader, unread, unread by feed | | user name and the API password from Settings > Profile, not the sign-in password; API access allowed under Administration > Authentication |
| IMAP | unread mail, latest mail, folders | | user name and password or app password of the mailbox, over TLS, STARTTLS or, on a trusted network, unencrypted; no OAuth2, so Microsoft 365 is mostly out of reach. Reads counts and the sender, subject and date of a mail, never its text, and never marks anything as read |
| Linkding | bookmarks, recent bookmarks | | REST API token from Settings > Integrations; each account sees its own bookmarks only |
| Memos | memos, open tasks | | a personal access token from Settings > My account > Access tokens, sent as Bearer. The instance profile answers without one, so the connection test asks for a memo. Read from the OpenAPI spec of v0.31.0 |
| Trilium Notes | overview, notes | | an ETAPI token from Options > ETAPI, sent in `Authorization` as it is, without Bearer. The counts need Trilium 0.94 or newer; older ones show only the version. The note count includes Trilium's own help notes |
| Mailpit | Mailpit overview (unread, all mail, accepted and rejected since the start, database size; amber while chaos is on), latest mail (sender and subject, unread highlighted, with Mailpit's own search) | | none, or the user and password of Mailpit's own login (MP_UI_AUTH or --ui-auth-file); with a webroot the URL includes it. Reading never marks a mail as read |
| NeutArr | NeutArr overview, hunted apps | | the API key from NeutArr's User page, sent as a header; NeutArr is the continuation of Huntarr |
| nexbeat | requests (waiting, running, failed, arrived this week), library (artists and albums in Lidarr or nexcrate), requests to approve (with covers) | approve, turn down | API token from nexbeat 1.2.0 or newer under Profile > API tokens; a read-only token fills every card, approving needs an administrator's token that may write. A user's token counts only that user's requests |
| nexcrate | library (there, wanted, on its way, stuck, as a ring), downloads (progress and time left), stuck downloads (needing you first, with the buttons nexcrate offers), coming up, just arrived (posters and covers), why is it missing (nexcrate's reason per wanted title), storage, findings | try again, search again, remove and search again, clear, on a stuck download | paired from the connection sheet: nexdeck shows a code, the owner confirms it in nexcrate and the key arrives by itself; a key made in nexcrate's settings works too. Every card reads with "read"; the buttons need "operate" |
| nexlore | nexlore overview, tasks, changed last | tick a task off, capture a line into the inbox | an API token from My account > Connections > API tokens, after the operator has switched API tokens on; Read fills the cards, Write also ticks off and captures |
| nexsift | nexsift inbox, newest lines | | an API key from Settings > API keys, after switching on Allow API keys; it may only read numbers and titles, never the text of a message |
| nextrmnl | status, sessions, history, connections | | read-only API key from nextrmnl 0.3.0 or newer, with API keys switched on |
| OctoPrint | printer, print job | | application key from User Settings > Application Keys; its user needs the permission Status (groups Users or Read-only) |
| Spoolman | overview, spools | | none, Spoolman has no sign-in; a user name and password only behind a proxy with basic authentication. A spool without a known weight shows no bar and never counts as running out |
| Moonraker (Klipper) | printer, print history | | none on a trusted network; otherwise Moonraker's API key, sent as `X-Api-Key`. Progress follows the slicer's M73 through display_status; layers only when the slicer sends SET_PRINT_STATS_INFO |
| openmediavault | system, file systems, disks | | admin, or a user in the group openmediavault-admin; other users may sign in but read nothing. Three wrong passwords in five minutes lock the account in openmediavault until faillock resets it, so a refused password is tried again only every 15 minutes |
| Pangolin | status, sites, resources (public with their health, private with their destination) | | Integration API key of the organization with List Sites, List Resources and List Site Resources, plus the organization ID. Self-hosted, the Integration API needs `enable_integration_api` in config.yml and a route to port 3003; Pangolin Cloud at https://api.pangolin.net |
| PatchMon | PatchMon overview, hosts, operating systems | | a key of usage type API with the scope host: get, from Settings > Integrations > Auto-Enrollment & API (a GetHomepage key is refused); the URL has to be one named in PatchMon's CORS_ORIGIN |
| Qui | Qui overview, instances | | an API key, made under Settings > API keys; one key reads every qBittorrent behind Qui |
| ReadMeABook | overview, downloading, latest requests | | API token (rmab_) of an administrator from Profile > API Tokens; a user's token is turned away from the admin figures the cards read; ReadMeABook 1.2.0 or newer |
| Real-Debrid | Real-Debrid account (premium days, end, points, active torrents against the limit), torrents (progress and state), latest downloads | | the private API token from real-debrid.com/apitoken; Real-Debrid refuses many VPN and data centre addresses, and allows 250 requests a minute for all apps on an account together |
| rTorrent | queue, speed, torrents, rTorrent overview | pause, resume | the XML-RPC address (such as :8000/RPC2 of crazymax/rtorrent-rutorrent) or the address of ruTorrent, with the web server's user and password when one is set |
| SNMP (switches and network devices) | device, ports, port traffic, findings, PoE | | SNMPv3 user with SHA and AES, read only; or an SNMPv2c community, which travels unencrypted. Any vendor, standard MIBs only (IF-MIB, EtherLike, ENTITY, POWER-ETHERNET, LLDP); never writes |
| Tracearr | Tracearr overview, violations | | the public API key from Settings > API, which starts with trr_pub_ |
| NetBird | mesh, machines, setup keys | | a personal access token from Team > Users > your account > Access tokens, sent as `Authorization: Token nbp_...` and not as Bearer. NetBird Cloud at https://api.netbird.io, or a self-hosted management server at its own address; /api/instance/version is answered by the latter only |
| ProxMenux | health, health checks, node, guests, disks | | an API token from ProxMenux Monitor's own Settings, read-only, or nothing when the monitor has no authentication. Port 8008, https with a certificate it made itself, so the TLS check usually has to be off |
| Kubernetes | cluster, nodes, pods, deployments | | a service account token with a ClusterRole of view; nothing is written. The API server's own certificate is not trusted outside the cluster, so give HexDeck its authority or switch the TLS check off. Usage needs metrics-server, and its absence is said on the card |
| OpenWrt | router, WAN, wireless | | the router's own user and password, over /ubus, which uhttpd-mod-ubus serves and LuCI installs. A stock installation lets root read everything; another user needs a read role in /etc/config/rpcd, and the wireless card needs rpcd-mod-iwinfo |
| UrBackup | clients, backups, running now | start an incremental file backup | user name and password of a local server user; the password is folded into the hash the web interface itself sends. A server with no users yet needs neither |
| Elasticsearch | cluster, indices | | an encoded API key with the cluster privileges monitor and view_index_metadata, or basic credentials, or nothing on a cluster without security. OpenSearch answers the same calls |
| Shelly | device, outputs | switch an output on or off | none on a device without authentication; a Gen 1 device takes its admin password. Gen 2 and later use digest, which is not supported yet. Local address only, never the cloud |
| Steam | player, recently played, library | | Web API key from steamcommunity.com/dev, plus the 64-bit id or custom profile name. The profile's game details have to be public, or Steam answers an empty list |
| Minecraft | server, players | | none; the server list ping carries no credentials. Java over TCP (25565), Bedrock over UDP (19132); Java servers before 1.7 speak an older ping that is not supported |
| Hacker News | stories | | none |
| Miniflux | unread, failing feeds, feed reader | | API key from Settings > API Keys |
| Mastodon | server, account, notifications | | none for the server card, whose answers are public; a user token with read:accounts and read:notifications for the others, from Preferences > Development > New application. This week's posts and logins are the week in progress; a server can switch that activity off |
| Karakeep | recent bookmarks, reading list | | API key from Settings > API Keys |
| Linkwarden | recent links, links | | access token from Settings > Access Tokens |
| YouTube | videos, from your subscriptions | | none for the channel feeds; a YouTube Data API key for the subscriptions card, whose account must keep its subscription list public |
| GitHub releases | releases | | none; sixty requests an hour per address |
| Share prices | prices | | none |
| Twitch | live | | client ID and secret of an application at dev.twitch.tv |
| Wake-on-LAN | wake | wake | none; a MAC address and a network that carries the broadcast |

Adapters marked **beta** in the interface have not been confirmed against a
live instance yet. They are built against the documented API and recorded
answers; a report with the service's version is welcome.

### Texts

Everything an adapter says is English: field labels, help texts, widget names
and descriptions, and the labels of values, chips, rows and actions. The
interface translates them by their English wording from
`frontend/src/i18n/texts.de.json`; a guard in `backend/tests/test_guards.py`
fails when a new text has no German entry. Data that is not a label (names,
sizes, identifiers) passes through untouched.

A widget that draws itself from its options (clock, notes, bookmarks, embedded
page, app tile) sets `client_only=True`; the settings sheet then hides the
refresh interval. A field of type `timezone` is offered as a list of IANA
zones. `POST /api/v1/widgets/{id}/preview` runs a fetch with draft options
without saving; the settings sheet uses it for its live preview.

An app tile may follow an integration: pick one in its settings and the
tile's link and its reachability check take the integration's address on
every read, so a changed address is changed once. A link of the tile's own
still wins.

A card can be enlarged but never made smaller than its `default_size`;
`min_size` is what the server uses when it has to squeeze a new widget into
a tight spot.

## Renderers

`value`, `gauge`, `stats`, `list`, `nowplaying`, `calendar`, `text`,
`bookmarks`, `iframe`, `clock`, `weather`, `feed`, `log`, `chart`, `app`, `posters`,
`counters`, `camera`, `bars`, `ring`, `timeline`, `button`, `image`, `wol`,
`search`, `ask`, `player`, `roadmap`, `project`, `items`: thirty-one, the keys of `RENDERER_MIN` in
`backend/app/adapters/base.py`. A fetch may pick another renderer for its data through
`meta["renderer"]`; the media library card uses that for its icon row.

Images such as posters are never linked with a token in the browser: an adapter
hands out `proxy:/path`, and `GET /api/v1/widgets/{id}/image?path=` fetches it
from the service with the adapter's `image_headers`, cached for an hour. An adapter
may override `image_source(config, path, ctx)` to turn a path into another request,
for example a camera snapshot with a session token that must not be cached
(`cache_seconds=0`). Live video goes the same way: `stream_source(config, options,
ctx)` names an HTTP-FLV stream, and `GET /api/v1/widgets/{id}/stream` relays its
bytes to the browser, which plays them with Media Source Extensions. The server
relays at most twelve streams at once.

## Writing an adapter

```python
from .base import Adapter, Context, Field, WidgetData, WidgetType, base_url

class ExampleAdapter(Adapter):
    kind = "example"
    label = "Example"
    category = "monitoring"
    description = "What it shows."
    icon = "example"          # a dashboard-icons name
    fields = (
        Field("url", "URL", type="url", required=True),
        Field("api_key", "API key", type="password", secret=True, required=True),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="...", renderer="value",
                   default_size=(2, 2), refresh_seconds=30, metrics=("value",)),
    )

    async def test(self, config, ctx):
        payload = await ctx.get_json(f"{base_url(config)}/api/version", headers={"X-Api-Key": config["api_key"]})
        return f"Example {payload['version']} answers."

    async def fetch(self, widget_kind, config, options, ctx):
        payload = await ctx.get_json(f"{base_url(config)}/api/status", headers={"X-Api-Key": config["api_key"]})
        return WidgetData(primary={"label": "Load", "value": payload["load"], "unit": "%"}, metrics={"value": payload["load"]})

    def demo(self, widget_kind, options, tick):
        from . import demo as fake
        value = fake.walk("example", tick, 5, 60)
        return WidgetData(primary={"label": "Load", "value": value, "unit": "%"}, metrics={"value": value})

ADAPTER = ExampleAdapter()
```

Rules:

- Secrets are fields with `secret=True`; they are encrypted at rest and never returned by the API.
- Raise `AdapterError` (or `AuthFailed`, `Unreachable`) with an English message and a hint; the card shows both.
- `metrics` are numbers recorded for sparklines; name them stably. Two or
  more of them also give the card a **View** option that draws them all as one
  chart, so name them for a legend, not for a column.
- **A drawing is offered, not written into the adapter.** A `list` card gets
  bars by itself. For a ring, set `ring=True` on the `WidgetType` **and** have
  the fetch write `meta["ring"] = ring_of(("Blocked", 400), ("Allowed", 600))`:
  only the fetch knows what the whole is, and slices taken from `secondary`
  would add up to something that does not exist.
- `demo()` must return believable, moving data for every widget kind; a test checks that.
- Add a test with recorded answers under `backend/tests/`, using `respx`.
