"""Solar forecast, against what Open-Meteo answered on 03.11.2026.

Panels near Milan at 30 degrees facing south, seven days of hours. The refusal
is what it said to a latitude of 145.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.adapters.solarforecast import API, SolarForecastAdapter, watts

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "solarforecast_openmeteo.json").read_text())
PANELS = {"latitude": 45.46, "longitude": 9.19, "kwp": 4.2, "tilt": 30, "azimuth": -45, "efficiency": 85}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={})


def test_the_power_follows_the_irradiance_and_the_heat_of_the_cell() -> None:
    assert watts(1.0, 0, 20.0, 1.0) == 0.0
    # At 1000 W/m² and 25 degrees of air the cell is at 59.2: 13.68 per cent less than on paper.
    assert round(watts(1.0, 1000, 25.0, 1.0), 1) == 863.2
    assert round(watts(4.0, 1000, 25.0, 0.5), 1) == 1726.4


def _rows() -> list[tuple[str, float]]:
    hourly = FIXTURE["milan_south"]["hourly"]
    return [(at, watts(4.2, value, air, 0.85)) for at, value, air in zip(hourly["time"], hourly["global_tilted_irradiance"], hourly["temperature_2m"], strict=True)]


def test_today_adds_up_the_day_and_says_what_is_left() -> None:
    rows = _rows()
    day = rows[0][0][:10]
    noon = datetime.fromisoformat(f"{day}T12:30")
    data = SolarForecastAdapter._today(rows, noon)
    total = sum(power for at, power in rows if at.startswith(day)) / 1000
    left = sum(power for at, power in rows if at.startswith(day) and at > f"{day}T12:30") / 1000
    assert data.primary == {"label": "Today", "value": f"{total:.1f} kWh"}
    assert data.secondary[0] == {"label": "Still to come", "value": f"{left:.1f} kWh"}
    # The row of 13:00 is the mean of the hour we are in.
    expected = round(next(power for at, power in rows if at == f"{day}T13:00"))
    assert data.secondary[2] == {"label": "Power now", "value": expected, "unit": "W"}
    assert data.metrics == {"power_w": float(expected)}


def test_the_days_are_named_without_a_month_in_words() -> None:
    rows = _rows()
    first = datetime.fromisoformat(rows[0][0])
    items = SolarForecastAdapter._days(rows, first, 3).items
    assert [item["title"] for item in items][:2] == ["Today", "Tomorrow"]
    third = rows[48][0]
    assert items[2]["title"] == f"{third[8:10]}.{third[5:7]}."
    assert items[0]["subtitle"].startswith("up to ") and items[0]["value"].endswith(" kWh")


@respx.mock
async def test_the_panels_are_asked_for_on_their_own_plane() -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json=FIXTURE["milan_south"]))
    data = await get_adapter("solarforecast").fetch("today", {}, PANELS, _ctx())
    asked = route.calls[0].request.url.params
    assert (asked["tilt"], asked["azimuth"], asked["timezone"], asked["forecast_days"]) == ("30.0", "-45.0", "auto", "7")
    assert asked["hourly"] == "global_tilted_irradiance,temperature_2m"
    assert data.status == "ok" and data.primary["label"] == "Today"


@respx.mock
async def test_a_place_that_does_not_exist_is_said_in_its_words() -> None:
    respx.get(API).mock(return_value=httpx.Response(400, json={"error": True, "reason": "Latitude must be in range of -90 to 90°. Given: 145.0."}))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("solarforecast").fetch("today", {}, {**PANELS, "latitude": 145}, _ctx())
    assert refused.value.code == "bad_request" and "145.0" in str(refused.value)


async def test_no_place_is_said_before_anything_is_asked() -> None:
    with pytest.raises(AdapterError) as refused:
        await get_adapter("solarforecast").fetch("today", {}, {"kwp": 1}, _ctx())
    assert refused.value.code == "missing_option"
