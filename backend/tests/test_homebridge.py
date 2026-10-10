"""Homebridge: whether it runs, its child bridges, and what has an update.

``homebridge_status.json`` holds three answers of Homebridge UI 5.29.0 with
Homebridge 2.4.0, running locally under hb-service on 2026-10-10: the child
bridges (one, stopped by hand from the UI), the plugins and Homebridge's own
version, with the install path made generic. The login's answers and the 412
of a user with two-factor login follow the UI's source at 5.29.0.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://homebridge.local:8581"
CONFIG = {"url": BASE, "username": "dashboard", "password": "made-up-password"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "homebridge_status.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _homebridge(status: str = "ok", children: list | None = None, plugins: list | None = None) -> respx.Route:
    login = respx.post(f"{BASE}/api/auth/login").mock(return_value=httpx.Response(201, json={"access_token": "token-1", "token_type": "Bearer", "expires_in": 28800}))
    respx.get(f"{BASE}/api/status/homebridge").mock(return_value=httpx.Response(200, json={"status": status}))
    respx.get(f"{BASE}/api/status/homebridge/child-bridges").mock(return_value=httpx.Response(200, json=ANSWERS["child_bridges"] if children is None else children))
    respx.get(f"{BASE}/api/plugins").mock(return_value=httpx.Response(200, json=ANSWERS["plugins"] if plugins is None else plugins))
    respx.get(f"{BASE}/api/status/homebridge-version").mock(return_value=httpx.Response(200, json=ANSWERS["version"]))
    return login


@respx.mock
async def test_it_logs_in_once_and_sends_the_token() -> None:
    login = _homebridge()
    ctx = _ctx()
    await get_adapter("homebridge").fetch("status", CONFIG, {}, ctx)
    await get_adapter("homebridge").fetch("bridges", CONFIG, {}, ctx)
    assert login.call_count == 1
    assert json.loads(login.calls[0].request.content) == {"username": "dashboard", "password": "made-up-password"}
    assert respx.calls[-1].request.headers["authorization"] == "Bearer token-1"


@respx.mock
async def test_a_refused_token_logs_in_again() -> None:
    login = _homebridge()
    respx.get(f"{BASE}/api/status/homebridge").mock(side_effect=[httpx.Response(401), httpx.Response(200, json={"status": "ok"})])
    ctx = _ctx()
    ctx.cache["homebridge_token"] = ("old-token", 9e12)
    data = await get_adapter("homebridge").fetch("bridges", CONFIG, {}, ctx)
    assert login.call_count == 1 and data.items[0]["value"] == "running"


@respx.mock
async def test_without_a_user_it_asks_for_the_noauth_token() -> None:
    _homebridge()
    noauth = respx.post(f"{BASE}/api/auth/noauth").mock(return_value=httpx.Response(201, json={"access_token": "token-2", "expires_in": 1296000}))
    await get_adapter("homebridge").fetch("status", {"url": BASE}, {}, _ctx())
    assert noauth.called


@pytest.mark.parametrize(("code", "error", "said"), [(403, AuthFailed, "refused"), (412, AuthFailed, "two-factor"), (429, AdapterError, "locked out")])
@respx.mock
async def test_a_refused_login(code: int, error: type, said: str) -> None:
    respx.post(f"{BASE}/api/auth/login").mock(return_value=httpx.Response(code, json={"statusCode": code}))
    with pytest.raises(error, match=said):
        await get_adapter("homebridge").test(CONFIG, _ctx())


@respx.mock
async def test_a_child_bridge_stopped_by_hand_is_not_a_fault() -> None:
    _homebridge()
    data = await get_adapter("homebridge").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "ok"
    assert data.primary == {"label": "Homebridge", "value": "running"}
    assert {"label": "Child bridges", "value": "0 / 1"} in data.secondary
    assert {"label": "Version", "value": "2.4.0"} in data.secondary
    bridges = await get_adapter("homebridge").fetch("bridges", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in bridges.items] == [("Homebridge", "running"), ("Dummy Bridge", "stopped")]


@respx.mock
async def test_a_child_bridge_down_by_itself_is() -> None:
    crashed = [{**ANSWERS["child_bridges"][0], "manuallyStopped": False}]
    _homebridge(children=crashed)
    data = await get_adapter("homebridge").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "warn" and data.metrics["bridges_down"] == 1.0
    bridges = await get_adapter("homebridge").fetch("bridges", CONFIG, {}, _ctx())
    assert bridges.items[1]["status"] == "bad"


@respx.mock
async def test_homebridge_down_is_red() -> None:
    _homebridge(status="down")
    data = await get_adapter("homebridge").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.primary["value"] == "down"


@respx.mock
async def test_the_setup_code_never_shows() -> None:
    _homebridge()
    for kind in ("status", "bridges"):
        data = await get_adapter("homebridge").fetch(kind, CONFIG, {}, _ctx())
        shown = json.dumps([data.primary, data.secondary, data.items])
        assert ANSWERS["child_bridges"][0]["pin"] not in shown and "X-HM://" not in shown


@respx.mock
async def test_the_updates() -> None:
    plugins = [*ANSWERS["plugins"], {**ANSWERS["plugins"][1], "name": "homebridge-ring", "displayName": "Ring",
                                     "installedVersion": "14.1.2", "latestVersion": "14.2.0", "updateAvailable": True}]
    _homebridge(plugins=plugins)
    data = await get_adapter("homebridge").fetch("updates", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in data.items] == [("Ring", "14.1.2 → 14.2.0")]
    assert data.status == "warn"
