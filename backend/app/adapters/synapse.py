"""Matrix Synapse: users and rooms, reports waiting, and servers it cannot reach.

Synapse's admin API lives under ``/_synapse/admin`` and takes the access
token of a server admin as a Bearer token (Element: Settings, Help & About,
Access token). ``v1/server_version`` names the version, ``v2/users`` counts
the accounts (``total``), ``v1/rooms`` lists the rooms ordered by members,
``v1/event_reports`` the reported messages and
``v1/federation/destinations`` every server Synapse federates with.

⚠️ A destination is failing when ``failure_ts`` is set; ``retry_last_ts``
is the last failed try and ``retry_interval`` the wait before the next.
Synapse's documentation warns that sorting this list by anything but the
name loads the database, so it is read by name, a thousand at most, and
kept ten minutes.

⚠️ A report stays until somebody deletes it; there is no "handled".

⚠️ An unknown token is 401 ``M_UNKNOWN_TOKEN``; the token of a user who is
not a server admin is 403 ``M_FORBIDDEN``.

Checked against Synapse 1.163.0 running locally on 2026-10-15, with three
users, two rooms, a reported message, an admin token, a user's token and a
wrong one. The federation list was empty there; its shape is the one in
Synapse's admin API documentation.
"""

from __future__ import annotations

import time
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
    duration_short,
)

READ_SECONDS = 300
FEDERATION_SECONDS = 600
MOST_DESTINATIONS = 1000


def failing(destinations: list[Any]) -> list[dict[str, Any]]:
    return [one for one in destinations if isinstance(one, dict) and one.get("failure_ts")]


