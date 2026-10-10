"""ESPHome: the dashboard's devices, online or not, and firmware updates.

``esphome_devices.json`` holds ``/devices`` (trimmed to the fields read)
and ``/ping`` as ESPHome Device Builder 1.23.0 answered them locally on
2026-10-10, with two configured devices and no right to ping, so both are
``null``. Online and offline are the documented ``true`` and ``false``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://esphome.local:6052"
CONFIG = {"url": BASE, "username": "admin", "password": "made-up-password"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "esphome_devices.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _esphome(ping: dict | None = None, devices: list | None = None) -> None:
    listed = {**ANSWERS["devices"], "configured": devices if devices is not None else ANSWERS["devices"]["configured"]}
    respx.get(f"{BASE}/devices").mock(return_value=httpx.Response(200, json=listed))
    respx.get(f"{BASE}/ping").mock(return_value=httpx.Response(200, json=ANSWERS["ping"] if ping is None else ping))


@respx.mock
async def test_basic_auth_and_the_count() -> None:
    _esphome()
    assert await get_adapter("esphome").test(CONFIG, _ctx()) == "ESPHome answers, with 2 configured devices."
    assert respx.calls[0].request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_not_known_yet_is_neither_up_nor_down() -> None:
    _esphome()
    data = await get_adapter("esphome").fetch("summary", CONFIG, {}, _ctx())
    assert data.status == "ok" and data.primary == {"label": "Online", "value": "0 / 2"}
    assert {"label": "Unknown", "value": 2} in data.secondary


@respx.mock
async def test_one_offline_and_one_behind() -> None:
    garage, living = ANSWERS["devices"]["configured"]
    devices = [garage, {**living, "deployed_version": "2026.6.3", "current_version": "2026.9.1"}]
    _esphome(ping={"garage-door.yaml": False, "living-room-sensor.yaml": True}, devices=devices)
    data = await get_adapter("esphome").fetch("summary", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.primary["value"] == "1 / 2"
    assert {"label": "Updates", "value": 1} in data.secondary and data.metrics["offline"] == 1.0
    listed = await get_adapter("esphome").fetch("devices", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in listed.items] == [
        ("Garage door", "offline"), ("Living room sensor", "update 2026.6.3 → 2026.9.1")]
    only = await get_adapter("esphome").fetch("devices", CONFIG, {"offline_only": True}, _ctx())
    assert [row["title"] for row in only.items] == ["Garage door"]


@respx.mock
async def test_a_device_never_flashed_here_needs_no_update() -> None:
    _esphome()
    data = await get_adapter("esphome").fetch("summary", CONFIG, {}, _ctx())
    assert {"label": "Updates", "value": 0} in data.secondary


@respx.mock
async def test_refused_and_the_login_page() -> None:
    respx.get(f"{BASE}/devices").mock(return_value=httpx.Response(401, text="Authentication required"))
    with pytest.raises(AuthFailed):
        await get_adapter("esphome").test(CONFIG, _ctx())
    respx.get(f"{BASE}/devices").mock(return_value=httpx.Response(200, text="<!DOCTYPE html><title>Login</title>"))
    with pytest.raises(AdapterError, match="JSON"):
        await get_adapter("esphome").test({"url": BASE}, _ctx())
