"""Fronius Solar API v1: the site, the flow and the inverters.

``fronius_solar_api.json`` is written in the shape a Symo and a Gen24 with a
battery answer (as pyfronius and Home Assistant record it): a Gen24 feeding
the grid and charging its battery with null energies for the day, a Symo
drawing from the grid, the same Symo at night, and an inverter list with a
name in HTML entities and one inverter in error.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

BASE = "http://192.168.1.80"
CONFIG = {"url": BASE}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "fronius_solar_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _fronius(flow: str) -> None:
    respx.get(f"{BASE}/solar_api/v1/GetPowerFlowRealtimeData.fcgi").mock(return_value=httpx.Response(200, json=ANSWERS[flow]))
    respx.get(f"{BASE}/solar_api/v1/GetInverterInfo.cgi").mock(return_value=httpx.Response(200, json=ANSWERS["symo_info"]))


@respx.mock
async def test_a_gen24_feeding_the_grid_and_charging() -> None:
    _fronius("gen24_flow")
    data = await get_adapter("fronius").fetch("power", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Solar", "value": 3.91, "unit": "kW", "metric": "pv"}
    assert {"label": "House", "value": 2.2, "unit": "kW", "metric": "home"} in data.secondary
    assert {"label": "To grid", "value": 0.51, "unit": "kW", "metric": "grid"} in data.secondary
    assert {"label": "Battery", "value": 63.2, "unit": "%"} in data.secondary
    # A Gen24 has no energy of the day; the total stands in, never a zero.
    assert {"label": "Total", "value": "9.83 MWh"} in data.secondary


@respx.mock
async def test_the_flow_in_the_cards_signs() -> None:
    _fronius("gen24_flow")
    data = await get_adapter("fronius").fetch("flow", CONFIG, {}, _ctx())
    assert data.meta["flow"] == {"solar": 3.91, "home": 2.2, "grid": -0.51, "battery": -1.2, "battery_soc": 63.2, "car": None}


@respx.mock
async def test_a_symo_drawing_from_the_grid_and_at_night() -> None:
    _fronius("symo_flow")
    data = await get_adapter("fronius").fetch("power", CONFIG, {}, _ctx())
    assert {"label": "From grid", "value": 0.98, "unit": "kW", "metric": "grid"} in data.secondary
    assert {"label": "Today", "value": "8.4 kWh"} in data.secondary
    assert not any(row["label"] == "Battery" for row in data.secondary), "no battery, no battery row"
    respx.reset()
    _fronius("night_flow")
    night = await get_adapter("fronius").fetch("flow", CONFIG, {}, _ctx())
    assert night.meta["flow"]["solar"] == 0.0 and night.meta["flow"]["battery"] is None


@respx.mock
async def test_each_inverter_with_its_name_and_error() -> None:
    _fronius("symo_flow")
    data = await get_adapter("fronius").fetch("inverters", CONFIG, {}, _ctx())
    first, second = data.items
    assert (first["title"], first["subtitle"], first["value"], first["status"]) == ("Symo 8.2", "running", "1450 W", "ok")
    assert (second["title"], second["subtitle"], second["status"]) == ("Garage", "error · code 567", "bad")
    assert data.status == "bad"


@respx.mock
async def test_an_error_in_the_head_is_said() -> None:
    respx.get(f"{BASE}/solar_api/v1/GetInverterInfo.cgi").mock(return_value=httpx.Response(200, json={
        "Head": {"Status": {"Code": 255, "Reason": "Not implemented", "UserMessage": ""}}, "Body": {}}))
    with pytest.raises(AdapterError, match="Not implemented"):
        await get_adapter("fronius").test(CONFIG, _ctx())