class SynapseAdapter(Adapter):
    kind = "synapse"
    label = "Matrix Synapse"
    category = "network"
    description = "A Matrix Synapse server: its users and rooms, the messages reported and waiting, and the servers it cannot reach."
    icon = "matrix"
    docs_url = "https://element-hq.github.io/synapse/latest/usage/administration/admin_api/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://matrix.example.com",
              help="The address the admin API answers at, usually the server's client address."),
        Field("token", "Access token", type="password", secret=True, required=True,
              help="Of a user who is a server admin; Element shows it under Settings, Help & About."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="server", label="Server", description="The users and rooms, the reported messages, the servers it cannot reach, and the version.",
                   renderer="value", default_size=(3, 2), refresh_seconds=300, metrics=("users", "rooms")),
        WidgetType(kind="rooms", label="Rooms", description="The rooms with the most members, with how many of them are on this server.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=8),)),
        WidgetType(kind="federation", label="Federation", description="The servers Synapse cannot reach, since when, and when it tries again.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None, cache: float = READ_SECONDS) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/_synapse/admin/{path}", params=params,
                                     headers={"Accept": "application/json", "Authorization": f"Bearer {config.get('token') or ''}"},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code == 401:
            raise AuthFailed("Synapse does not know the access token.", hint="The access token of a server admin.")
        if response.status_code == 403:
            raise AuthFailed("The token's user is not a server admin.", hint="Make the user an admin, or use an admin's token.")
        if response.status_code == 404:
            raise AdapterError("Synapse's admin API does not answer at this address.", code="http_error",
                               hint="A reverse proxy may keep /_synapse/admin away from the outside; use the address it is open at.")
        if response.status_code >= 400:
            raise AdapterError(f"Synapse answered with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Synapse did not answer with JSON.", code="not_json") from failure
        return answer if isinstance(answer, dict) else {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        version = (await self._get(config, ctx, "v1/server_version", cache=0)).get("server_version") or ""
        users = (await self._get(config, ctx, "v2/users", {"limit": 1, "guests": "false"}, cache=0)).get("total")
        return f"Synapse {version} answers with {int(users or 0)} users.".replace("Synapse  ", "Synapse ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "rooms":
            limit = max(1, min(int(options.get("limit") or 8), 50))
            rooms = await self._get(config, ctx, "v1/rooms", {"limit": limit, "order_by": "joined_members"})
            return self._rooms(rooms.get("rooms") or [], limit)
        destinations = (await self._get(config, ctx, "v1/federation/destinations", {"limit": MOST_DESTINATIONS}, FEDERATION_SECONDS)).get("destinations") or []
        if widget_kind == "federation":
            return self._federation(destinations)
        version = (await self._get(config, ctx, "v1/server_version", cache=3600)).get("server_version") or ""
        users = (await self._get(config, ctx, "v2/users", {"limit": 1, "guests": "false"})).get("total")
        rooms = (await self._get(config, ctx, "v1/rooms", {"limit": 1})).get("total_rooms")
        reports = (await self._get(config, ctx, "v1/event_reports", {"limit": 1})).get("total")
        return self._server(version, int(users or 0), int(rooms or 0), int(reports or 0), destinations)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _server(version: str, users: int, rooms: int, reports: int, destinations: list[Any]) -> WidgetData:
        down = failing(destinations)
        secondary: list[dict[str, Any]] = [{"label": "Rooms", "value": rooms, "metric": "rooms"}, {"label": "Reports", "value": reports}]
        if destinations:
            secondary.append({"label": "Unreachable", "value": f"{len(down)}/{len(destinations)}"})
        if version:
            secondary.append({"label": "Version", "value": version})
        return WidgetData(
            status="warn" if reports or down else "ok",
            primary={"label": "Users", "value": users, "metric": "users"},
            secondary=secondary,
            metrics={"users": float(users), "rooms": float(rooms)},
        )

    @staticmethod
    def _rooms(rooms: list[Any], limit: int) -> WidgetData:
        rows = []
        for room in rooms[:limit]:
            if not isinstance(room, dict):
                continue
            members, local = int(room.get("joined_members") or 0), int(room.get("joined_local_members") or 0)
            rows.append({
                "id": str(room.get("room_id")),
                "title": str(room.get("name") or room.get("canonical_alias") or room.get("room_id") or "?"),
                "subtitle": " · ".join(part for part in (str(room.get("canonical_alias") or "") if room.get("name") else "",
                                                         f"{local} here" if local != members else "", "encrypted" if room.get("encryption") else "") if part),
                "value": members,
                "status": "ok",
            })
        return WidgetData(status="ok", items=rows, meta={"empty": "No room"})

    @staticmethod
    def _federation(destinations: list[Any]) -> WidgetData:
        now = time.time()
        rows = []
        for destination in sorted(failing(destinations), key=lambda one: float(one.get("failure_ts") or 0)):
            since = ago(float(destination["failure_ts"]) / 1000)
            retry = float(destination.get("retry_last_ts") or 0) / 1000 + float(destination.get("retry_interval") or 0) / 1000
            rows.append({
                "id": str(destination.get("destination")),
                "title": str(destination.get("destination") or "?"),
                "subtitle": " · ".join(part for part in (f"failing for {since}" if since else "",
                                                         f"next try in {duration_short(retry - now)}" if retry > now else "") if part),
                "status": "bad",
            })
        return WidgetData(status="warn" if rows else "ok", items=rows,
                          meta={"empty": f"All {len(destinations)} servers reachable" if destinations else "No federation yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = time.time() * 1000
        destinations = [{"destination": "matrix.org", "failure_ts": None}, {"destination": "chat.example.net", "failure_ts": now - 7_200_000,
                                                                              "retry_last_ts": now - 600_000, "retry_interval": 3_600_000}]
        if widget_kind == "rooms":
            rooms = [{"room_id": "!a", "name": "Homelab", "canonical_alias": "#homelab:example.com", "joined_members": 214, "joined_local_members": 9},
                     {"room_id": "!b", "name": "Family", "joined_members": 5, "joined_local_members": 5, "encryption": "m.megolm.v1.aes-sha2"}]
            return self._rooms(rooms, max(1, int(options.get("limit") or 8)))
        if widget_kind == "federation":
            return self._federation(destinations)
        return self._server("1.163.0", 12 + fake.counter("synapse-users", tick, 0, 0.002), 31, 0, destinations)


ADAPTER = SynapseAdapter()
