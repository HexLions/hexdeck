"""Node-RED: whether the flows run, the node types missing, and the flows.

``nodered_runtime.json`` holds what Node-RED 5.0.8 answered locally on
2026-10-10 to a read-only user: ``/flows`` (three flows, one disabled, and a
``tasmota-switch`` node whose type is not installed), ``/nodes``,
``/flows/state``, which said "start" all the same, and ``/diagnostics``,
trimmed to the parts read. The token answers follow the same run.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context
from app.adapters.nodered import missing_types

BASE = "http://nodered.local:1880"
CONFIG = {"url": BASE, "username": "viewer", "password": "made-up-password"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "nodered_runtime.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _nodered(flows: dict | None = None) -> respx.Route:
    login = respx.post(f"{BASE}/auth/token").mock(return_value=httpx.Response(200, json={"access_token": "token-1", "expires_in": 604800, "token_type": "Bearer"}))
    respx.get(f"{BASE}/flows").mock(return_value=httpx.Response(200, json=flows or ANSWERS["flows"]))
    respx.get(f"{BASE}/nodes").mock(return_value=httpx.Response(200, json=ANSWERS["nodes"]))
    respx.get(f"{BASE}/flows/state").mock(return_value=httpx.Response(200, json=ANSWERS["state"]))
    respx.get(f"{BASE}/diagnostics").mock(return_value=httpx.Response(200, json=ANSWERS["diagnostics"]))
    return login


def test_the_missing_types() -> None:
    assert missing_types(ANSWERS["flows"]["flows"], ANSWERS["nodes"]) == ["tasmota-switch"]
    assert missing_types([{"type": "tab"}, {"type": "subflow:abc"}, {"type": "group"}], []) == []


@respx.mock
async def test_it_asks_a_read_token_once() -> None:
    login = _nodered()
    ctx = _ctx()
    await get_adapter("nodered").fetch("status", CONFIG, {}, ctx)
    await get_adapter("nodered").fetch("flows", CONFIG, {}, ctx)
    assert login.call_count == 1
    body = login.calls[0].request.content.decode()
    assert "scope=read" in body and "client_id=node-red-admin" in body
    assert respx.calls[-1].request.headers["authorization"] == "Bearer token-1"


@respx.mock
async def test_without_admin_auth_no_token_is_asked() -> None:
    login = _nodered()
    await get_adapter("nodered").fetch("status", {"url": BASE}, {}, _ctx())
    assert not login.called and "authorization" not in respx.calls[0].request.headers


@respx.mock
async def test_started_but_waiting_for_a_missing_type() -> None:
    _nodered()
    data = await get_adapter("nodered").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.primary == {"label": "Flows", "value": "waiting"}
    assert data.meta["notice"] == "Missing node types: tasmota-switch"
    assert {"label": "Enabled flows", "value": "2 / 3"} in data.secondary
    assert {"label": "Version", "value": "5.0.8"} in data.secondary


@respx.mock
async def test_running_when_nothing_is_missing() -> None:
    flows = {**ANSWERS["flows"], "flows": [one for one in ANSWERS["flows"]["flows"] if one.get("type") != "tasmota-switch"]}
    _nodered(flows)
    data = await get_adapter("nodered").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "ok" and data.primary["value"] == "running" and data.meta["notice"] == ""


@respx.mock
async def test_the_flows_the_broken_one_first() -> None:
    _nodered()
    data = await get_adapter("nodered").fetch("flows", CONFIG, {}, _ctx())
    assert [(row["title"], row["subtitle"], row["value"]) for row in data.items] == [
        ("Lights", "missing tasmota-switch", ""), ("Backups", "3 nodes", ""), ("Old experiments", "1 node", "disabled")]


@respx.mock
async def test_a_refused_login() -> None:
    respx.post(f"{BASE}/auth/token").mock(return_value=httpx.Response(403, json={"error": "invalid_grant"}))
    with pytest.raises(AuthFailed):
        await get_adapter("nodered").test(CONFIG, _ctx())
