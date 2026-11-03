"""Solar forecast: what your panels should make today, tomorrow and in the week.

Checked against Open-Meteo's forecast API on 03.11.2026, with panels near
Milan facing south, east and west, and a latitude out of range. Written after
the HACS integration "Open-Meteo Solar Forecast" and its library (rany2,
Apache 2.0 and MIT), which read the same API with the same physics; no code
taken, and neither its numpy nor its sun position calculations are needed
for a card that adds up hours.

⚠️ ``azimuth`` is Open-Meteo's: 0 is south, -90 east, 90 west, 180 north.
Compass bearings would point the panels elsewhere; asked at 60 degrees of
tilt, -90 peaked in the morning and 90 in the afternoon.

⚠️ ``global_tilted_irradiance`` is the mean of the hour that ends at its time,
in W/m² on the plane of the panels, so at one kWp the watts of a row are also
its watt-hours. The rows are local time at the place (``timezone=auto``)
without a zone, so "today" is worked out with ``utc_offset_seconds``, not the
clock of the machine HexDeck runs on.

The power: ``kWp × irradiance / 1000 × (1 + -0.004 × (cell - 25)) × efficiency``,
with the cell at ``air + irradiance × 0.0342`` (the Ross model, panels that are
not so well cooled). A forecast, not a measurement: no shade and no inverter
limit are known, which is what the efficiency stands in for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import demo as fake
from .base import Adapter, AdapterError, Context, Field, WidgetData, WidgetType

API = "https://api.open-meteo.com/v1/forecast"
ALPHA_TEMP = -0.004
TEMP_STC = 25.0
ROSS = 0.0342


def _number(value: Any, fallback: float | None = None) -> float | None:
    try:
        return float(value) if value not in (None, "") else fallback
    except (TypeError, ValueError):
        return fallback


def watts(kwp: float, irradiance: float, air: float, efficiency: float) -> float:
    """What the panels give at this irradiance, in watts."""
    if irradiance <= 0:
        return 0.0
    cell = air + irradiance * ROSS
    return kwp * irradiance * (1 + ALPHA_TEMP * (cell - TEMP_STC)) * efficiency


def energy(kwh: float) -> str:
    return f"{kwh:.1f} kWh"


OPTIONS = (
    Field("latitude", "Latitude", type="number", required=True, placeholder="45.46"),
    Field("longitude", "Longitude", type="number", required=True, placeholder="9.19"),
    Field("kwp", "Panels (kWp)", type="number", required=True, default=3, help="What the panels add up to on paper."),
    Field("tilt", "Tilt (degrees)", type="number", default=30, help="0 lies flat, 90 stands upright."),
    Field("azimuth", "Facing (degrees)", type="number", default=0, help="0 is south, -90 east, 90 west, 180 north."),
    Field("efficiency", "Efficiency (%)", type="number", default=85, help="What is left after the inverter, the cables and the dirt. 85 suits most systems."),
)


class SolarForecastAdapter(Adapter):
    kind = "solarforecast"
    label = "Solar forecast"
    category = "other"
    description = "What your solar panels should make today, tomorrow and in the week, worked out from the Open-Meteo forecast. No account needed."
    icon = "lucide:sun"
    beta = True
    docs_url = "https://open-meteo.com/en/docs"
    needs_integration = False
    widgets = (
        WidgetType(kind="today", label="Solar today", description="The energy expected today, what is left of it, tomorrow's, and the power now.",
                   renderer="value", default_size=(3, 2), refresh_seconds=1800, metrics=("power_w",), options=OPTIONS),
        WidgetType(kind="days", label="Solar this week", description="The energy expected on each of the next days, with the highest power.",
                   renderer="list", default_size=(3, 3), refresh_seconds=3600,
                   options=(*OPTIONS, Field("limit", "Days", type="number", default=7))),
    )

    def default_link(self, config: dict[str, Any]) -> str:
        return "https://open-meteo.com/"

    async def _hours(self, options: dict[str, Any], ctx: Context, cache: float = 1500) -> dict[str, Any]:
        latitude, longitude = _number(options.get("latitude")), _number(options.get("longitude"))
        if latitude is None or longitude is None:
            raise AdapterError("The card needs a latitude and a longitude.", code="missing_option", hint="Fill them in the card's settings.")
        response = await ctx.request("GET", API, params={
            "latitude": latitude, "longitude": longitude, "tilt": _number(options.get("tilt"), 30), "azimuth": _number(options.get("azimuth"), 0),
            "hourly": "global_tilted_irradiance,temperature_2m", "forecast_days": 7, "timezone": "auto",
        }, cache_seconds=cache, auth_errors=False)
        try:
            answer = response.json()
        except ValueError as error:
            raise AdapterError("Open-Meteo answered with something other than JSON.", code="bad_answer") from error
        if response.status_code == 400:
            raise AdapterError(f"Open-Meteo refused the question: {answer.get('reason', 'no reason given')}", code="bad_request",
                               hint="Latitude goes from -90 to 90, longitude from -180 to 180, the tilt from 0 to 90.")
        if response.status_code >= 400:
            raise AdapterError(f"Open-Meteo answered with HTTP {response.status_code}.", code="http_error")
        hourly = answer.get("hourly") if isinstance(answer, dict) else None
        if not isinstance(hourly, dict) or not hourly.get("time"):
            raise AdapterError("Open-Meteo answered without a forecast.", code="bad_answer")
        kwp = max(0.0, _number(options.get("kwp"), 0.0) or 0.0)
        efficiency = max(0.0, min(100.0, _number(options.get("efficiency"), 85.0) or 0.0)) / 100
        rows = []
        for at, irradiance, air in zip(hourly["time"], hourly.get("global_tilted_irradiance") or [], hourly.get("temperature_2m") or [], strict=False):
            rows.append((str(at), watts(kwp, _number(irradiance, 0.0) or 0.0, _number(air, TEMP_STC) or TEMP_STC, efficiency)))
        return {"rows": rows, "offset": int(answer.get("utc_offset_seconds") or 0)}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        await self._hours({"latitude": 45.46, "longitude": 9.19, "kwp": 1}, ctx, cache=0)
        return "Open-Meteo answers with a forecast."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        found = await self._hours(options, ctx)
        now = datetime.now(UTC) + timedelta(seconds=found["offset"])
        if widget_kind == "days":
            return self._days(found["rows"], now, max(1, min(7, int(_number(options.get("limit"), 7) or 7))))
        return self._today(found["rows"], now)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _today(rows: list[tuple[str, float]], now: datetime) -> WidgetData:
        today, tomorrow = now.strftime("%Y-%m-%d"), (now + timedelta(days=1)).strftime("%Y-%m-%d")
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        total = sum(power for at, power in rows if at.startswith(today)) / 1000
        left = sum(power for at, power in rows if at.startswith(today) and at > stamp) / 1000
        next_day = sum(power for at, power in rows if at.startswith(tomorrow)) / 1000
        # The row that ends next is the hour we are in.
        power = next((value for at, value in rows if at > stamp), 0.0)
        return WidgetData(status="ok", primary={"label": "Today", "value": energy(total)},
                          secondary=[{"label": "Still to come", "value": energy(left)}, {"label": "Tomorrow", "value": energy(next_day)},
                                     {"label": "Power now", "value": round(power), "unit": "W"}],
                          metrics={"power_w": float(round(power))})

    @staticmethod
    def _days(rows: list[tuple[str, float]], now: datetime, limit: int) -> WidgetData:
        days: dict[str, list[float]] = {}
        for at, power in rows:
            days.setdefault(at[:10], []).append(power)
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        items = []
        for day in sorted(day for day in days if day >= today)[:limit]:
            title = "Today" if day == today else "Tomorrow" if day == tomorrow else f"{day[8:10]}.{day[5:7]}."
            items.append({"title": title, "subtitle": f"up to {round(max(days[day]))} W", "status": "ok", "value": energy(sum(days[day]) / 1000)})
        return WidgetData(status="ok", items=items, meta={"empty": "Open-Meteo has no forecast for these days."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = datetime.now(UTC)
        rows = []
        for day in range(7):
            sky = fake.walk(f"solar-{day}", tick // 60, 0.35, 1.0)
            moment = now + timedelta(days=day)
            for hour in range(24):
                shape = max(0.0, 1 - abs(hour - 13) / 6)
                rows.append((f"{moment:%Y-%m-%d}T{hour:02d}:00", watts(4.2, 800 * shape * sky, 12.0, 0.85)))
        if widget_kind == "days":
            return self._days(rows, now, max(1, min(7, int(_number(options.get("limit"), 7) or 7))))
        return self._today(rows, now)


ADAPTER = SolarForecastAdapter()
