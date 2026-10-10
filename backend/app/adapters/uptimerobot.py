"""UptimeRobot: your monitors, which are down, and the incidents still open.

API v3 at ``https://api.uptimerobot.com/v3``, read-only, with an API key as
``Authorization: Bearer``: ``/monitors`` for the monitors with their last day
of uptime, and ``/incidents?status=open`` for what is happening now.

⚠️ A monitor's answer carries its HTTP password, custom headers and API key
next to its name. Nothing past the name, address, type, status, interval and
the last day's uptime is read.

⚠️ The schema leaves ``status`` open; the filter's description names the
values: ``UP``, ``DOWN``, ``LOOKS_DOWN`` (failing, not yet confirmed),
``PAUSED`` and ``STARTED`` (created, not checked yet).

⚠️ The free plan allows ten requests a minute for the whole account. The cards
ask every few minutes and keep their answers, so a board with all three on it
stays well inside that.

⚠️ The read-only key from the Integrations page is enough and the one to use:
an account key could also change and delete monitors.

Read from UptimeRobot's OpenAPI spec for API 3.0
(cdn.uptimerobot.com/api/openapi.yaml) on 2026-10-09.
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
    measured,
)

API = "https://api.uptimerobot.com/v3"
#: A monitor's status as a colour and a word.
STATE = {"UP": ("ok", "up"), "DOWN": ("bad", "down"), "LOOKS_DOWN": ("warn", "looks down"),
         "PAUSED": ("unknown", "paused"), "STARTED": ("unknown", "not checked yet")}
MONITORS_SECONDS = 180
INCIDENTS_SECONDS = 180


def _uptime(monitor: dict[str, Any]) -> float | None:
    """The last day's uptime, the mean of its equal buckets, as a percentage."""
    buckets = [one.get("uptime") for one in ((monitor.get("lastDayUptimes") or {}).get("histogram") or []) if isinstance(one, dict)]
    known = [float(value) for value in buckets if isinstance(value, (int, float))]
    return round(sum(known) / len(known), 2) if known else None


