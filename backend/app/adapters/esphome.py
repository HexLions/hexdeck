"""ESPHome: the devices of the dashboard, which are online, which need an update.

The two plain HTTP endpoints that ESPHome's dashboard keeps for Home
Assistant and other tools, read-only: ``/devices`` for the configured
devices and ``/ping`` for whether each is online, as ``{"<file>.yaml": true,
false or null}``.

⚠️ Since 2026.9 the dashboard is no longer part of ESPHome: it is the
separate ESPHome Device Builder, which keeps both endpoints as they were.
With a user name and password set, it takes them as basic auth. The old
built-in dashboard asked for its password only through its login page; it
works here when it has none.

⚠️ Whether a device is online is the dashboard's own guess, from mDNS and
ping. ``null`` means it has not found out yet, which a dashboard without the
right to ping, as in a container without NET_RAW, says until mDNS answers.

⚠️ A device needs an update when the firmware it runs (``deployed_version``)
is older than the ESPHome of the dashboard (``current_version``). A device
never flashed from this dashboard has no deployed version and counts as
nothing.

Checked against ESPHome Device Builder 1.23.0 with ESPHome 2026.9.1 running
locally on 2026-10-10, with two configured devices, with and without a
password; online and offline could not be seen there without the devices.
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
    base_url,
    measured,
)

DEVICES_SECONDS = 60
#: The answer of /ping as a colour and a word.
ONLINE = {True: ("ok", "online"), False: ("bad", "offline"), None: ("unknown", "unknown")}


def _outdated(device: dict[str, Any]) -> bool:
    deployed, current = str(device.get("deployed_version") or ""), str(device.get("current_version") or "")
    return bool(deployed and current and deployed != current) or bool(device.get("update_available") and deployed)


class ESPHomeAdapter(Adapter):
    kind = "esphome"
    label = "ESPHome"
    category = "home"
    description = "The devices of ESPHome's dashboard: which are online and which run firmware older than the dashboard's ESPHome."
    icon = "esphome"
    docs_url = "https://esphome.io/guides/getting_started_hassio/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://esphome:6052"),
        Field("username", "Username", help="Only when the dashboard has a user name and password."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Overview", description="How many devices are online and offline, and how many need a firmware update.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("offline",)),
        WidgetType(kind="devices", label="Devices", description="Every configured device with its board and state, the offline ones first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("offline_only", "Only the offline ones", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=12))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        username = str(config.get("username") or "").strip()
        response = await ctx.request("GET", f"{base_url(config)}{path}", verify=not config.get("insecure"),
                                     headers={"Accept": "application/json"}, cache_seconds=cache, auth_errors=False,
                                     auth=(username, str(config.get("password") or "")) if username else None)
        if response.status_code in (401, 403):
            raise AuthFailed("ESPHome refused the user name and password." if username else "ESPHome asks for a user name and password.",
                             hint="The user name and password the dashboard was started with.")
        if response.status_code >= 400:
            raise AdapterError(f"ESPHome answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of ESPHome's dashboard, usually port 6052.")
        try:
            return response.json()
        except ValueError as failure:
            # The old dashboard with a password answers with its login page.
            raise AdapterError("ESPHome did not answer with JSON.", code="not_json",
                               hint="The address of ESPHome's dashboard, usually port 6052. An old built-in dashboard with a password is not supported.") from failure

    async def _devices(self, config: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        listed = await self._get(config, ctx, "/devices", DEVICES_SECONDS) or {}
        online = await self._get(config, ctx, "/ping", DEVICES_SECONDS) or {}
        devices = [one for one in (listed.get("configured") or []) if isinstance(one, dict)]
        return devices, online if isinstance(online, dict) else {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        devices, _ = await self._devices(config, ctx)
        return f"ESPHome answers, with {len(devices)} configured device{'s' if len(devices) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        devices, online = await self._devices(config, ctx)
        if widget_kind == "devices":
            return self._list(devices, online, bool(options.get("offline_only")), max(1, int(options.get("limit") or 12)))
        return self._summary(devices, online)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(devices: list[dict[str, Any]], online: dict[str, Any]) -> WidgetData:
        states = [online.get(str(one.get("configuration") or "")) for one in devices]
        up = sum(1 for state in states if state is True)
        down = sum(1 for state in states if state is False)
        outdated = sum(1 for one in devices if _outdated(one))
        secondary: list[dict[str, Any]] = [{"label": "Offline", "value": down, "metric": "offline"}, {"label": "Updates", "value": outdated}]
        unknown = len(devices) - up - down
        if unknown:
            secondary.append({"label": "Unknown", "value": unknown})
        return WidgetData(
            status="bad" if down else "warn" if outdated else "ok",
            primary={"label": "Online", "value": f"{up} / {len(devices)}"},
            secondary=secondary,
            metrics=measured({"offline": float(down)}),
        )

    @staticmethod
    def _list(devices: list[dict[str, Any]], online: dict[str, Any], offline_only: bool, limit: int) -> WidgetData:
        order = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}
        rows = []
        for device in devices:
            colour, word = ONLINE.get(online.get(str(device.get("configuration") or "")), ONLINE[None])
            if offline_only and colour != "bad":
                continue
            if colour == "ok" and _outdated(device):
                colour, word = "warn", f"update {device.get('deployed_version')} → {device.get('current_version')}"
            parts = [str(device.get("target_platform") or ""), str(device.get("board_id") or ""), str(device.get("area") or "")]
            rows.append({
                "id": device.get("configuration") or device.get("name"),
                "title": str(device.get("friendly_name") or device.get("name") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": word,
                "status": colour,
            })
        rows.sort(key=lambda row: (order.get(row["status"], 4), row["title"].lower()))
        return WidgetData(
            status="bad" if any(row["status"] == "bad" for row in rows) else "warn" if any(row["status"] == "warn" for row in rows) else "ok",
            items=rows[:limit],
            meta={"empty": "No device is offline" if offline_only else "No device configured"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        plug_down = fake.flicker("esphome-plug", tick, 0.2)
        devices = [
            {"configuration": "living-room.yaml", "friendly_name": "Living room sensor", "target_platform": "esp32", "board_id": "esp32dev",
             "area": "Living room", "deployed_version": "2026.9.1", "current_version": "2026.9.1"},
            {"configuration": "garage-door.yaml", "friendly_name": "Garage door", "target_platform": "esp8266", "board_id": "d1_mini",
             "area": "Garage", "deployed_version": "2026.6.3", "current_version": "2026.9.1"},
            {"configuration": "desk-plug.yaml", "friendly_name": "Desk plug", "target_platform": "bk72xx", "board_id": "generic-bk7231n",
             "area": "Office", "deployed_version": "2026.9.1", "current_version": "2026.9.1"},
            {"configuration": "bed-presence.yaml", "friendly_name": "Bed presence", "target_platform": "esp32", "board_id": "esp32-c3",
             "area": "Bedroom", "deployed_version": "2026.9.1", "current_version": "2026.9.1"},
        ]
        online = {"living-room.yaml": True, "garage-door.yaml": True, "desk-plug.yaml": not plug_down, "bed-presence.yaml": True}
        if widget_kind == "devices":
            return self._list(devices, online, bool(options.get("offline_only")), max(1, int(options.get("limit") or 12)))
        return self._summary(devices, online)


ADAPTER = ESPHomeAdapter()
