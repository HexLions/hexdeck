# Running HexDeck

Backups, restores, updating, and the one mount worth thinking about before you
make it.

## The data volume

Everything HexDeck cannot rebuild lives in `./data`:

File names inside the data directory keep their nexdeck names on purpose: a
nexdeck installation or backup is a HexDeck one without any change.

| | |
|---|---|
| `nexdeck.db` | boards, cards, accounts, connections, history |
| `secret.key` | generated when `HEXDECK_SECRET_KEY` is empty |
| `uploads/` | backgrounds and icons somebody uploaded |
| `boards/` | provisioned board files, if you use them |
| `backups/` | snapshots HexDeck wrote itself |
| `cache/` | fetched logos; safe to delete |

**The key and the database belong together.** Every stored API key, every
password of a connection and every second factor is encrypted with that key. A
database restored next to a different key comes up with every connection
unreadable, and there is no way back from that: the values are gone, and you
enter them again by hand.

## Backups

HexDeck writes a snapshot by itself every `HEXDECK_BACKUP_EVERY_HOURS` hours
(24 by default, `0` switches it off) and keeps the last five. You can also make
one at any time under **Settings → Backups**.

A snapshot is the database alone. To carry an installation somewhere else,
download the archive: it is a zip with a password, and it holds the database,
the key file and the uploads.

```
Settings → Backups → Download → set a password
```

⚠️ **The password is not recoverable.** HexDeck does not keep it and cannot
open the archive without it.

### Copies off the machine

A snapshot sits on the same disk as the database. To have a copy elsewhere
without downloading one by hand, mount a folder of another machine into the
container and name it:

```yaml
    volumes:
      - /mnt/nas/hexdeck-backups:/backup
    environment:
      - HEXDECK_BACKUP_COPY_DIR=/backup
      - HEXDECK_BACKUP_COPY_PASSWORD=a-long-password-kept-somewhere-else
```

Every scheduled snapshot and every backup made by hand then also goes there
as the same encrypted archive the Download button makes, named after the
snapshot (`hexdeck-automatic-…zip`). The last `HEXDECK_BACKUP_COPY_KEEP` (14)
stay; older ones HexDeck wrote are deleted, anything else in the folder is
left alone. Without a password nothing is copied, because the archive holds
the key file. **Settings → Backups** says where the copies go and whether the
last one worked; a share that is not mounted is said there and in the log,
and the snapshot itself is written anyway.

## Restoring

```
Settings → Backups → Restore → pick the archive, enter its password
```

Before anything is overwritten, HexDeck shows what is inside the archive: which
version wrote it, when, whether it carries a key file, and whether this
installation would ignore that key because `HEXDECK_SECRET_KEY` is set. Read
that screen. The combination "the archive brings a key" plus "this
installation has its own" is the one that leaves you with unreadable
credentials.

A restore stops the background services, holds every new database session,
writes a safety copy of the current state, and only then replaces the file. If
the safety copy cannot be written, the restore is refused; there is a
checkbox for the case where the safety copy is what is broken.

## Changing the secret key

A key that was shown somewhere it should not have been (a screenshot, a
pasted compose file) is changed like this, without entering any API key
again:

1. Generate a new key, for example with `openssl rand -base64 48`.
2. Set `HEXDECK_SECRET_KEY` to the new key and `HEXDECK_SECRET_KEY_PREVIOUS`
   to the old one. An installation that never set a key has its old one in
   `/data/secret.key`.
3. Start HexDeck. The log says how many stored secrets were moved to the new
   key: connections, the mail password, second factors, sign-in providers,
   Web Push.
4. Remove `HEXDECK_SECRET_KEY_PREVIOUS` and start again.

Everybody signs in again afterwards, since the sessions were signed with the
old key, and that is the point of changing it. A secret neither key opens is
left as it is and named in the log as a count; enter that one again. Backups
made before the change still need the old key.

## Updating

```
docker compose pull
docker compose up -d
```

The database is migrated on the way up. Migrations only ever add; a version
that ran once will not run again. When a start has migrations to run, it
first writes an automatic snapshot of the database as it was, named
`…-before-schema-N`; it counts among the five automatic ones that are kept.
**Take a backup first anyway**, an archive kept off the machine: the
migration is not the risk, the disk is.

Going back to an older image with a newer database is not supported. HexDeck
notices and says so in the log, and the columns a newer version added stay
where they are; whether the older version copes with them is not something it
can promise. Restore a snapshot from before the update instead.

## Prometheus

`/api/metrics` hands HexDeck's own state to Prometheus in its text format:
which cards fail (`hexdeck_card_up`, `hexdeck_card_failures`), how long their
reads take (`hexdeck_card_read_seconds`), how many browsers follow a board
(`hexdeck_stream_connections`), when the newest snapshot was written
(`hexdeck_backup_last_timestamp_seconds`) and whether the last copy off the
machine worked (`hexdeck_backup_copy_ok`). Each card is labelled with its
number, its kind and its board.

It is off until `HEXDECK_METRICS_TOKEN` is set, and then answers only a
request that sends that token:

```yaml
scrape_configs:
  - job_name: hexdeck
    metrics_path: /api/metrics
    authorization:
      credentials: the-value-of-HEXDECK_METRICS_TOKEN
    static_configs:
      - targets: ["hexdeck:8000"]
```

An alert on `hexdeck_card_up == 0` for longer than the card's interval, or on
`time() - hexdeck_backup_last_timestamp_seconds > 2 * 86400`, says what the
dashboard itself cannot say when nobody is looking at it.

## The Docker socket

The shipped `docker-compose.yml` mounts `/var/run/docker.sock`. That gives
HexDeck the container cards: containers, their state, start and stop and
restart, and their logs.

⚠️ **It is worth a thought before you make it.** The Docker socket is root on
that host in all but name: whoever can send commands through it can start a
container that mounts the whole filesystem. The `:ro` in the compose file does
not change this: it stops writes to the socket *file*, not the commands sent
through it.

What that means in practice:

* Only administrators can create a Docker connection in HexDeck, and only
  people with the "act" level on a board can press the buttons on its cards.
* Anybody who gets administrator in HexDeck can reach the host through it.
* If you do not want the container cards, delete the line. Everything else
  works without it.

When the socket is mounted but the cards say permission denied, the container
is not in the socket's group. Read the group on the host and pass it in:

```
stat -c %g /var/run/docker.sock     # e.g. 999
DOCKER_GID=999                      # into .env
```

## Getting back in

A forgotten password is set anew from the shell of the machine, with the
container running (Portainer: Containers → hexdeck → Console → `/bin/sh`):

```
docker exec -it hexdeck python -m app.tools.reset_password admin
```

That prints a new password once; sign in with it and change it under My
settings. `--password 'your own'` sets one of your choice. Every browser
session of the account is ended; its second factor is left alone.

Without shell access to the running container, start it once with a way
back in instead:

```
docker compose run --rm -e HEXDECK_RESCUE=1 HexDeck
```

That prints a one-time sign-in link, valid for fifteen minutes, and nothing
else; it does not start the server. Use it, set a password, and start
normally again. The link is written to the terminal only, never to the log.
