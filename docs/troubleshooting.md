# When something is wrong

Two places tell most of the story, both for administrators: **System → Card
status** lists every card being read, how long its last read took and why it
failed, with the reason the card itself keeps to the log; **System → Log** is
HexDeck's own log, with a deeper level that switches itself off again.

## HexDeck does not start

| The log says | What to do |
|---|---|
| `HEXDECK_SECRET_KEY is an example value or blank` | Set a random key: `openssl rand -base64 48`. The example in the README is refused on purpose, see [Two things to decide](../README.md#two-things-to-decide-before-the-first-start). |
| `HEXDECK_SECRET_KEY and HEXDECK_SECRET_KEY_FILE are both set` | Keep one of the two. The same goes for the other `*_FILE` variables. |
| `HEXDECK_SECRET_KEY_FILE cannot be read` | The file is not where the variable says, inside the container: check the secret's mount. |
| `This database was written by a newer HexDeck` | An older image on a newer database. Go back to the newer image, or restore a snapshot from before the update, see [Updating](operating.md#updating). |

## A card

| The card says | What it means |
|---|---|
| A stored secret cannot be read with this installation's key. | `HEXDECK_SECRET_KEY` changed. Put the old one back, or set it as `HEXDECK_SECRET_KEY_PREVIOUS` for one start so every stored key moves over, see [Changing the secret key](operating.md#changing-the-secret-key). |
| This card could not be read. The server log says why. | Something unexpected. **System → Card status** shows the reason. |
| The service did not answer in time. | The address answers slowly, or speaks something else: an integration pointed at the wrong port, such as UPS (PeaNUT) at NUT's port 3493, waits for an answer that never comes. |
| The service rejected the credentials. | A key that was withdrawn or ran out. Some services count a wrong key against HexDeck's address (mailcow, SFTPGo); fix it before the card asks again and again. |
| The error over numbers greyed out | The last read failed; the card keeps the last good numbers, greyed out, until a read works again. |

Cards that fail are read less often, up to every five minutes, so a service
that comes back may take that long to turn the card green; **Refresh** on the
card asks at once.

## The board does not move

The cards show numbers but change only on a reload: something in between
holds the live updates back. Behind nginx set `proxy_buffering off`, see
[Reverse proxies](reverse-proxy.md).

## Docker cards

| Symptom | Cause |
|---|---|
| Permission denied on `/var/run/docker.sock` | The container does not have the socket's group. The entrypoint detects it; where that fails, set `DOCKER_GID` to the group of the socket on the host (`stat -c %g /var/run/docker.sock`, usually 999 or 998). |
| The buttons refuse while the cards show | A read-only socket proxy, which is what it is for, see [The Docker socket](operating.md#the-docker-socket). |

## TrueNAS

- Use `https://` and an API key of a user with the **Read-Only
  Administrator** role. Over `http://` HexDeck does not send the key to the
  current API, because TrueNAS revokes a key that arrives over plain http.
- A UPS on USB to the NAS: turn on the UPS service, tick **Remote Monitor**
  and use the **UPS (NUT)** integration, with the service's Identifier as
  the UPS name.

## Nobody can sign in

```
docker exec -it hexdeck python -m app.tools.reset_password admin
```

prints a new password for `admin`. More in
[Getting back in](operating.md#getting-back-in).
