"""NetBird: which machine is on the mesh, whose login has run out, and which key still works.

The REST API of the management server, the same one its own dashboard uses:
``/api/peers`` for the machines, ``/api/setup-keys`` for the keys that enrol
them, ``/api/groups`` for what they are grouped into, and
``/api/instance/version`` for the server's own version. All read-only.

⚠️ The token goes in as ``Authorization: Token nbp_...``, with the word Token
and not Bearer. The spec allows a Bearer JWT as well, but that is a browser
session; a personal access token, which is what somebody makes for this, is
refused outright if it is sent as Bearer.

⚠️ Works against NetBird Cloud (``https://api.netbird.io``) and against a
self-hosted management server, which is the same API on your own address. Only
the address differs, and the field says so.

⚠️ "Connected" in NetBird means the peer holds a connection to the management
service, not that the tunnel to any other peer is up. A laptop that is asleep
is simply not connected, and that is not a fault; a login that has expired is,
because the machine stays off the mesh until somebody authenticates it again.

⚠️ ``/api/instance/version`` is answered by a self-hosted server and not by the
cloud, which has no version of yours to report. Its absence is not an error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    ago,
    base_url,
    measured,
)

#: What a setup key's state is worth. "overused" still enrols nothing.
KEY_STATES = {"valid": "ok", "overused": "warn", "expired": "bad", "revoked": "unknown"}
PEERS_SECONDS = 30
KEYS_SECONDS = 300
VERSION_SECONDS = 3600
#: A key worth warning about before it goes.
EXPIRING_DAYS = 14


def _when(written: Any) -> float | None:
    """A NetBird timestamp as a moment, or nothing.

    ⚠️ They are RFC 3339 with a ``Z``, and a peer that has never been seen
    carries the zero time, which would otherwise read as 1 January year one.
    """
    text = str(written or "").strip()
    if not text or text.startswith("0001-01-01"):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _days_until(written: Any) -> float | None:
    moment = _when(written)
    return None if moment is None else (moment - datetime.now(UTC).timestamp()) / 86400.0


class NetBirdAdapter(Adapter):
    kind = "netbird"
    label = "NetBird"
    category = "network"
    description = "The machines on the mesh, the logins that have run out, and the setup keys that still work."
    icon = "netbird"
    #: Confirmed against a live instance on 2026-10-06.
    beta = False
    docs_url = "https://docs.netbird.io/api"
    fields = (
        Field("url", "URL", type="url", required=True, default="https://api.netbird.io",
              placeholder="https://api.netbird.io",
              help="NetBird Cloud answers at https://api.netbird.io. A self-hosted management server answers the same API at its own address."),
        Field("token", "Access token", type="password", secret=True, required=True,
              help="A personal access token from Team > Users > your account > Access tokens. It begins with nbp_ and NetBird shows it once."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False,
              help="For a self-hosted server behind a certificate of its own."),
    )
    widgets = (
        WidgetType(kind="mesh", label="Mesh", description="How many machines are connected, how many have a login that ran out, and the server's version.",
                   renderer="value", default_size=(3, 2), refresh_seconds=120, metrics=("connected", "expired")),
        WidgetType(kind="peers", label="Machines", description="Every machine on the mesh with its system, its version and when it was last seen.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120, metrics=("connected",),
                   options=(
                       Field("trouble_first", "What needs attention first", type="bool", default=True,
                             help="An expired login or a machine waiting for approval before the rest. Off sorts by name."),
                       Field("offline_only", "Only what is not connected", type="bool", default=False),
                       Field("group", "Group", placeholder="servers",
                             help="Only the machines in this group, by its name. Empty means all of them."),
                       Field("limit", "Entries", type="number", default=12),
                   )),
        WidgetType(kind="keys", label="Setup keys", description="The keys that enrol machines, with what is left of them and when they expire.",
                   renderer="list", default_size=(3, 2), refresh_seconds=900,
                   options=(
                       Field("hide_revoked", "Hide revoked keys", type="bool", default=True),
                       Field("limit", "Entries", type="number", default=8),
                   )),
    )

    # -- talking to the management server --------------------------------------

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float,
                   optional: bool = False) -> Any:
        token = str(config.get("token") or "").strip()
        response = await ctx.request(
            "GET", f"{base_url(config)}{path}",
            # ⚠️ "Token", not "Bearer": a personal access token sent as Bearer
            # is refused, and the message does not say which word was wrong.
            headers={"Authorization": f"Token {token}", "Accept": "application/json"},
            verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False,
        )
        if response.status_code in (401, 403):
            raise AuthFailed(
                "NetBird refused the access token.",
                hint="A personal access token from Team > Users > your account > Access tokens, which begins with nbp_. "
                     "A token that has expired is refused the same way, and the dashboard shows when each one does.",
            )
        if response.status_code == 404:
            if optional:
                # The cloud has no version of yours to report, and an older
                # self-hosted server has not got the endpoint. Neither is a fault.
                return None
            raise AdapterError(f"This server has no {path}.", code="no_such_path")
        if response.status_code >= 400:
            raise AdapterError(f"NetBird answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("NetBird did not answer with JSON.", code="not_json",
                               hint="The address is the management API: https://api.netbird.io for the cloud, "
                                    "or your own server's, not the dashboard's.") from failure

    async def _peers(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/api/peers", PEERS_SECONDS)
        return [one for one in (answer if isinstance(answer, list) else []) if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        peers = await self._peers(config, ctx)
        connected = sum(1 for one in peers if one.get("connected"))
        version = await self._get(config, ctx, "/api/instance/version", 0, optional=True)
        said = f", management {version.get('management_current_version')}" \
            if isinstance(version, dict) and version.get("management_current_version") else ""
        return f"NetBird answers with {len(peers)} machines, {connected} connected{said}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "keys":
            answer = await self._get(config, ctx, "/api/setup-keys", KEYS_SECONDS)
            return self._keys([one for one in (answer if isinstance(answer, list) else []) if isinstance(one, dict)], options)
        peers = await self._peers(config, ctx)
        if widget_kind == "peers":
            return self._peer_list(peers, options)
        return self._mesh(peers, await self._get(config, ctx, "/api/instance/version", VERSION_SECONDS, optional=True))

    # -- reading one peer ------------------------------------------------------

    @staticmethod
    def _peer_state(peer: dict[str, Any]) -> tuple[str, str]:
        """``(status, why)``: what is wrong with this machine, if anything."""
        if peer.get("login_expired"):
            return "bad", "Login expired"
        if peer.get("approval_required"):
            return "bad", "Waiting for approval"
        if peer.get("connected"):
            return "ok", "Connected"
        # Not connected is a machine that is off or asleep, which is ordinary.
        return "unknown", "Not connected"

    # -- the cards -----------------------------------------------------------

    @classmethod
    def _mesh(cls, peers: list[dict[str, Any]], version: Any) -> WidgetData:
        connected = sum(1 for one in peers if one.get("connected"))
        expired = sum(1 for one in peers if one.get("login_expired"))
        waiting = sum(1 for one in peers if one.get("approval_required"))
        secondary: list[dict[str, Any]] = []
        if expired:
            secondary.append({"label": "Login expired", "value": expired, "metric": "expired"})
        if waiting:
            secondary.append({"label": "To approve", "value": waiting})
        secondary.append({"label": "Machines", "value": len(peers)})
        said = version if isinstance(version, dict) else {}
        if said.get("management_current_version"):
            secondary.append({"label": "Server", "value": str(said["management_current_version"])})
        notice = ""
        if said.get("management_update_available") and said.get("management_available_version"):
            notice = f"A newer management server is out: {said['management_available_version']}."
        return WidgetData(
            status="bad" if expired or waiting else "ok",
            primary={"label": "Connected", "value": f"{connected} / {len(peers)}"},
            secondary=secondary,
            metrics=measured({"connected": float(connected), "expired": float(expired)}),
            meta={"notice": notice},
        )

    @classmethod
    def _peer_list(cls, peers: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        wanted = str(options.get("group") or "").strip().lower()
        order = {"bad": 0, "ok": 1, "unknown": 2}
        rows = []
        connected = 0
        for peer in peers:
            groups = [str((one or {}).get("name") or "") for one in peer.get("groups") or [] if isinstance(one, dict)]
            if wanted and wanted not in [one.lower() for one in groups]:
                continue
            colour, why = cls._peer_state(peer)
            connected += 1 if peer.get("connected") else 0
            if options.get("offline_only") and peer.get("connected"):
                continue
            seen = _when(peer.get("last_seen"))
            parts = [str(peer.get("os") or ""), f"v{peer.get('version')}" if peer.get("version") else ""]
            if not peer.get("connected") and seen:
                parts.append(f"seen {ago(seen)}")
            rows.append({"colour": colour, "row": {
                "id": peer.get("id"),
                "title": str(peer.get("hostname") or peer.get("name") or peer.get("ip") or "?"),
                "subtitle": " · ".join(part for part in parts if part)[:90],
                "value": str(peer.get("ip") or "") if colour != "bad" else why,
                "status": colour,
            }})
        if options.get("trouble_first", True):
            ranked = sorted(rows, key=lambda one: (order.get(one["colour"], 3), str(one["row"]["title"]).lower()))
        else:
            ranked = sorted(rows, key=lambda one: str(one["row"]["title"]).lower())
        trouble = sum(1 for one in rows if one["colour"] == "bad")
        return WidgetData(
            status="bad" if trouble else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 12))],
            primary={"label": "Connected", "value": f"{connected} / {len(rows) if not options.get('offline_only') else len(peers)}"},
            metrics=measured({"connected": float(connected)}),
            meta={"empty": "No machine is on this mesh." if not wanted else f"No machine is in {options.get('group')}."},
        )

    @classmethod
    def _keys(cls, keys: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        rows = []
        for key in keys:
            state = str(key.get("state") or ("valid" if key.get("valid") else "expired")).lower()
            colour = KEY_STATES.get(state, "unknown")
            if options.get("hide_revoked", True) and state == "revoked":
                continue
            left = _days_until(key.get("expires"))
            # A key that still works but not for much longer is worth a colour:
            # the machine enrolled with it is what stops working, days later.
            if colour == "ok" and left is not None and 0 <= left <= EXPIRING_DAYS:
                colour = "warn"
            limit = int(key.get("usage_limit") or 0)
            used = int(key.get("used_times") or 0)
            parts = [str(key.get("type") or ""), f"{used} of {limit} used" if limit else f"{used} used"]
            if left is not None and left >= 0:
                # ⚠️ Rounded, not truncated: a key that expires in six days is
                # 5.999 days away by the time the answer is read, and "5 d left"
                # on the day it was made reads as a card that cannot count.
                parts.append(f"{round(left)} d left")
            rows.append({"colour": colour, "row": {
                "title": str(key.get("name") or key.get("id") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": state,
                "status": colour,
            }})
        ranked = sorted(rows, key=lambda one: (order.get(one["colour"], 4), str(one["row"]["title"]).lower()))
        unusable = sum(1 for one in rows if one["colour"] in ("bad", "warn"))
        return WidgetData(
            status="bad" if any(one["colour"] == "bad" for one in rows) else "warn" if unusable else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 8))],
            primary={"label": "Keys", "value": len(rows)},
            meta={"empty": "This account has no setup keys."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = datetime.now(UTC)
        def stamp(days: float) -> str:
            return datetime.fromtimestamp(now.timestamp() + days * 86400, UTC).isoformat().replace("+00:00", "Z")

        peers = [
            {"id": "a", "hostname": "nas", "ip": "100.84.0.3", "os": "Debian 13", "version": "0.80.0",
             "connected": True, "last_seen": stamp(0), "groups": [{"id": "g1", "name": "servers"}]},
            {"id": "b", "hostname": "desk", "ip": "100.84.0.7", "os": "Windows 11", "version": "0.80.0",
             "connected": fake.flicker("nb-desk", tick, 0.6), "last_seen": stamp(-0.2),
             "groups": [{"id": "g2", "name": "workstations"}]},
            {"id": "c", "hostname": "laptop", "ip": "100.84.0.9", "os": "macOS 15", "version": "0.78.1",
             "connected": False, "last_seen": stamp(-9), "login_expired": True,
             "groups": [{"id": "g2", "name": "workstations"}]},
            {"id": "d", "hostname": "pi", "ip": "100.84.0.12", "os": "Raspberry Pi OS", "version": "0.80.0",
             "connected": False, "last_seen": stamp(-1.5), "groups": [{"id": "g1", "name": "servers"}]},
        ]
        if widget_kind == "peers":
            return self._peer_list(peers, options)
        if widget_kind == "keys":
            return self._keys([
                {"id": "k1", "name": "servers", "state": "valid", "type": "reusable", "used_times": 4,
                 "usage_limit": 0, "expires": stamp(120)},
                {"id": "k2", "name": "one laptop", "state": "valid", "type": "one-off", "used_times": 0,
                 "usage_limit": 1, "expires": stamp(6)},
                {"id": "k3", "name": "old demo", "state": "expired", "type": "reusable", "used_times": 11,
                 "usage_limit": 20, "expires": stamp(-30)},
            ], options)
        return self._mesh(peers, {"management_current_version": "0.80.0", "management_available_version": "0.80.0",
                                  "management_update_available": False})


ADAPTER = NetBirdAdapter()
