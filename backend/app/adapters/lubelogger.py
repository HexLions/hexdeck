"""LubeLogger: the cars in the garage and the maintenance coming due.

LubeLogger's API, read-only, with the user name and password as basic auth
when its login is on, and nothing when it is off: ``/api/vehicle/info``
(without a vehicle) for each vehicle with its reminder counts and last
odometer, ``/api/vehicle/reminders/all`` for every reminder, ``/api/version``
for the version.

⚠️ A reminder's urgency is LubeLogger's own, from thresholds set in its
settings: NotUrgent, Urgent, VeryUrgent, PastDue. A reminder 500 km away can
be NotUrgent; the card does not second-guess it.

⚠️ ``dueDate`` is written in the server's culture ("10/20/2026" on an en-US
one) and is "01/01/0001" for a reminder by distance only, so it is never
parsed: ``dueDays`` and ``dueDistance`` are, and ``metric`` says which of the
two decides the reminder.

⚠️ Distances are in whatever unit the garage uses, which the API does not
say, so they are shown without one.

⚠️ A refused login is a 401 with LubeLogger's login page as the body.

Checked against LubeLogger 1.7.3 running locally on 2026-10-10, verified by
its digest, with two vehicles and four reminders, with its login off and on.
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

#: LubeLogger's urgency as a colour, a word and an order, the most urgent first.
URGENCY = {"PastDue": ("bad", "past due", 0), "VeryUrgent": ("bad", "very urgent", 1),
           "Urgent": ("warn", "urgent", 2), "NotUrgent": ("ok", "", 3)}
INFO_SECONDS = 600


def _number(value: Any) -> int | None:
    """LubeLogger writes its numbers as strings: "9", "-10", "2150"."""
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _vehicle_name(vehicle: dict[str, Any]) -> str:
    return " ".join(str(part) for part in (vehicle.get("year"), vehicle.get("make"), vehicle.get("model")) if part) or "?"


def _due(reminder: dict[str, Any]) -> str:
    """When a reminder is due, by the measure that decides it."""
    if reminder.get("metric") == "Odometer":
        distance = _number(reminder.get("dueDistance"))
        if distance is None:
            return ""
        return f"{-distance:,} over" if distance < 0 else f"{distance:,} to go"
    days = _number(reminder.get("dueDays"))
    if days is None:
        return ""
    return f"{-days} d late" if days < 0 else "today" if days == 0 else f"in {days} d"


class LubeLoggerAdapter(Adapter):
    kind = "lubelogger"
    label = "LubeLogger"
    category = "other"
    description = "The vehicles in LubeLogger and the maintenance coming due, the past-due reminders first."
    icon = "lubelogger"
    docs_url = "https://docs.lubelogger.com/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://lubelogger:8080"),
        Field("username", "Username", help="A LubeLogger user. Empty when its login is turned off."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many reminders are past due and urgent across the garage, and how many vehicles there are.",
                   renderer="value", default_size=(3, 2), refresh_seconds=1800, metrics=("past_due",)),
        WidgetType(kind="reminders", label="Reminders", description="The maintenance reminders of every vehicle, the most urgent first, with when each is due.",
                   renderer="list", default_size=(3, 3), refresh_seconds=1800,
                   options=(Field("urgent_only", "Only the urgent ones", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=8))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        username = str(config.get("username") or "").strip()
        response = await ctx.request("GET", f"{base_url(config)}/api{path}", verify=not config.get("insecure"),
                                     headers={"Accept": "application/json"}, cache_seconds=cache, auth_errors=False,
                                     auth=(username, str(config.get("password") or "")) if username else None)
        if response.status_code in (401, 403):
            raise AuthFailed("LubeLogger refused the user name and password." if username else "LubeLogger asks for a user name and password.",
                             hint="A LubeLogger user, as on its login page.")
        if response.status_code >= 400:
            raise AdapterError(f"LubeLogger answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("LubeLogger did not answer with JSON.", code="not_json",
                               hint="The address of LubeLogger itself.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        vehicles = await self._get(config, ctx, "/vehicles", 0) or []
        version = await self._get(config, ctx, "/version", 0) or {}
        return f"LubeLogger {version.get('currentVersion') or '?'} answers, with {len(vehicles)} vehicle{'s' if len(vehicles) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        info = [one for one in (await self._get(config, ctx, "/vehicle/info", INFO_SECONDS) or []) if isinstance(one, dict)]
        if widget_kind == "reminders":
            reminders = [one for one in (await self._get(config, ctx, "/vehicle/reminders/all", INFO_SECONDS) or []) if isinstance(one, dict)]
            names = {str((one.get("vehicleData") or {}).get("id")): _vehicle_name(one.get("vehicleData") or {}) for one in info}
            return self._reminders(reminders, names, bool(options.get("urgent_only")), max(1, int(options.get("limit") or 8)))
        return self._overview(info)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(info: list[dict[str, Any]]) -> WidgetData:
        past_due = sum(int(one.get("pastDueReminderCount") or 0) for one in info)
        urgent = sum(int(one.get("urgentReminderCount") or 0) + int(one.get("veryUrgentReminderCount") or 0) for one in info)
        return WidgetData(
            status="bad" if past_due else "warn" if urgent else "ok",
            primary={"label": "Past due", "value": past_due, "metric": "past_due"},
            secondary=[{"label": "Urgent", "value": urgent}, {"label": "Vehicles", "value": len(info)}],
            metrics=measured({"past_due": float(past_due)}),
        )

    @staticmethod
    def _reminders(reminders: list[dict[str, Any]], names: dict[str, str], urgent_only: bool, limit: int) -> WidgetData:
        rows = []
        for reminder in reminders:
            colour, word, order = URGENCY.get(str(reminder.get("urgency") or ""), ("unknown", "", 4))
            if urgent_only and colour == "ok":
                continue
            due = _due(reminder)
            rows.append(({
                "id": reminder.get("id"),
                "title": str(reminder.get("description") or "?"),
                "subtitle": " · ".join(part for part in (names.get(str(reminder.get("vehicleId")), ""), word) if part),
                "value": due,
                "status": colour,
            }, (order, _number(reminder.get("dueDays")) or 0)))
        rows.sort(key=lambda pair: pair[1])
        items = [row for row, _ in rows]
        return WidgetData(
            status="bad" if any(row["status"] == "bad" for row in items) else "warn" if any(row["status"] == "warn" for row in items) else "ok",
            items=items[:limit],
            meta={"empty": "Nothing is due" if urgent_only else "No reminder yet"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        late = fake.flicker("lube-tyres", tick, 0.5)
        info = [
            {"vehicleData": {"id": 1, "year": 2019, "make": "Mazda", "model": "CX-5"}, "pastDueReminderCount": 1 if late else 0, "urgentReminderCount": 1},
            {"vehicleData": {"id": 2, "year": 2012, "make": "Fiat", "model": "Panda"}, "pastDueReminderCount": 0, "urgentReminderCount": 0},
        ]
        if widget_kind == "reminders":
            reminders = [
                {"vehicleId": "1", "id": "1", "description": "Oil change", "urgency": "Urgent", "metric": "Date", "dueDays": "9"},
                {"vehicleId": "1", "id": "2", "description": "Tyre rotation", "urgency": "PastDue" if late else "Urgent", "metric": "Date",
                 "dueDays": "-3" if late else "4"},
                {"vehicleId": "2", "id": "3", "description": "Inspection", "urgency": "NotUrgent", "metric": "Date", "dueDays": "141"},
                {"vehicleId": "2", "id": "4", "description": "Timing belt", "urgency": "NotUrgent", "metric": "Odometer", "dueDistance": "2400"},
            ]
            names = {"1": "2019 Mazda CX-5", "2": "2012 Fiat Panda"}
            return self._reminders(reminders, names, bool(options.get("urgent_only")), max(1, int(options.get("limit") or 8)))
        return self._overview(info)


ADAPTER = LubeLoggerAdapter()
