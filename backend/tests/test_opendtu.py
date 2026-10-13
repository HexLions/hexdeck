"""OpenDTU: the power of the micro inverters now, today and in total.

``opendtu_live.json`` follows the shape OpenDTU v26.9.28 writes in
``WebApi_ws_live.cpp``: the list without a serial (each inverter's common
fields, the totals and the hints) and, per serial, the same with the
``AC``, ``DC`` and ``INV`` channels. Three inverters: one producing, one
reachable but not producing, one asleep.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://opendtu.local"
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "opendtu_live.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _opendtu(hints: dict | None = None) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        serial = request.url.params.get("inv")
        body = ANSWERS["detail"][serial] if serial else ANSWERS["live"]
        if hints is not None:
            body = {**body, "hints": {**body["hints"], **hints}}
        return httpx.Response(200, json=body)

    respx.get(f"{BASE}/api/livedata/status").mock(side_effect=answer)


@respx.mock
async def test_the_totals_and_who_produces() -> None:
    _opendtu()
    data = await get_adapter("opendtu").fetch("solar", {"url": BASE}, {}, _ctx())
    assert data.status == "ok"
    assert data.primary == {"label": "Power", "value": "512 W", "metric": "power"}
    assert {"label": "Today", "value": "2.87 kWh", "metric": "yield_day"} in data.secondary
    assert {"label": "Total", "value": "1834.6 kWh"} in data.secondary
    assert {"label": "Producing", "value": "1/3"} in data.secondary
    assert data.metrics == {"power": 512.3, "yield_day": 2873.0}


@respx.mock
async def test_each_inverter_from_its_own_channels() -> None:
    _opendtu()
    data = await get_adapter("opendtu").fetch("inverters", {"url": BASE}, {}, _ctx())
    east, garage, shed = data.items
    assert (east["title"], east["value"], east["status"]) == ("Balcony east", "512 W", "ok")
    assert east["subtitle"] == "today 2.87 kWh · 42 °C"
    assert garage["status"] == "warn" and garage["subtitle"].startswith("not producing")
    assert "limit 50 %" in garage["subtitle"]
    # Asleep at night is not an error: no channels come back for it, and it stays grey.
    assert (shed["status"], shed["subtitle"], shed["value"]) == ("unknown", "asleep", "")
    serials = [call.request.url.params.get("inv") for call in respx.calls if call.request.url.params.get("inv")]
    assert serials == ["116181234567", "1161ABCDEF01", "114172221111"], "the serial goes as written, in hexadecimal"


@respx.mock
async def test_a_radio_that_is_not_connected_turns_it_red() -> None:
    _opendtu({"radio_problem": True})
    data = await get_adapter("opendtu").fetch("solar", {"url": BASE}, {}, _ctx())
    assert data.status == "bad" and "radio" in data.meta["notice"]


@respx.mock
async def test_the_default_password_is_a_warning() -> None:
    _opendtu({"default_password": True})
    data = await get_adapter("opendtu").fetch("solar", {"url": BASE}, {}, _ctx())
    assert data.status == "warn" and "default password" in data.meta["notice"]


@respx.mock
async def test_the_password_as_admin_when_reading_is_locked() -> None:
    route = respx.get(f"{BASE}/api/livedata/status").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthFailed):
        await get_adapter("opendtu").test({"url": BASE, "password": "made-up-password"}, _ctx())
    assert route.calls[0].request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_the_connection_test_counts_the_inverters() -> None:
    _opendtu()
    assert await get_adapter("opendtu").test({"url": BASE}, _ctx()) == "OpenDTU answers with 3 inverters."