class UptimeRobotAdapter(Adapter):
    kind = "uptimerobot"
    label = "UptimeRobot"
    category = "monitoring"
    description = "Your UptimeRobot monitors with the last day's uptime, the ones that are down first, and the incidents still open."
    icon = "uptimerobot"
    docs_url = "https://uptimerobot.com/api/v3/"
    fields = (
        Field("token", "API key", type="password", secret=True, required=True,
              help="The read-only API key from Integrations > API in the dashboard. An account key works too, but it could also change monitors."),
    )
    widgets = (
        WidgetType(kind="summary", label="Overview", description="How many monitors are up, down and paused.",
                   renderer="value", default_size=(3, 2), refresh_seconds=300, metrics=("down",)),
        WidgetType(kind="monitors", label="Monitors", description="Every monitor with its status and the last day's uptime, the ones that are down first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("down_only", "Only what is not up", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=10))),
        WidgetType(kind="incidents", label="Open incidents", description="The incidents still going on, with the monitor, the reason and since when.",
                   renderer="list", default_size=(3, 2), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=6),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any], cache: float) -> Any:
        token = str(config.get("token") or "").strip()
        response = await ctx.request("GET", f"{API}{path}", params=params, cache_seconds=cache, auth_errors=False,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        if response.status_code in (401, 403):
            raise AuthFailed("UptimeRobot refused the API key.",
                             hint="The read-only API key from Integrations > API in the UptimeRobot dashboard.")
        if response.status_code == 429:
            raise AdapterError("UptimeRobot's limit of requests a minute is used up for now.", code="rate_limited",
                               hint="The free plan allows ten a minute for the whole account; other tools using the same account count too.")
        if response.status_code >= 400:
            raise AdapterError(f"UptimeRobot answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("UptimeRobot did not answer with JSON.", code="not_json") from failure

    async def _monitors(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/monitors", {"limit": 200}, MONITORS_SECONDS)
        return [one for one in ((answer or {}).get("data") or []) if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        monitors = await self._monitors(config, ctx)
        return f"UptimeRobot answers with {len(monitors)} monitor{'s' if len(monitors) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "incidents":
            answer = await self._get(config, ctx, "/incidents", {"status": "open"}, INCIDENTS_SECONDS)
            return self._incidents([one for one in ((answer or {}).get("data") or []) if isinstance(one, dict)],
                                   max(1, int(options.get("limit") or 6)))
        monitors = await self._monitors(config, ctx)
        if widget_kind == "monitors":
            return self._list(monitors, bool(options.get("down_only")), max(1, int(options.get("limit") or 10)))
        return self._summary(monitors)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(monitors: list[dict[str, Any]]) -> WidgetData:
        statuses = [str(one.get("status") or "") for one in monitors]
        up = statuses.count("UP")
        down = statuses.count("DOWN")
        doubtful = statuses.count("LOOKS_DOWN")
        paused = statuses.count("PAUSED")
        secondary: list[dict[str, Any]] = []
        if down:
            secondary.append({"label": "Down", "value": down, "metric": "down"})
        if doubtful:
            secondary.append({"label": "Looks down", "value": doubtful})
        if paused:
            secondary.append({"label": "Paused", "value": paused})
        secondary.append({"label": "Monitors", "value": len(monitors)})
        return WidgetData(
            status="bad" if down else "warn" if doubtful else "ok",
            primary={"label": "Up", "value": f"{up} / {len(monitors) - paused}"},
            secondary=secondary,
            metrics=measured({"down": float(down)}),
        )

    @staticmethod
    def _list(monitors: list[dict[str, Any]], down_only: bool, limit: int) -> WidgetData:
        order = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}
        rows = []
        for monitor in monitors:
            colour, word = STATE.get(str(monitor.get("status") or ""), ("unknown", str(monitor.get("status") or "?").lower()))
            if down_only and colour == "ok":
                continue
            uptime = _uptime(monitor)
            parts = [str(monitor.get("type") or "").lower(), str(monitor.get("url") or "")]
            rows.append({
                "id": monitor.get("id"),
                "title": str(monitor.get("friendlyName") or monitor.get("url") or "?"),
                "subtitle": " · ".join(part for part in parts if part)[:90],
                "value": f"{uptime:.2f}%" if colour == "ok" and uptime is not None else word,
                "status": colour,
            })
        rows.sort(key=lambda row: (order.get(row["status"], 4), str(row["title"]).lower()))
        return WidgetData(
            status="bad" if any(row["status"] == "bad" for row in rows) else "warn" if any(row["status"] == "warn" for row in rows) else "ok",
            items=rows[:limit],
            meta={"empty": "Everything is up" if down_only else "No monitor yet"},
        )

    @staticmethod
    def _incidents(incidents: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        for incident in incidents:
            monitor = incident.get("monitor") or {}
            rows.append({
                "id": incident.get("id"),
                "title": str(monitor.get("friendlyName") or f"Monitor {monitor.get('id')}"),
                "subtitle": " · ".join(part for part in (str(incident.get("reason") or ""), ago(incident.get("startedAt"))) if part),
                "value": "",
                "status": "bad",
            })
        return WidgetData(status="bad" if rows else "ok", items=rows[:limit], primary={"label": "Open", "value": len(rows)},
                          meta={"empty": "No incident is open"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        down = fake.flicker("ur-blog", tick, 0.3)
        def histogram(value: float) -> dict[str, Any]:
            return {"bucketSize": 3600, "histogram": [{"timestamp": hour, "uptime": value} for hour in range(24)]}
        monitors = [
            {"id": 1, "friendlyName": "Home Assistant", "type": "HTTP", "url": "https://ha.example.com", "status": "UP", "lastDayUptimes": histogram(100)},
            {"id": 2, "friendlyName": "Nextcloud", "type": "KEYWORD", "url": "https://cloud.example.com", "status": "UP", "lastDayUptimes": histogram(99.86)},
            {"id": 3, "friendlyName": "Blog", "type": "HTTP", "url": "https://blog.example.com", "status": "DOWN" if down else "UP", "lastDayUptimes": histogram(97.2)},
            {"id": 4, "friendlyName": "Mail", "type": "PORT", "url": "mail.example.com", "status": "UP", "lastDayUptimes": histogram(100)},
            {"id": 5, "friendlyName": "Backups", "type": "HEARTBEAT", "url": "", "status": "PAUSED"},
        ]
        if widget_kind == "incidents":
            return self._incidents([{"id": "9", "monitor": {"id": 3, "friendlyName": "Blog"}, "reason": "Connection Timeout", "startedAt": ""}] if down else [],
                                   max(1, int(options.get("limit") or 6)))
        if widget_kind == "monitors":
            return self._list(monitors, bool(options.get("down_only")), max(1, int(options.get("limit") or 10)))
        return self._summary(monitors)


ADAPTER = UptimeRobotAdapter()
