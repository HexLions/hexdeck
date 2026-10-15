# Integrations and widgets

An **integration** is one configured connection to a service: an address and
credentials. A **widget** shows one view of it on a board. Many widgets can
share one integration; the server asks the service once per widget interval
and caches identical requests for a few seconds.

Every adapter lives in one file under `backend/app/adapters/`. It declares
its connection fields, its widgets, and how to fetch, act and fake data.
The frontend never knows a service: every widget returns a `WidgetData`
that one of thirty-five renderers draws.

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
| Jenkins | overview, jobs | build a job (off by default, asks first) | a user name with an API token from the user's Security page, sent as HTTP Basic; with a token no CSRF crumb is needed. Jobs inside a folder are read one level deep. The button builds only a job the last answer listed, by its full name |
| Cronicle | overview, events | run an event (off by default, asks first) | an API key from Admin, API Keys in an `X-API-Key` header; reading needs no privilege, the button needs Run Events. Only the master server answers the API. An event's last result is its newest job in the history. Every answer is HTTP 200 and says success or failure in its `code` |
| Meilisearch | indexes, search | | a key of its own with stats.get, indexes.get, tasks.get and version |
| Synology DSM | system, volumes, disks, containers, vms | start, stop, restart; power on, shut down, reboot | user and password; containers and VM details through DSM's own interface calls |
| Unraid | system, array, guests | | API key (GraphQL) |
| Nextcloud | overview, active users, free space | | serverinfo token, or an administrator account |
| TrueNAS | system, pools, alerts | | API key; use https, where a Read-Only Administrator is enough; over http only a TrueNAS before 25.04 is read, with a full administrator's key, because later versions deprecate the REST API that http is limited to |
| Garage | cluster, nodes, buckets | | an admin token as Bearer, on the admin API port (3903), not the S3 port; `admin_token` from garage.toml or a token from `garage admin-token create`. Objects, bytes and free space need Garage 2.4; older 2.x shows the health alone |
| Proxmox Backup Server | datastores, host, tasks | | API token; DatastoreAudit on /datastore and Sys.Audit on /system |
| Kopia | snapshots, backups | | the server's user and password; the CSRF token of its start page is fetched and kept |
| Duplicati | backup jobs, backups | | the password of the web interface; Duplicati 2.1 or newer |
| Syncthing | folders, status | | API key |
| Backrest | backup plans, backups | back up a plan now | user and password of Backrest's own sign-in, or nothing when it is switched off |
| rclone | status, transfers | | the `--rc-user` and `--rc-pass` of `rclone rcd` (or rclone started with `--rc`) as HTTP Basic, or nothing with `--rc-no-auth`. Every call is a POST with a `{}` body. The counters run from the daemon's start, so an error stays counted until `core/stats-reset`; the card turns amber for errors, red only for a fatal one |
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
| MySpeed | last test, tests | | MySpeed's password in a `password` header when one is set, or nothing when its password level lets everyone read. The card turns amber when the last test falls short of the speeds set as expected in MySpeed |
| nexpulse | latest result (a running test live, a button to start one), history (speed or ping idle and under load), period summary, recent tests, latency under load (graded A+ to F) | | API key from nexpulse under Settings > API keys; a key that may only read fills every card, one with "Read and start tests" adds the button. nexpulse 0.1.1 or newer tells nexdeck which kind of key it is; on 0.1.0 the button is always shown |
| Traefik | overview, routers | | none, or basic authentication |
| Caddy | overview, sites, upstreams | | none, the admin API has no sign-in; a user name and password only behind a proxy with basic authentication. The admin API listens on localhost:2019 unless the Caddyfile says `admin 0.0.0.0:2019`, and it can rewrite the configuration, so keep it on an internal network; this adapter only sends GET. Upstreams report active requests and remembered failures, not availability |
| Nginx Proxy Manager | proxy hosts, certificates, status | | an account; the token is fetched and kept |
| nginx ignition | status, traffic, certificates | reload, start, stop; renew a certificate | an API token, which carries the permissions of the user who made it: one that may only read fills every card, one that may write adds the buttons, which are offered only where they are the thing to press. Traffic needs the statistics switched on in nginx ignition, 2.47.0 or newer |
| OPNsense | system, gateways | | API key and secret |
| pfSense | system, interfaces | | API key of the package pfSense-pkg-RESTAPI |
| FortiGate | system (processor, memory, sessions, FortiOS version), interfaces (link, address, speed, traffic; the named ones turn the card red without a link), IPsec tunnels (by their phase 2 selectors), HA cluster (primary, configuration in sync) | | the token of a REST API administrator with a read-only profile and nexdeck under Trusted hosts, sent as a bearer token; an optional VDOM. Beta: not yet run against a FortiGate |
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
| Keycloak | realm, failed sign-ins, sessions by client | | a confidential client of the realm with Service accounts roles on, its service account given `view-users`, `view-realm`, `view-clients` and `view-events` of realm-management; HexDeck signs in with client credentials and keeps the token while it lasts. Failed sign-ins need the realm to record events; with events off the card says so instead of showing zero |
| Matrix Synapse | server, rooms, federation | | the access token of a server admin, as a Bearer token, to `/_synapse/admin`; a reverse proxy often keeps that path from the outside, so use an address where it is open. The federation list is read by name, a thousand servers at most, as Synapse's documentation advises |
| CrowdSec | blocked addresses, blocked | | bouncer key from `cscli bouncers add`; it may read decisions and nothing else |
| mailcow | status, mail queue, mailboxes | | an API key in an `X-API-Key` header; the read-only one is enough, and HexDeck's address has to be in its allowed list unless Skip IP check is ticked. A wrong key counts as a failed login in mailcow's own fail2ban, so a card left with one can get HexDeck's address banned; the card says whether the key or the address was refused |
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
| M3U Editor | M3U Editor overview (streams the proxy carries, viewers, bandwidth, playlists, channels, guides; amber on a failed or old sync), active streams (channel, viewers by user name, running time, bandwidth; amber on a failover source or errors), playlists (channels, groups, last sync), guides (channels, last sync), recordings (running, planned, recent) | sync, on a playlist or a guide | an API token from API Tokens; the View ability is enough. The recordings need a DVR set up for the account. Custom, merged and alias playlists are listed but synced through their sources. Viewer addresses are never shown |
| Unmanic | workers, queue | | none |
| FileFlows | status, running | | optional access token |
| Maintainerr | collections, status | | none |
| Reclaimerr | Reclaimerr overview (titles marked for deletion, space they free, next automatic deletion, protections; amber on an old sync or scan or a failed task), leaving soon (the titles that go by themselves first, nearest deadline first, then the ones marked by hand, with size, days left and an open delete request of a user), tasks (schedule, last and next run) | postpone, keep, reset the timer, protect, on a title (a title marked by hand can only be protected); run now, on a task that neither deletes nor writes to Radarr and Sonarr | an rcl_ token from Settings > Integrations. Reading needs system:read, candidates:read, media:read, protections:read, tasks:read and events:read; the buttons need candidates:manage and tasks:run, and a card says which scope it misses. Sizes are known for whole movies and series only. What users vote for deletion is not in Reclaimerr's external API |
| Jellystat | libraries, most watched | | API key |
| Radarr, Sonarr, Lidarr, Readarr | queue, status, calendar | search missing | API key |
| Prowlarr | indexers, status | | API key |
| Jackett | overview, indexers | | the API key from the dashboard, for the Torznab list of indexers; which indexers fail and why sits behind Jackett's login, which the key does not open, so it needs the admin password, or nothing when Jackett has none. A wrong key is answered with HTTP 200 and Torznab error 100 |
| slskd | status, downloads (or uploads) | connect, clear the finished from the list | an API key listed under web > authentication > api_keys, sent as `X-API-Key` (a Bearer header is refused). Without Soulseek credentials slskd answers but is offline, and the status says so |
| autobrr | recent releases, grabbed | | API key from Settings > API keys |
| SABnzbd, NZBGet, qBittorrent, Transmission, Deluge | queue, speed | pause, resume | key or password |
| pyLoad | status, downloads | pause, resume | an API key from Settings > Users, sent as `X-API-Key`, on pyLoad from 2026 on; older versions take the user name and password as basic auth instead, which newer ones refuse |
| aria2 | queue, speed | pause, resume | the `--rpc-secret`, if aria2 was started with one; it travels as the first parameter of every call, not as a header. The address is the RPC one, `/jsonrpc` is added. Failed downloads are listed with aria2's reason and not counted in the queue |
| MeTube | fetch a video, downloads, download count | fetch an address you type in, save the file to your own machine, remove, try again | none; MeTube has no login of its own, so whoever reaches it may queue and delete |
| Sportarr | upcoming, missing events, events | | API key from Settings > General > Security |
| Tube Archivist | download queue, latest videos, video archive | start downloads | API token from Settings > Application, sent as Token |
| Home Assistant | entity, entity list | turn on/off, scenes, scripts, covers, locks | long-lived token; live over WebSocket |
| Homebridge | status, bridges, updates | | a user of the Homebridge UI without two-factor login (the UI has no API keys), or nothing when its login is off. The token is kept for the connection. Whether Homebridge runs is known only when the UI runs it under hb-service, as in the official images. The child bridges' HomeKit setup codes are never read out |
| openHAB | things, items | switch a Switch Item on or off | an API token from an administrator (Profile, Create API token) as a Bearer token, needed for the Things; the Items are readable without one while anonymous access stays on, as it is by default. The items card names Items or Groups, a Group standing for its members. The switch sends `ON` or `OFF` only to a Switch Item the card lists |
| ESPHome | overview, devices | | none, or the dashboard's user name and password as basic auth (ESPHome Device Builder). Reads `/devices` and `/ping`, which the old built-in dashboard has too, but only without a password. Online is the dashboard's own guess from mDNS and ping |
| Node-RED | status, flows | | none, or a user of the editor when adminAuth is set; the token is asked with the scope read, which a read-only user is allowed. Missing node types are found by comparing the flows with the installed nodes, because the flow state says started even while Node-RED waits for them |
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
| InfluxDB | status, tasks, buckets | | an API token (Load Data, API Tokens) that may read the organisation's buckets and tasks, as `Authorization: Token`, and the organisation's name. InfluxDB 2's API only. A task's failure shows InfluxDB's own error; the system buckets are left out |
| Alertmanager | alerts, firing alerts, silences | | none, or basic auth when its web configuration or a proxy asks for it. API v2; only the version and the cluster are read from /api/v2/status, never the configuration it carries |
| Grafana | alerts, status | | service account token; needs unified alerting, so Grafana 9.0 or newer |
| GoatCounter | visitors, pages, referrers | | a token from User, API with the permission Read statistics, as a Bearer token. GoatCounter allows 500 API requests an hour per token, so the cards keep their answer ten minutes. A referrer without a name is direct traffic |
| JSON API | value (with up to four badges from the same answer), list | | optional bearer token |
| iCal feed | events | | feed address |
| Paperless-ngx | archive, latest documents | | API token from the user profile |
| Papra | documents (count, size, trash), latest documents (with tags), organizations | | an API key from API keys in the user menu with Read organizations and Read documents; the organization by name or ID, or the first the key can see. Beta: run against a fresh Papra, not yet one in use |
| Mealie | meal plan, shopping list, kitchen | | API token from Profile > Manage your API tokens |
| Grocy | stock to watch, pantry | | API key from Manage API keys |
| KitchenOwl | shopping list, meal plan | | a long-lived token from Settings, Sessions, as a Bearer token. A household and a list are picked by name, empty takes the first. Every installation describes its API at `/api/openapi` |
| Vikunja | due tasks, tasks | | API token with read_all for tasks and projects; the time zone for "today" is a field, because a token cannot read the user's own |
| Kimai | time entries, booked time | stop a running timer | API token from Profile > API access |
| Dawarich | distance, distance by month | | API key of the account; distances follow Dawarich's hourly calculation |
| wger | weight, weigh-ins | | API key from the profile, sent as Token |
| Firefly III | money, subscriptions, budgets | | personal access token from Profile > OAuth |
| Actual Budget | this month (left to budget, budgeted, spent, overspent categories), accounts (balances, on and off budget), categories (spent of the budget, overspent first) | | through [actual-http-api](https://github.com/jhonderson/actual-http-api), a second container next to Actual, because Actual has no API of its own: its URL, its API_KEY and the budget's sync ID (Settings > Show advanced settings), and for an end-to-end encrypted budget its password. Reads only |
| evcc | energy, charging | | none; the state is readable without a password |
| OpenDTU | solar, inverters | | none while read-only access is on (OpenDTU's default); otherwise HTTP authentication as `admin` with the password of the web interface. An inverter that does not answer at night is shown as asleep, not as a fault; OpenDTU's own hints (radio not connected, no time, default password) colour the card |
| Fronius | solar, energy flow, inverters | | none; the local Solar API v1 of the inverter or its Datamanager, which a Gen24 needs switched on under Communication. The grid is positive while the house takes from it, the battery while it gives. A Gen24 has no energy of the day, so the card shows the total instead |
| BookOrbit | reading now, recently added, library | | user and password; BookOrbit has no API keys and allows five sign-ins a minute, so the token is kept |
| Ghostfolio | portfolio, holdings | | security token of the account from Settings > Access; it is exchanged for a JWT that lasts 180 days |
| Homebox | inventory, value by place, warranties | | API key from Profile > API Keys, on Homebox 0.26 and later; Homebox shows it only once. On 0.25 and older, which has no API keys, a user name and password. The item export is at /entities/export from 0.26 and /items/export before it, and whichever answers is remembered |
| PhotoPrism | photo library, recently added | start indexing | app password from Settings > Account > Apps and Devices; a client access token cannot list photos |
| Tandoor Recipes | meal plan, shopping list | tick an entry off | API token from Settings > API; the scope read is enough to look, ticking off needs read write |
| Wallos | next payments, subscription costs | | API key from the profile; it goes in the body of a POST, never in the address |
| Weather (Open-Meteo), RSS feeds, Calendar, Basics | current, headlines, upcoming, clock, notes, bookmarks, iframe, app tile, host, updates, status, notices, to-do | | none; the host card reads /proc, and the machine's own when /proc and /sys are mounted under /host |
| AMP (CubeCoders) | servers, server, panel | start, stop or restart a server | a panel account's user name and password; AMP has no API tokens, and an account with two-factor cannot be used unattended. One server's numbers come through a login proxied by the controller |
| Pterodactyl | overview, servers | start, stop, restart a server (off by default, asks before stopping) | a client API key from Account, API Credentials (`ptlc_...`), as a Bearer token; the card sees the servers that user can reach. Each server's live state comes from its node through the panel; a node that does not answer marks that server, not the card. Buttons act only on a server the panel lists |
| Arcane | overview, environments, projects, containers, image updates | start, stop and restart a container; start, stop, redeploy and update a project; update a container to its newer image | API key from Settings > API keys; environments:list, containers:list and projects:list for the cards, images:list, volumes:list and networks:list for the overview's counts, and containers:start, containers:stop, containers:restart, projects:deploy, projects:down, projects:update and image-updates:check for the buttons; Arcane 2.14 |
| Dockhand | overview, environments, stacks, containers, image updates | | API token from Profile > API tokens, or nothing when Dockhand's sign-in is switched off; a token may do everything its user may, and in the free edition every user may do everything, though the cards only read; Dockhand 1.0 |
| FileBrowser Quantum | storage | | an API token with the permission api, made in FileBrowser's settings; shares are counted when the token may read them |
| FreshRSS | feed reader, unread, unread by feed | | user name and the API password from Settings > Profile, not the sign-in password; API access allowed under Administration > Authentication |
| IMAP | unread mail, latest mail, folders | | user name and password or app password of the mailbox, over TLS, STARTTLS or, on a trusted network, unencrypted; no OAuth2, so Microsoft 365 is mostly out of reach. Reads counts and the sender, subject and date of a mail, never its text, and never marks anything as read |
| Linkding | bookmarks, recent bookmarks | | REST API token from Settings > Integrations; each account sees its own bookmarks only |
| Memos | memos, open tasks | | a personal access token from Settings > My account > Access tokens, sent as Bearer. The instance profile answers without one, so the connection test asks for a memo. Read from the OpenAPI spec of v0.31.0 |
| Trilium Notes | overview, notes | | an ETAPI token from Options > ETAPI, sent in `Authorization` as it is, without Bearer. The counts need Trilium 0.94 or newer; older ones show only the version. The note count includes Trilium's own help notes |
| Wiki.js | overview, pages | | a full-access API key from Administration > API Access, with the API turned on there, as Bearer. A wrong key is read as a guest's rather than refused, so the connection test asks the system info, which only an administrator gets. Wiki.js 2; the 3.0 betas are not supported |
| BookStack | overview, changed last | | an API token from the user's profile (API Tokens), as `Authorization: Token <id>:<secret>`; the user's role needs the permission Access system API. The counts and pages are what that user may see |
| Mailpit | Mailpit overview (unread, all mail, accepted and rejected since the start, database size; amber while chaos is on), latest mail (sender and subject, unread highlighted, with Mailpit's own search) | | none, or the user and password of Mailpit's own login (MP_UI_AUTH or --ui-auth-file); with a webroot the URL includes it. Reading never marks a mail as read |
| NeutArr | NeutArr overview, hunted apps | | the API key from NeutArr's User page, sent as a header; NeutArr is the continuation of Huntarr |
| nexbeat | requests (waiting, running, failed, arrived this week), library (artists and albums in Lidarr or nexcrate), requests to approve (with covers) | approve, turn down | API token from nexbeat 1.2.0 or newer under Profile > API tokens; a read-only token fills every card, approving needs an administrator's token that may write. A user's token counts only that user's requests |
| nexcrate | library (there, wanted, on its way, stuck, as a ring), downloads (progress and time left), stuck downloads (needing you first, with the buttons nexcrate offers), coming up, just arrived (posters and covers), why is it missing (nexcrate's reason per wanted title), storage, findings | try again, search again, remove and search again, clear, on a stuck download | paired from the connection sheet: nexdeck shows a code, the owner confirms it in nexcrate and the key arrives by itself; a key made in nexcrate's settings works too. Every card reads with "read"; the buttons need "operate" |
| nexlore | nexlore overview, tasks, changed last, daily note | tick a task off, capture a line into the inbox, add a line to the daily note | an API token from My account > Connections > API tokens, after the operator has switched API tokens on; Read fills the cards, Write also ticks off and captures; the daily note needs Write, because it is made from the space's template when it is missing |
| nexsift | nexsift inbox, newest lines | | an API key from Settings > API keys, after switching on Allow API keys; it may only read numbers and titles, never the text of a message |
| nexcanvas | boards (the ones changed last with a picture each, how many changed today and this week), board (one board as a picture that renews itself) | | an API token from My account > Connections, after the operator has switched on API tokens under Settings > Server > API; it only reads, and a board the token may not read answers like one that does not exist |
| nexbrand | overview (clients, those with a published version, projects), latest versions (drafts first, then the newest published ones) | | an API token from My account > Connections, after the operator has switched on API tokens; the level Read is enough; colours, fonts, logos and notes never reach a card |
| nexpaper | inbox (the oldest waiting documents, yellow by count or age), deadlines (the next 14 days), filing (documents, this week, being read) | | an API key with the scope Read from My account, after the operator has allowed API keys under Settings > API; needs the nexpaper that has `GET /api/v1/dashboard` |
| nexdiary | diary (streak, written today, entries and notes of the month), time capsule (days until the next one opens) | | an API token from My account > Connections, after the operator has switched on API tokens under Settings > API; numbers only, never a word of the diary; needs the nexdiary that has `GET /api/v1/dashboard` |
| nextrmnl | status, sessions, history, connections | | read-only API key from nextrmnl 0.3.0 or newer, with API keys switched on |
| OctoPrint | printer, print job | | application key from User Settings > Application Keys; its user needs the permission Status (groups Users or Read-only) |
| Spoolman | overview, spools | | none, Spoolman has no sign-in; a user name and password only behind a proxy with basic authentication. A spool without a known weight shows no bar and never counts as running out |
| LubeLogger | overview, reminders | | a LubeLogger user as basic auth, or nothing when its login is off. The urgency is LubeLogger's own, from the thresholds in its settings; distances are shown without a unit because the API does not give one |
| Moonraker (Klipper) | printer, print history | | none on a trusted network; otherwise Moonraker's API key, sent as `X-Api-Key`. Progress follows the slicer's M73 through display_status; layers only when the slicer sends SET_PRINT_STATS_INFO |
| openmediavault | system, file systems, disks | | admin, or a user in the group openmediavault-admin; other users may sign in but read nothing. Three wrong passwords in five minutes lock the account in openmediavault until faillock resets it, so a refused password is tried again only every 15 minutes |
| Pangolin | status, sites, resources (public with their health, private with their destination) | | Integration API key of the organization with List Sites, List Resources and List Site Resources, plus the organization ID. Self-hosted, the Integration API needs `enable_integration_api` in config.yml and a route to port 3003; Pangolin Cloud at https://api.pangolin.net |
| PatchMon | PatchMon overview, hosts, operating systems | | a key of usage type API with the scope host: get, from Settings > Integrations > Auto-Enrollment & API (a GetHomepage key is refused); the URL has to be one named in PatchMon's CORS_ORIGIN |
| Qui | Qui overview, instances | | an API key, made under Settings > API keys; one key reads every qBittorrent behind Qui |
| ReadMeABook | overview, downloading, latest requests | | API token (rmab_) of an administrator from Profile > API Tokens; a user's token is turned away from the admin figures the cards read; ReadMeABook 1.2.0 or newer |
| Real-Debrid | Real-Debrid account (premium days, end, points, active torrents against the limit), torrents (progress and state), latest downloads, data used (a bar for each of the last 7, 14 or 31 days, the total and the hoster most of it came from), hoster limits (each hoster with a limit, used against what is left, and when it resets) | | the private API token from real-debrid.com/apitoken; Real-Debrid refuses many VPN and data centre addresses, and allows 250 requests a minute for all apps on an account together |
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
| Tasmota | device, sensors | switch a relay on or off | none, or the web admin password, sent as HTTP Basic for `admin` (never in the address). One `Status 0` command reads everything; a plug with two measuring channels shows their sum. Local address only |
| Steam | player, recently played, library | | Web API key from steamcommunity.com/dev, plus the 64-bit id or custom profile name. The profile's game details have to be public, or Steam answers an empty list |
| Minecraft | server, players | | none; the server list ping carries no credentials. Java over TCP (25565), Bedrock over UDP (19132); Java servers before 1.7 speak an older ping that is not supported |
| Hacker News | stories | | none |
| Miniflux | unread, failing feeds, feed reader | | API key from Settings > API Keys |
| Mastodon | server, account, notifications | | none for the server card, whose answers are public; a user token with read:accounts and read:notifications for the others, from Preferences > Development > New application. This week's posts and logins are the week in progress; a server can switch that activity off |
| Lemmy | instance, hot | | none; the site's counts and the hot posts of its own communities are public. Lemmy 0.19's API v3; a post opens on this instance, though it may come from another. NSFW posts are left out |
| Karakeep | recent bookmarks, reading list | | API key from Settings > API Keys |
| Linkwarden | recent links, links | | access token from Settings > Access Tokens |
| Readeck | overview, bookmarks | | an API token from Settings > API Tokens as Bearer; the role Bookmarks: Read Only is enough. Each count is the `Total-Count` header of a filtered list of one |
| YouTube | videos, from your subscriptions | | none for the channel feeds; a YouTube Data API key for the subscriptions card, whose account must keep its subscription list public |
| GitHub releases | releases | | none; sixty requests an hour per address |
| Share prices | prices | | none |
| Twitch | live | | client ID and secret of an application at dev.twitch.tv |
| Owncast | stream | | none for whether the stream is live; the admin password, as basic auth with the user admin, for the viewers, the peak and what is being sent. Since 0.3 the viewers are no longer public |
| Kodi | now playing, library | pause, play, stop | the web server's user name and password as HTTP Basic, with Allow remote control via HTTP on (Settings, Services, Control). JSON-RPC on `/jsonrpc`; a slide show is not counted as playing |
| PeerTube | instance, published last | | none; the server stats, the version and the newest local videos are public on every instance. The counts are the instance's own videos, not the federated ones. The card turns amber while abuse reports wait for a moderator |
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

Or it follows an app of the catalog (System > Apps): `options.app_id` names
the app and `options.address` the label of the address it opens, or `auto`.
Name, icon, description, address and check then come from the app, for an
app without an adapter as well. `auto` opens the address named for home when
the browser is in a trusted network (System > Sign-in providers > No
sign-in at home, which may name networks without switching that sign-in on)
and the one for the road everywhere else, including where HexDeck cannot
tell. The check runs on the home address, since HexDeck runs at home. An export names the app and carries
it along; an import over HTTP creates no app and turns a tile whose app is
missing into a tile of its own.

A card can be enlarged but never made smaller than its `default_size`;
`min_size` is what the server uses when it has to squeeze a new widget into
a tight spot.

## Renderers

`value`, `gauge`, `stats`, `list`, `nowplaying`, `calendar`, `text`,
`bookmarks`, `iframe`, `clock`, `weather`, `feed`, `log`, `chart`, `app`, `posters`,
`counters`, `camera`, `bars`, `ring`, `timeline`, `button`, `image`, `wol`,
`search`, `ask`, `player`, `roadmap`, `project`, `items`, `meter`, `segments`: thirty-three, the keys of `RENDERER_MIN` in
`backend/app/adapters/base.py`. A fetch may pick another renderer for its data through
`meta["renderer"]`; the media library card uses that for its icon row.

Every card drawn by `value`, `stats` or `gauge` that records at least one
metric gets the drawings of a number by itself: a dial, LED bars, seven
segments and a chart (a card drawn by `gauge` gets "The number" instead of
a dial, as the view `number`; `value` stays its dial), with "Full at" and "Empty at" for the dial and the
LEDs, the lines "Yellow from" and "Red from" (red below yellow means low
numbers are the bad ones), and the hours a chart reaches back: one, six or
twenty-four, as far as HexDeck keeps minute rows. Those lines colour the drawing only (`meta["level"]`); the card's
`status` stays what the service said.

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
- `metrics` are numbers recorded for sparklines; name them stably. They also
  give the card a **View** option that draws them as one chart (a number card
  from one metric on, any other card from two), so name them for a legend, not
  for a column.
- **A dial needs to know what full is.** A number in `%` is full at 100 by
  itself. A card that knows its own ceiling, such as a line's speed, passes it
  as `as_gauge(card, options, maximum=...)`: the dial and the LED bars both use
  it. A fetch that has to pick one number out of several for these views asks
  `single_number_view(options)`.
- **A drawing is offered, not written into the adapter.** A `list` card gets
  bars by itself. For a ring, set `ring=True` on the `WidgetType` **and** have
  the fetch write `meta["ring"] = ring_of(("Blocked", 400), ("Allowed", 600))`:
  only the fetch knows what the whole is, and slices taken from `secondary`
  would add up to something that does not exist.
- `demo()` must return believable, moving data for every widget kind; a test checks that.
- Add a test with recorded answers under `backend/tests/`, using `respx`.
