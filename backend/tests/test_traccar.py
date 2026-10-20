"""Traccar, against the answers of Traccar 6.16.0 run locally on 20.10.2026.

The fixture is what that server answered with three devices: two that sent
OsmAnd positions (first online, then two minutes later ``unknown``) and one
that never reported (``offline`` from the start), the latest positions, and
the bodies of a garbled token (400) and a wrong password (401).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

TR = "http://traccar.local:8082"
TOKEN = {"url": TR, "token": "test-token-for-traccar"}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "traccar_devices.json").read_text())


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _server(devices: list) -> None:
    respx.get(f"{TR}/api/devices").mock(return_value=httpx.Response(200, json=devices))
    respx.get(f"{TR}/api/positions").mock(return_value=httpx.Response(200, json=FIXTURE["positions"]))
    respx.get(f"{TR}/api/server").mock(return_value=httpx.Response(200, json={"id": 1, "version": FIXTURE["server_version"]}))


@respx.mock
async def test_the_summary_counts_unknown_and_offline_as_quiet(ctx: Context) -> None:
    _server(FIXTURE["devices_online"])
    data = await get_adapter("traccar").fetch("summary", TOKEN, {}, ctx)
    assert data.primary == {"label": "Online", "value": "2 / 3"} and data.secondary == [{"label": "Not heard from", "value": 1}]
    assert respx.calls[0].request.headers["authorization"] == "Bearer " + TOKEN["token"]
    _server(FIXTURE["devices_later"])
    data = await get_adapter("traccar").fetch("summary", TOKEN, {}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
    assert data.status == "warn" and data.primary["value"] == "0 / 3", "online turns unknown after the status timeout"


@respx.mock
async def test_the_devices(ctx: Context) -> None:
    _server(FIXTURE["devices_online"])
    rows = (await get_adapter("traccar").fetch("devices", TOKEN, {}, ctx)).items
    assert [(row["title"], row["status"]) for row in rows] == [("Dog collar", "warn"), ("Bike", "ok"), ("Car", "ok")]
    assert rows[0]["subtitle"] == "never heard from"
    car = rows[2]["subtitle"]
    assert "50 km/h" in car and "battery 88%" in car, "27 knots, the battery the OsmAnd position carried"


@respx.mock
async def test_signing_in_with_a_password(ctx: Context) -> None:
    _server(FIXTURE["devices_online"])
    await get_adapter("traccar").fetch("summary", {"url": TR, "username": "admin@example.com", "password": "pw"}, {}, ctx)
    assert respx.calls[0].request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_refusals(ctx: Context) -> None:
    respx.get(f"{TR}/api/devices").mock(side_effect=[
        httpx.Response(400, text=FIXTURE["bad_token_body"], headers={"Content-Type": "application/json"}),
        httpx.Response(401, text=FIXTURE["unauthorized_body"]),
        httpx.Response(500),
        httpx.Response(200, text="<html>"),
    ])
    adapter = get_adapter("traccar")
    for kind in (AuthFailed, AuthFailed):
        with pytest.raises(kind) as failure:
            await adapter.fetch("summary", TOKEN, {}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
        assert failure.value.hint
    for code in ("http_error", "not_traccar"):
        with pytest.raises(AdapterError) as failure:
            await adapter.fetch("summary", TOKEN, {}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
        assert failure.value.code == code
    with pytest.raises(AuthFailed):
        await adapter.fetch("summary", {"url": TR}, {}, ctx)


@respx.mock
async def test_the_connection_test(ctx: Context) -> None:
    _server(FIXTURE["devices_online"])
    assert await get_adapter("traccar").test(TOKEN, ctx) == "Traccar 6.16.0 answers with 3 trackers."


def test_the_demo() -> None:
    adapter = get_adapter("traccar")
    for tick in range(8):
        assert adapter.demo("summary", {}, tick).primary["value"].endswith("/ 3")
        assert len(adapter.demo("devices", {}, tick).items) == 3


@respx.mock
async def test_a_disabled_device_and_a_refusing_proxy(ctx: Context) -> None:
    devices = [*FIXTURE["devices_online"], {**FIXTURE["devices_online"][0], "id": 9, "name": "Old phone", "disabled": True}]
    _server(devices)
    data = await get_adapter("traccar").fetch("summary", TOKEN, {}, ctx)
    assert data.primary["value"] == "2 / 3", "a disabled tracker is left out"
    respx.get(f"{TR}/api/devices").mock(return_value=httpx.Response(403))
    with pytest.raises(AuthFailed):
        await get_adapter("traccar").fetch("summary", TOKEN, {}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
