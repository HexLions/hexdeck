"""RackTemp: the sensors from /metrics, one sensor, and what is not RackTemp.

``racktemp_metrics.json`` holds what RackTemp 1.0.7 answered locally on 2026-11-16 with three sensors: one over the
upper limit set for it, one without limits, and one that never reported.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.adapters.racktemp import parse_metrics, sensors_of

BASE = "https://racktemp.local:7431"
CONFIG = {"url": BASE}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "racktemp_metrics.json").read_text())
TOP, CLOSET = "cmwjdjs3200029y2eyo2rm1ln", "cmwjdjs3t00049y2ez1cdotwt"


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _racktemp() -> None:
    respx.get(f"{BASE}/metrics").mock(return_value=httpx.Response(200, text=ANSWERS["metrics"]))
    respx.get(f"{BASE}/api/version").mock(return_value=httpx.Response(200, json=ANSWERS["version"]))


@respx.mock
async def test_the_connection_test_asks_for_no_key() -> None:
    _racktemp()
    assert await get_adapter("racktemp").test(CONFIG, _ctx()) == "RackTemp 1.0.7 answers with 3 sensors."
    for call in respx.calls:
        assert "key" not in str(call.request.url) and "authorization" not in call.request.headers


@respx.mock
async def test_every_sensor_against_its_limits() -> None:
    _racktemp()
    data = await get_adapter("racktemp").fetch("sensors", CONFIG, {}, _ctx())
    assert [(item["title"], item["subtitle"], item["value"], item["status"]) for item in data.items] == [
        ("Rack top", "Server cabinet · Above 30 °C", "31.4 °C · 38 %", "bad"),
        ("Closet", "Hall", "22.6 °C · 55 %", "ok"),
        ("Attic", "Roof · No reading yet", "", "bad"),
    ]
    assert data.status == "bad"
    assert {row["label"]: row["value"] for row in data.secondary} == {"Sensors": 3, "Problems": 2}


@respx.mock
async def test_one_sensor_and_the_choices() -> None:
    _racktemp()
    adapter = get_adapter("racktemp")
    assert await adapter.choices("sensor", CONFIG, _ctx()) == [
        (TOP, "Rack top (Server cabinet)"), (CLOSET, "Closet (Hall)"), ("cmwjdk3l6000a9y2eufidcwml", "Attic (Roof)")]
    data = await adapter.fetch("sensor", CONFIG, {"sensor": TOP}, _ctx())
    assert data.primary == {"label": "Rack top", "value": 31.4, "unit": "°C"}
    assert {row["label"]: row["value"] for row in data.secondary} == {"Humidity": 38, "Limits": "15 to 30", "Last reading": "6s", "Location": "Server cabinet"}
    assert data.status == "bad" and data.meta == {"status_reason": "Above 30 °C"}
    assert data.metrics == {"temperature": 31.4, "humidity": 38.2}


@respx.mock
async def test_a_sensor_that_was_removed() -> None:
    _racktemp()
    with pytest.raises(AdapterError) as missing:
        await get_adapter("racktemp").fetch("sensor", CONFIG, {"sensor": "gone"}, _ctx())
    assert missing.value.code == "no_such_sensor"


@respx.mock
async def test_something_else_at_the_address() -> None:
    respx.get(f"{BASE}/metrics").mock(return_value=httpx.Response(200, text="node_load1 0.5\n"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("racktemp").fetch("sensors", CONFIG, {}, _ctx())
    assert refused.value.code == "not_racktemp"


def test_a_name_with_a_quote_and_a_comma() -> None:
    text = 'rack_temp_celsius{sensor_id="a",sensor="Rack \\"B\\", left",location=""} 25\n'
    assert parse_metrics(text) == [("rack_temp_celsius", {"sensor_id": "a", "sensor": 'Rack "B", left', "location": ""}, 25.0)]
    assert sensors_of(text)[0]["name"] == 'Rack "B", left'


def test_an_offline_sensor_with_an_old_reading() -> None:
    text = 'rack_temp_celsius{sensor_id="a",sensor="A",location=""} 25\nrack_temp_online{sensor_id="a",sensor="A",location=""} 0\n'
    data = get_adapter("racktemp")._list(sensors_of(text))
    assert data.items[0]["subtitle"] == "Offline" and data.items[0]["status"] == "bad"


@pytest.mark.parametrize("kind", ["sensors", "sensor"])
def test_every_card_has_a_demo(kind: str) -> None:
    data = get_adapter("racktemp").demo(kind, {"sensor": "closet"}, 3)
    assert data.primary or data.items
