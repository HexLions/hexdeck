"""aria2 over JSON-RPC.

``aria2_rpc.json`` holds what aria2 1.37.0 answered locally on 2026-10-13
with ``--rpc-secret``: three downloads running from a local file server,
one paused, one waiting, one finished and one that failed with "Resource
not found". The download folder is written as ``/downloads``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://aria2.local:6800"
CONFIG = {"url": BASE, "secret": "made-up-secret"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "aria2_rpc.json").read_text())
METHODS = {"aria2.getGlobalStat": "global", "aria2.tellActive": "active", "aria2.tellWaiting": "waiting",
           "aria2.tellStopped": "stopped", "aria2.getVersion": "version"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _aria2() -> respx.Route:
    def answer(request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)
        if call["params"][:1] != ["token:made-up-secret"]:
            return httpx.Response(400, json={"id": call["id"], "jsonrpc": "2.0", "error": {"code": 1, "message": "Unauthorized"}})
        if call["method"] in ("aria2.pauseAll", "aria2.unpauseAll"):
            return httpx.Response(200, json={"id": call["id"], "jsonrpc": "2.0", "result": "OK"})
        return httpx.Response(200, json={"id": call["id"], "jsonrpc": "2.0", "result": ANSWERS[METHODS[call["method"]]]})

    return respx.post(f"{BASE}/jsonrpc").mock(side_effect=answer)


@respx.mock
async def test_the_queue_with_the_failed_one_last() -> None:
    _aria2()
    data = await get_adapter("aria2").fetch("queue", CONFIG, {"limit": 10}, _ctx())
    titles = [row["title"] for row in data.items]
    assert titles == ["big-archive.iso", "second-image.img", "waiting-copy.iso", "paused-copy.img", "queued-later.zip", "does-not-exist.tar"]
    failed = data.items[-1]
    assert failed["status"] == "bad" and "Resource not found" in failed["subtitle"]
    assert data.items[3]["status"] == "warn" and "paused" in data.items[3]["subtitle"]
    assert {"label": "Queue", "value": 5} in data.secondary, "the failed one is shown, not counted as queued"


@respx.mock
async def test_the_speed_is_read_from_strings() -> None:
    _aria2()
    data = await get_adapter("aria2").fetch("speed", CONFIG, {}, _ctx())
    assert data.primary["label"] == "Download" and data.primary["value"] == 0.3
    assert [action.id for action in data.actions] == ["pause"]


@respx.mock
async def test_the_secret_is_the_first_parameter() -> None:
    route = _aria2()
    assert await get_adapter("aria2").test(CONFIG, _ctx()) == "aria2 1.37.0 answers, 3 downloads running."
    assert json.loads(route.calls[0].request.content)["params"][0] == "token:made-up-secret"
    assert "authorization" not in route.calls[0].request.headers


@respx.mock
async def test_a_wrong_secret_is_a_400_that_says_unauthorized() -> None:
    _aria2()
    with pytest.raises(AuthFailed):
        await get_adapter("aria2").test({**CONFIG, "secret": "wrong"}, _ctx())


@respx.mock
async def test_pause_and_resume_everything() -> None:
    route = _aria2()
    assert await get_adapter("aria2").action("speed", "pause", {}, CONFIG, {}, _ctx()) == "Downloads paused."
    assert json.loads(route.calls[-1].request.content)["method"] == "aria2.pauseAll"
    await get_adapter("aria2").action("speed", "resume", {}, CONFIG, {}, _ctx())
    assert json.loads(route.calls[-1].request.content)["method"] == "aria2.unpauseAll"


@respx.mock
async def test_an_address_that_already_ends_in_jsonrpc() -> None:
    _aria2()
    await get_adapter("aria2").test({**CONFIG, "url": f"{BASE}/jsonrpc"}, _ctx())
