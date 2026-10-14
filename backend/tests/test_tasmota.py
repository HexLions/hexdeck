"""Tasmota: relays, power and sensors from one ``Status 0``.

``tasmota_status.json`` follows the keys Tasmota v15.6.0 writes in
``CmndStatus`` and ``MqttShowState``: a measuring plug with one relay
(``POWER``) and a four-relay board with two sensors (``POWER1`` to
``POWER4``, a DS18B20 and a BME280).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://192.168.1.60"
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "tasmota_status.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _tasmota(which: str) -> respx.Route:
    return respx.get(f"{BASE}/cm").mock(return_value=httpx.Response(200, json=ANSWERS[which]))


@respx.mock
async def test_a_plug_that_measures() -> None:
    route = _tasmota("plug")
    data = await get_adapter("tasmota").fetch("device", {"url": BASE}, {}, _ctx())
    assert route.calls[0].request.url.params["cmnd"] == "Status 0"
    assert data.primary == {"label": "Power", "value": 1843.0, "unit": "W", "metric": "power"}
    assert {"label": "Relay", "value": "on"} in data.secondary
    assert {"label": "Today", "value": 0.734, "unit": "kWh"} in data.secondary
    assert {"label": "Wi-Fi", "value": -65, "unit": "dBm"} in data.secondary
    assert [action.id for action in data.actions] == ["off"] and data.actions[0].params == {"relay": 1}


@respx.mock
async def test_a_relay_without_a_meter_shows_its_state() -> None:
    _tasmota("relays")
    data = await get_adapter("tasmota").fetch("device", {"url": BASE}, {"relay": 2}, _ctx())
    assert data.primary == {"label": "Relay 2", "value": "off"}
    assert [action.id for action in data.actions] == ["on"]
    read_only = await get_adapter("tasmota").fetch("device", {"url": BASE}, {"relay": 2, "switching": False}, _ctx())
    assert read_only.actions == []


@respx.mock
async def test_every_relay_and_every_reading() -> None:
    _tasmota("relays")
    data = await get_adapter("tasmota").fetch("sensors", {"url": BASE}, {}, _ctx())
    titles = [row["title"] for row in data.items]
    assert titles[:4] == ["Relay 1", "Relay 2", "Relay 3", "Relay 4"]
    values = {row["title"]: row["value"] for row in data.items}
    assert values["DS18B20 Temperature"] == "17.9 °C"
    assert values["BME280 Humidity"] == "61.4 %"
    assert values["BME280 Pressure"] == "1012.4 hPa"
    assert "DS18B20 Id" not in values, "an identifier is not a reading"


@respx.mock
async def test_switching_sends_the_power_command() -> None:
    route = _tasmota("relays")
    message = await get_adapter("tasmota").action("sensors", "off", {"relay": 4}, {"url": BASE}, {}, _ctx())
    assert route.calls[-1].request.url.params["cmnd"] == "Power4 Off"
    assert message == "Relay 4 switched off."


@respx.mock
async def test_the_password_goes_as_basic_for_admin() -> None:
    route = respx.get(f"{BASE}/cm").mock(return_value=httpx.Response(401, json={"WARNING": "Need user=<username>&password=<password>"}))
    with pytest.raises(AuthFailed):
        await get_adapter("tasmota").test({"url": BASE, "password": "made-up-password"}, _ctx())
    request = route.calls[0].request
    assert request.headers["authorization"].startswith("Basic ")
    assert "password" not in request.url.params, "the password stays out of the address"


@respx.mock
async def test_the_connection_test_names_the_device() -> None:
    _tasmota("plug")
    assert await get_adapter("tasmota").test({"url": BASE}, _ctx()) == "Washing machine answers with Tasmota 15.0.1 and 1 relay."
