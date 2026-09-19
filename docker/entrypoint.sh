#!/bin/sh
# Container start.
#
# HexDeck does not run as root. When the data directory is mounted from the
# host its ownership rarely matches the user inside the image, so the
# container starts as root, fixes the ownership of /data and then hands over
# to the unprivileged user "HexDeck". PUID and PGID pick the host user, as
# with the *arr images.
#
# When the Docker socket is mounted, the group that owns it on the host has
# to be granted too; DOCKER_GID does that (usually 999 or 998). Without it,
# the Docker integration answers "permission denied".

set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$1" = "uvicorn" ]; then
    case " $* " in
        *" --port "*) ;;
        *) set -- "$@" --port "${HEXDECK_PORT:-8000}" ;;
    esac
fi

# Already unprivileged (compose sets user:)? Then there is nothing to fix.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

if [ "$(id -g hexdeck)" != "$PGID" ]; then
    groupmod -o -g "$PGID" hexdeck
fi
if [ "$(id -u hexdeck)" != "$PUID" ]; then
    usermod -o -u "$PUID" hexdeck
fi

if [ -S /var/run/docker.sock ]; then
    SOCKET_GID=${DOCKER_GID:-$(stat -c %g /var/run/docker.sock)}
    if ! getent group "$SOCKET_GID" >/dev/null; then
        groupadd -o -g "$SOCKET_GID" dockersock
    fi
    usermod -a -G "$SOCKET_GID" hexdeck
fi

mkdir -p /data
if [ "$(stat -c %u /data)" != "$PUID" ] || [ "$(stat -c %g /data)" != "$PGID" ]; then
    echo "hexdeck: setting ownership of the data directory to $PUID:$PGID."
    chown -R "$PUID:$PGID" /data
fi

exec gosu hexdeck "$@"
