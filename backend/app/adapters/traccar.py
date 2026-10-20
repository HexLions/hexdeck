"""Traccar: which trackers are online, and when each one was last heard from.

Checked against Traccar 6.16.0 running locally on 20.10.2026 (H2 database,
only the OsmAnd protocol on, ``status.timeout`` at 120 seconds), with three
devices, positions sent the way the OsmAnd app sends them, one device that
never reported, an API token, a wrong token and a wrong password.

⚠️ Two ways in. A token made in Traccar under Settings > Preferences >
Token (``POST /api/session/token``) goes as ``Authorization: Bearer``; an
e-mail address and password as HTTP Basic also work on every request. A
wrong or garbled token is not 401 but **400**, with a Java stack trace
(``ArrayIndexOutOfBoundsException`` in ``CryptoManager.verify``) as the body;
a wrong password and no credentials at all are 401.

⚠️ ``status`` is not what the words suggest. A device that sent a position
is ``online``; when nothing more arrives within the status timeout it turns
``unknown``, not ``offline``. ``offline`` was the device that never reported
at all (``lastUpdate`` null, ``positionId`` 0). Both mean "not heard from
now", so both count against the card. ``lastUpdate`` is when the server
heard from the device, not the time of the fix: a position dated ten minutes
back still made the device online.

⚠️ Speeds are knots, as everywhere in Traccar's API; the battery is
``attributes.batteryLevel`` in percent when the device sends one.
``/api/positions`` without parameters answers the latest position of every
device the account may see. A device marked ``disabled`` is left out of both
cards.
"""

from __future__ import annotations

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
)

KNOT_KMH = 1.852
NOT_TRACCAR = "This address answers, but not the way Traccar does."
WEB_ADDRESS = "Check the URL; it is Traccar's web address, without /api."


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _online(device: dict[str, Any]) -> bool:
    return device.get("status") == "online"


class TraccarAdapter(Adapter):
    kind = "traccar"
    label = "Traccar"
    category = "home"
    description = "Traccar GPS tracking: which trackers are online, when each was last heard from, how fast it moves and its battery."
    icon = "traccar"
    beta = True
    docs_url = "https://www.traccar.org/api-reference/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://traccar:8082"),
        Field("token", "API token", secret=True,
              help="Made in Traccar under Settings > Preferences > Token. Or leave it empty and sign in below."),
        Field("username", "E-mail", help="Only without a token."),
        Field("password", "Password", type="password", secret=True, help="Only without a token."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Trackers", description="How many trackers are online, and how many have gone quiet.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("online",)),
        WidgetType(kind="devices", label="Devices", description="Every tracker with its state, when it was last heard from, its speed and battery.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 30) -> Any:
        token = str(config.get("token") or "").strip()
        auth = None if token or not config.get("username") else (str(config.get("username")), str(config.get("password") or ""))
        if not token and auth is None:
            raise AuthFailed("Traccar needs a token, or an e-mail address and password.",
                             hint="Make a token in Traccar under Settings > Preferences > Token.")
        response = await ctx.request("GET", f"{base_url(config)}/api{path}", headers={"Authorization": f"Bearer {token}"} if token else None,
                                     auth=auth, verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code == 400 and token and "Exception" in response.text[:500]:
            # Traccar's answer to a token it cannot read: 400 and a stack trace, not 401.
            raise AuthFailed("Traccar could not read the token.",
                             hint="Make a new one under Settings > Preferences > Token; it may have expired or been cut short.")
        if response.status_code in (401, 403):
            raise AuthFailed("Traccar refused the token or the e-mail address and password.",
                             hint="Check them, or make a new token under Settings > Preferences > Token.")
        if response.status_code >= 400:
            raise AdapterError(f"Traccar answered with HTTP {response.status_code}.", code="http_error", hint=WEB_ADDRESS)
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError(NOT_TRACCAR, code="not_traccar", hint=WEB_ADDRESS) from error

    async def _devices(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        devices = await self._get(config, ctx, "/devices")
        if not isinstance(devices, list):
            raise AdapterError(NOT_TRACCAR, code="not_traccar", hint=WEB_ADDRESS)
        return [one for one in devices if isinstance(one, dict) and not one.get("disabled")]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        server = await self._get(config, ctx, "/server", cache=0)
        devices = await self._devices(config, ctx)
        version = server.get("version", "") if isinstance(server, dict) else ""
        return f"Traccar {version} answers with {len(devices)} tracker{'' if len(devices) == 1 else 's'}.".replace("  ", " ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        devices = await self._devices(config, ctx)
        if widget_kind == "summary":
            return self._summary(devices)
        positions = await self._get(config, ctx, "/positions")
        by_id = {one.get("id"): one for one in positions if isinstance(one, dict)} if isinstance(positions, list) else {}
        return self._list(devices, by_id, max(1, int(options.get("limit") or 10)))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(devices: list[dict[str, Any]]) -> WidgetData:
        online = sum(1 for one in devices if _online(one))
        quiet = len(devices) - online
        return WidgetData(
            status="warn" if quiet else "ok",
            primary={"label": "Online", "value": f"{online} / {len(devices)}"},
            secondary=[{"label": "Not heard from", "value": quiet}],
            metrics={"online": float(online)},
        )

    @staticmethod
    def _list(devices: list[dict[str, Any]], positions: dict[Any, dict[str, Any]], limit: int) -> WidgetData:
        items = []
        # The quiet ones first, then by name.
        for one in sorted(devices, key=lambda device: (_online(device), str(device.get("name") or "").lower()))[:limit]:
            position = positions.get(one.get("positionId")) or {}
            heard = ago(one.get("lastUpdate"))
            parts = [f"heard {heard} ago" if heard else "never heard from"]
            speed = _number(position.get("speed"))
            if speed:
                parts.append(f"{round(speed * KNOT_KMH)} km/h")
            battery = _number((position.get("attributes") or {}).get("batteryLevel"))
            if battery is not None:
                parts.append(f"battery {round(battery)}%")
            items.append({
                "title": str(one.get("name") or one.get("uniqueId") or "?"),
                "subtitle": " · ".join(parts),
                # The dot says online or not; a word here would not translate.
                "status": "ok" if _online(one) else "warn",
            })
        return WidgetData(status="warn" if any(item["status"] == "warn" for item in items) else "ok", items=items,
                          meta={"empty": "Traccar has no trackers yet."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        devices = [
            {"id": 1, "name": "Car", "status": "online", "lastUpdate": "2026-10-20T14:10:03.935+00:00", "positionId": 11},
            {"id": 2, "name": "Bike", "status": "unknown" if tick % 4 == 0 else "online", "lastUpdate": "2026-10-20T13:52:41.000+00:00", "positionId": 12},
            {"id": 3, "name": "Dog collar", "status": "online", "lastUpdate": "2026-10-20T14:09:30.000+00:00", "positionId": 13},
        ]
        positions = {
            11: {"speed": fake.walk("traccar-car", tick, 0, 40), "attributes": {}},
            12: {"speed": 0.0, "attributes": {"batteryLevel": 41.0}},
            13: {"speed": 1.2, "attributes": {"batteryLevel": fake.walk("traccar-dog", tick, 55, 80)}},
        }
        if widget_kind == "summary":
            return self._summary(devices)
        return self._list(devices, positions, max(1, int(options.get("limit") or 10)))


ADAPTER = TraccarAdapter()
