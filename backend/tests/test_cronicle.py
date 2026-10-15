"""Cronicle: failing events, running jobs and the run button.

``cronicle_api.json`` holds what Cronicle 0.9.136 answered locally on
2026-10-15 with an API key: four shell events (one disabled), one job
running, and a history with a success and a failure with exit code 3. The
server's name is written as ``cronicle-01``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://cronicle.local:3012"
CONFIG = {"url": BASE, "api_key": "made-up-key"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "cronicle_api.json").read_text())
CALLS = {"get_schedule": "schedule", "get_active_jobs": "active", "get_history": "history"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _cronicle() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        call = request.url.path.split("/")[3]
        if request.headers.get("x-api-key") != "made-up-key":
            return httpx.Response(200, json={"code": "api", "description": f"Invalid API Key: {request.headers.get('x-api-key')}"})
        if call == "run_event":
            return httpx.Response(200, json={"code": 0, "ids": ["jmade-up"]})
        return httpx.Response(200, json=ANSWERS[CALLS[call]])

    respx.route(url__regex=rf"{BASE}/api/app/\w+/v1").mock(side_effect=answer)


@respx.mock
async def test_the_overview_counts_the_event_that_failed() -> None:
    _cronicle()
    data = await get_adapter("cronicle").fetch("overview", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.primary == {"label": "Failing", "value": 1, "metric": "failing"}
    assert data.secondary == [{"label": "Running", "value": 1}, {"label": "Events", "value": "3/4"}]
    assert data.meta["notice"] == "Failed last time: Renew certificates"


@respx.mock
async def test_each_event_with_its_last_result() -> None:
    _cronicle()
    data = await get_adapter("cronicle").fetch("events", CONFIG, {}, _ctx())
    rows = {row["title"]: row for row in data.items}
    assert [row["title"] for row in data.items][0] == "Renew certificates"
    assert rows["Renew certificates"]["subtitle"].startswith("failed: Script exited with code: 3: cannot reach ACME")
    assert rows["Media scan"]["subtitle"].startswith("running") and rows["Media scan"]["status"] == "warn"
    assert rows["Nightly backup"]["subtitle"].startswith("succeeded")
    assert rows["Old cleanup"]["subtitle"] == "disabled"
    assert all(row["actions"] == [] for row in data.items), "no button unless the card asks"


@respx.mock
async def test_the_run_button_runs_only_a_listed_event() -> None:
    _cronicle()
    data = await get_adapter("cronicle").fetch("events", CONFIG, {"running": True}, _ctx())
    rows = {row["title"]: row for row in data.items}
    assert rows["Media scan"]["actions"] == [] and rows["Old cleanup"]["actions"] == [], "not while running, not when disabled"
    backup = rows["Nightly backup"]["actions"][0]
    assert backup["confirm"] is True
    assert await get_adapter("cronicle").action("events", "run", backup["params"], CONFIG, {}, _ctx()) == "Nightly backup started."
    assert json.loads(respx.calls[-1].request.content) == {"id": backup["params"]["event"]}
    with pytest.raises(AdapterError):
        await get_adapter("cronicle").action("events", "run", {"event": "made-up"}, CONFIG, {}, _ctx())


@respx.mock
async def test_a_wrong_key_says_so_in_a_200() -> None:
    _cronicle()
    with pytest.raises(AuthFailed):
        await get_adapter("cronicle").test({**CONFIG, "api_key": "wrong"}, _ctx())


@respx.mock
async def test_a_server_that_is_not_the_master() -> None:
    respx.get(f"{BASE}/api/app/get_schedule/v1").mock(return_value=httpx.Response(200, json={
        "code": "master", "description": "This API call can only be invoked on the master server."}))
    with pytest.raises(AdapterError, match="not the master"):
        await get_adapter("cronicle").test(CONFIG, _ctx())
