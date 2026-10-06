# What is left to do

Written on 2026-10-06, when the work moved to a dedicated host. Everything
above the line is a decision already taken and waiting to be carried out;
everything below it is an idea nobody has committed to.

The state it was left in: **0.22.0**, 163 integrations, 2316 backend tests and
518 frontend tests green, no adapter of this fork's own marked beta.

## Open, and worth doing

### 1. Look at the new drawings in a real browser

Six drawings landed in 0.20.0 and 0.21.0 and **none of them has been seen on a
screen**: heading, strips, in-and-out, flow, heatmap, topology, and the tabs
and group holders. They pass their tests, which proves the data reaches them
and not that they look right at 3 columns on a phone.

Put a board together with a heading, the status page set to *strips*, a card
with a traffic pair (UniFi, FRITZ!Box, Gluetun) switched to *in and out*, and
a group holding three cards. Then look at it on the wall tablet.

### 2. Say why Homebox refused

It works now; nobody wrote down what was wrong. The adapter's docstring lists
three causes it cannot tell apart: a key that expired, a changed
`HBOX_AUTH_API_KEY_PEPPER`, or a proxy eating the `Authorization` header. If
the answer surfaces, one sentence in `backend/app/adapters/homebox.py` saves
the next person the afternoon.

### 3. The thirty-five adapters still marked beta

All of them came from upstream and neither upstream nor this fork has seen
them answer. They are not this fork's to confirm by reading code: a beta badge
comes off when somebody watches the card fill with real data. The ones the
operator plausibly runs are worth doing first: `pbs`, `scrutiny`, `uptimekuma`,
`tailscale`, `unraid`, `opnsense`, `mikrotik`, `frigate`.

### 4. The README's screenshots are from 0.17.0

They show neither the host card, nor the status page, nor the updates list,
nor anything from 0.20.0 and 0.21.0. New ones are taken in demo mode with the
Playwright chromium already in `frontend/`, as `docs/screenshot-*.png`.

### 5. Regenerate the API keys that were pasted into a chat

Pi-hole, TrueNAS, Jellyfin, Immich, Portainer (three), Proxmox, qBittorrent,
UrBackup, Homebox. They were pasted while importing a Homepage config in
September. If that has not been done, it is the oldest open item here.

## Behind upstream, on purpose

Nothing of what this fork set out to take is missing any more. What remains is
upstream's and was never wanted here:

- **French** (`fr`). Spanish was done in 0.21.0 and cost 421 translated
  strings; French would cost the same. Upstream's `fr.json` covers upstream's
  own keys only.
- **Showcase mode**, which invents names and blurs cameras for a public demo.
  Two components had their calls into it taken out on the way in rather than
  the mode faked; adding it means porting `lib/showcase.ts` and the places
  that read it.

⚠️ The Spanish of 0.21.0 is this fork's own work and **no native speaker has
read it**. Corrections are the cheapest contribution anybody could make.

## Ideas nobody picked

Thirteen integrations were offered in September and left unchosen. They are
still the gaps worth filling, in rough order of how many people run them:

| | |
| --- | --- |
| Cloudflare | Tunnel health, zones, requests and threats over 24 hours |
| GitLab | Pipelines, merge requests and issues, beside Gitea and Forgejo |
| Vaultwarden | Users, organisations, version. Needs its admin token |
| Alertmanager | Firing alerts and silences, beside the Prometheus that is already here |
| MinIO | Buckets, space, objects, disks online, from its metrics endpoint |
| Caddy | Upstreams healthy or not, read from metrics rather than the admin API |
| Keycloak | Users, active sessions, clients |
| Woodpecker CI or Jenkins | Builds running, failed, queued |
| Pterodactyl or Pelican | Game servers: processor, memory, start and stop |
| Matrix (Synapse) | Users, rooms, server version |
| Mailcow | Queue, mailbox quotas |
| Memos or BookStack | Recent notes or pages |
| Aria2 or slskd | Downloads |

Deliberately refused, with the reason, so nobody re-opens them: Actual Budget
(its API is meant for the Node client), Zigbee2MQTT (needs an MQTT broker, not
HTTP), free ESXi (no REST without vCenter), Fail2ban (no API, and CrowdSec
covers the ground).

## How to pick up any of this

Read [CLAUDE.md](CLAUDE.md) first: it holds the rules this project is worked
under, the release procedure, and the traps that have already cost a day.
