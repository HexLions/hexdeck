"""Flood: the torrents, the sign-in and the buttons.

``flood_api.json`` holds what Flood 4.16.4 answered locally on 2026-11-16 in front of Transmission 4.1.0: one torrent
complete and seeding, one without its data, once running and once stopped, and its refusals.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.flood import FloodAdapter

BASE = "http://flood.local:3000"
CONFIG = {"url": BASE, "username": "admin", "password": "made-up-password"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "flood_api.json").read_text())
SEEDING, MISSING = "3A6C3D19DA28B35F1621A59B38EDDFB25CEFF446", "A1DBBB14FC989FF415579388D3C531C151B8975C"


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _signed_in(torrents: dict | None = None) -> respx.Route:
    """Flood refuses until the jwt cookie comes along, as it does."""

    def answer(request: httpx.Request) -> httpx.Response:
        if "jwt=made-up-token" not in request.headers.get("cookie", ""):
            return httpx.Response(401, json=ANSWERS["unauthorized"])
        return httpx.Response(200, json=torrents or ANSWERS["torrents"])

    respx.post(f"{BASE}/api/auth/authenticate").mock(
        return_value=httpx.Response(200, json={"success": True, "username": "admin", "level": 10}, headers={"set-cookie": "jwt=made-up-token; Path=/; HttpOnly"}))
    respx.get(f"{BASE}/api/client/connection-test").mock(return_value=httpx.Response(200, json=ANSWERS["connection_test"]))
    return respx.get(f"{BASE}/api/torrents").mock(side_effect=answer)


@respx.mock
async def test_it_signs_in_once_and_keeps_the_cookie() -> None:
    torrents = _signed_in()
    ctx = _ctx()
    assert await get_adapter("flood").test(CONFIG, ctx) == "Flood answers, 2 torrents."
    sign_in = next(call.request for call in respx.calls if call.request.url.path == "/api/auth/authenticate")
    assert json.loads(sign_in.content) == {"username": "admin", "password": "made-up-password"}
    assert "made-up-password" not in str(sign_in.url)
    await get_adapter("flood").fetch("speed", CONFIG, {}, ctx)
    assert sum(1 for call in respx.calls if call.request.url.path == "/api/auth/authenticate") == 1
    assert torrents.call_count == 3, "refused once, then twice with the cookie"
    await get_adapter("flood").close(CONFIG, ctx)
    assert "flood_client" not in ctx.cache


@respx.mock
async def test_a_wrong_password() -> None:
    respx.get(f"{BASE}/api/torrents").mock(return_value=httpx.Response(401, json=ANSWERS["unauthorized"]))
    respx.post(f"{BASE}/api/auth/authenticate").mock(return_value=httpx.Response(401, json=ANSWERS["failed_login"]))
    with pytest.raises(AuthFailed):
        await get_adapter("flood").test(CONFIG, _ctx())


@respx.mock
async def test_flood_without_its_client() -> None:
    _signed_in()
    respx.get(f"{BASE}/api/client/connection-test").mock(return_value=httpx.Response(200, json={"isConnected": False}))
    with pytest.raises(AdapterError) as away:
        await get_adapter("flood").test(CONFIG, _ctx())
    assert away.value.code == "client_unreachable"


def test_the_queue_leaves_the_complete_ones_out() -> None:
    snapshot = FloodAdapter._snapshot(list(ANSWERS["torrents"]["torrents"].values()))
    assert [(item.name, item.state, item.progress) for item in snapshot.items] == [("linux-image.iso", "stalled", 0.0)]
    assert snapshot.total == 1 and snapshot.remaining_bytes == 2_000_000 and snapshot.paused is False
    assert snapshot.download_bps == 0 and snapshot.upload_bps == 0


def test_a_stopped_torrent_is_paused_but_the_client_is_not() -> None:
    snapshot = FloodAdapter._snapshot(list(ANSWERS["torrents_stopped"]["torrents"].values()))
    assert snapshot.items[0].state == "paused"
    assert snapshot.paused is False, "the other one still seeds"


def test_the_speeds_are_added_up() -> None:
    torrents = [{"hash": "a", "status": ["downloading", "active"], "downRate": 1_000_000, "upRate": 20_000, "sizeBytes": 10, "bytesDone": 5},
                {"hash": "b", "status": ["seeding", "active", "complete"], "downRate": 0, "upRate": 50_000}]
    snapshot = FloodAdapter._snapshot(torrents)
    assert snapshot.download_bps == 1_000_000 and snapshot.upload_bps == 70_000
    assert snapshot.items[0].state == "downloading"


@respx.mock
async def test_pause_stops_what_a_fresh_read_lists() -> None:
    _signed_in()
    stop = respx.post(f"{BASE}/api/torrents/stop").mock(return_value=httpx.Response(200))
    await get_adapter("flood").action("speed", "pause", {}, CONFIG, {}, _ctx())
    assert json.loads(stop.calls[0].request.content) == {"hashes": [SEEDING, MISSING]}


@respx.mock
async def test_resume_starts_the_stopped_ones_only() -> None:
    _signed_in(ANSWERS["torrents_stopped"])
    start = respx.post(f"{BASE}/api/torrents/start").mock(return_value=httpx.Response(200))
    await get_adapter("flood").action("speed", "resume", {}, CONFIG, {}, _ctx())
    assert json.loads(start.calls[0].request.content) == {"hashes": [MISSING]}


@pytest.mark.parametrize("kind", ["queue", "speed"])
def test_every_card_has_a_demo(kind: str) -> None:
    data = get_adapter("flood").demo(kind, {}, 3)
    assert data.primary or data.items
