# Notifications: channels, events, quiet hours

Everything that happens is a notice in the bell at the top right. A
**channel** carries the events its owner picks somewhere else as well: a
phone, a chat, a mailbox. Channels belong to a person, not to the
installation, and are made under **Settings → Notifications**.

## Channels

| Kind | What it needs |
|---|---|
| Web Push | Nothing to fill in: subscribed from the browser or the installed app. Browsers allow it on `https://` (and on `localhost`) only. |
| ntfy | The server's address, the topic, and a token if the topic is protected. |
| Gotify | The server's address and an application token. |
| Telegram | A bot made with @BotFather and the chat ID: write to the bot once, then read the ID from `getUpdates`. |
| Discord, Slack | An incoming webhook address. |
| E-mail | An address. Sent through the mail server set under **System → Mail server**. |
| Apprise | One or more Apprise URLs, for the hundred services Apprise speaks. Administrators only, because an Apprise URL can name any address. |

Every channel has a test button; tests are limited to a few a minute so a
button held down does not flood the other end. Secrets of a channel are
shown as `********` once saved.

## Events

A channel says only what it subscribes to. A new channel starts with the
ones nobody wants to miss: a service down, a service back, an action that
failed, and a UPS on battery.

| Event | When |
|---|---|
| A service stopped answering / is back | A reachability check of a card or app failed, or answers again. |
| A card turned red / is green again | Any card going from green or amber to red, at most once an hour per card, and back. |
| A card stopped working | A card that cannot be read at all any more, said once. |
| A service rejected its credentials | A key or password that stopped working. |
| An action failed / succeeded | A button on a card. |
| A new media request, a download finished | Seerr, the download clients. |
| A certificate is running out | Nginx Proxy Manager and the certificate cards, once a day. |
| A disk is filling up | The TrueNAS and Synology cards. |
| A UPS runs on battery / is back on mains power | The UPS (NUT) card: on battery with the runtime left, again when the battery runs low, and back. |
| A maintenance item is due | Projects with maintenance items. |
| A new HexDeck version | The update check on the About page. |

## Quiet hours and a daily summary

Each channel can keep quiet: give it a start and an end (`22:30` to `07:00`).
What arrives in between waits and goes out as one message when the quiet
hours end. Ticked, **A service that goes down still comes through** lets an
outage through at night anyway. A **daily summary** at a time of the owner's choice says
what is down, what is red and what happened in the last 24 hours.

The times are the server's: set `TZ` in the compose file (for example
`TZ=Europe/Rome`) or the summary arrives an hour or two off. Both are checked
once a minute, so they can be up to a minute late, never early. Nothing is
lost to a restart at night: what was held back are the notices in the bell,
and those are kept.

## When a message does not arrive

- Press the channel's test button: the answer says what the other end said.
- The bell has it but the channel does not: the channel does not subscribe to
  that event, or quiet hours are holding it.
- Web Push on `http://`: browsers refuse it. Put HexDeck behind https, see
  [Reverse proxies](reverse-proxy.md).
- **System → Log** shows each message that failed to go out, with the
  reason.
