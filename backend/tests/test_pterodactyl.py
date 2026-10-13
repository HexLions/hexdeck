"""Pterodactyl's client API: servers, their live state, start and stop.

``pterodactyl_client.json`` is written in the shape the panel's client
``ServerTransformer`` and ``StatsTransformer`` produce in release v1.15.1:
five servers, one running, one starting, one offline, one whose install
failed, and one whose node does not answer (the panel's 504 for it).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "https://panel.example.com"
CONFIG = {"url": BASE, "token": "ptlc_made-up"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "pterodactyl_client.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _panel() -> None:
    respx.get(f"{BASE}/api/client").mock(return_value=httpx.Response(200, json=ANSWERS["servers"]))
    for identifier, answer in ANSWERS["resources"].items():
        respx.get(f"{BASE}/api/client/servers/{identifier}/resources").mock(return_value=httpx.Response(200, json=answer))
    respx.get(f"{BASE}/api/client/servers/{ANSWERS['unreachable']}/resources").mock(return_value=httpx.Response(504, json={"errors": []}))


@respx.mock
async def test_the_overview() -> None:
    _panel()
    data = await get_adapter("pterodactyl").fetch("overview", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Running", "value": "1/5", "metric": "running"}
    assert data.secondary == [{"label": "Offline", "value": 1}, {"label": "Flagged", "value": 2}]
    assert data.status == "bad" and data.meta["notice"] == "install failed, node unreachable"


@respx.mock
async def test_each_server_with_what_it_uses() -> None:
    _panel()
    data = await get_adapter("pterodactyl").fetch("servers", CONFIG, {}, _ctx())
    rows = {row["title"]: row for row in data.items}
    assert rows["Minecraft survival"]["subtitle"] == "running · CPU 87 % of 300 % · 3.7 GB of 6.0 GB · up 41h"
    assert rows["Valheim"]["status"] == "warn" and rows["Valheim"]["subtitle"] == "starting"
    assert rows["Terraria"]["status"] == "unknown"
    assert rows["Rust"]["subtitle"] == "install failed" and rows["Rust"]["status"] == "bad"
    assert rows["Factorio"]["subtitle"] == "node unreachable"
    assert all(row["actions"] == [] for row in data.items)


@respx.mock
async def test_start_and_stop_only_a_listed_server() -> None:
    _panel()
    power = respx.post(f"{BASE}/api/client/servers/9c4d8a02/power").mock(return_value=httpx.Response(204))
    data = await get_adapter("pterodactyl").fetch("servers", CONFIG, {"power": True}, _ctx())
    rows = {row["title"]: row for row in data.items}
    assert [action["id"] for action in rows["Minecraft survival"]["actions"]] == ["stop", "restart"]
    assert rows["Minecraft survival"]["actions"][0]["confirm"] is True
    assert [action["id"] for action in rows["Terraria"]["actions"]] == ["start"]
    assert rows["Rust"]["actions"] == []
    assert await get_adapter("pterodactyl").action("servers", "start", {"server": "9c4d8a02"}, CONFIG, {}, _ctx()) == "Terraria: start sent."
    assert json.loads(power.calls[0].request.content) == {"signal": "start"}
    with pytest.raises(AdapterError):
        await get_adapter("pterodactyl").action("servers", "start", {"server": "not-mine"}, CONFIG, {}, _ctx())


@respx.mock
async def test_a_wrong_key() -> None:
    respx.get(f"{BASE}/api/client").mock(return_value=httpx.Response(401, json=ANSWERS["unauthenticated"]))
    with pytest.raises(AuthFailed):
        await get_adapter("pterodactyl").test(CONFIG, _ctx())


@respx.mock
async def test_the_key_goes_as_bearer() -> None:
    _panel()
    assert await get_adapter("pterodactyl").test(CONFIG, _ctx()) == "The panel answers with 5 servers."
    assert respx.calls[0].request.headers["authorization"] == "Bearer ptlc_made-up"
