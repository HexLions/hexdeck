"""Woodpecker CI, against Woodpecker 3.19.0 run locally on 21.10.2026.

The fixture is what that server answered an administrator's personal token,
with Forgejo 17.0.0 as the forge, one agent on the local backend, a pipeline
that passed and one that ended with exit 3; ``queue_running`` was read while a
pipeline ran. Addresses are replaced, and so is the agent secret that
``/api/agents`` hands back.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

WP = "https://ci.local"
CONFIG = {"url": WP, "token": "test-personal-token"}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "woodpecker_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _server(queue: httpx.Response | None = None, agents: httpx.Response | None = None) -> None:
    respx.get(f"{WP}/api/user/repos").mock(return_value=httpx.Response(200, json=FIXTURE["repos"]))
    respx.get(f"{WP}/api/user/feed").mock(return_value=httpx.Response(200, json=FIXTURE["feed"]))
    fresh = [{**one, "last_contact": int(time.time()) - 5} for one in FIXTURE["agents"]]
    respx.get(f"{WP}/api/agents").mock(return_value=agents or httpx.Response(200, json=fresh))
    respx.get(f"{WP}/api/queue/info").mock(return_value=queue or httpx.Response(200, json=FIXTURE["queue_running"]))


@respx.mock
async def test_the_summary_for_an_administrator() -> None:
    _server()
    data = await get_adapter("woodpecker").fetch("summary", CONFIG, {}, _ctx())
    assert data.status == "bad", "broken-app's last pipeline failed"
    assert data.primary == {"label": "Running", "value": 1}
    assert data.secondary == [{"label": "Failing", "value": 1}, {"label": "Waiting", "value": 0}, {"label": "Agents", "value": "1 / 1"}]
    assert "agent-secret" not in json.dumps(data.model_dump()), "the agent's secret never reaches the card"
    assert respx.calls[0].request.headers["authorization"] == "Bearer test-personal-token"


@respx.mock
async def test_the_summary_for_somebody_else() -> None:
    """Queue and agents are for administrators; Woodpecker says 403 to the rest."""
    _server(queue=httpx.Response(403, text=FIXTURE["unauthorized_body"]), agents=httpx.Response(403, text=FIXTURE["unauthorized_body"]))
    data = await get_adapter("woodpecker").fetch("summary", CONFIG, {}, _ctx())
    assert data.secondary == [{"label": "Failing", "value": 1}]
    assert data.primary == {"label": "Running", "value": 0}, "counted from the repositories instead"


@respx.mock
async def test_the_repositories_failing_first() -> None:
    _server()
    rows = (await get_adapter("woodpecker").fetch("repos", CONFIG, {}, _ctx())).items
    assert [(row["title"], row["value"], row["status"]) for row in rows] == [
        ("fjadmin/broken-app", "Failed", "bad"), ("fjadmin/hello-app", "Passed", "ok")]
    assert rows[0]["subtitle"].startswith("main · ")


@respx.mock
async def test_the_latest_pipelines() -> None:
    _server()
    rows = (await get_adapter("woodpecker").fetch("pipelines", CONFIG, {"limit": 2}, _ctx())).items
    assert [row["title"] for row in rows] == ["fjadmin/hello-app #2", "fjadmin/broken-app #2"]
    assert rows[0]["subtitle"].startswith("main · manual")


@respx.mock
async def test_refusals() -> None:
    respx.get(f"{WP}/api/user/repos").mock(side_effect=[
        httpx.Response(401, text=FIXTURE["unauthorized_body"]), httpx.Response(403, text=FIXTURE["unauthorized_body"]),
        httpx.Response(500), httpx.Response(200, text="<html>"), httpx.Response(200, json={"not": "a list"})])
    adapter = get_adapter("woodpecker")
    for _ in range(2):
        with pytest.raises(AuthFailed) as failure:
            await adapter.fetch("repos", CONFIG, {}, _ctx())
        assert failure.value.hint
    for code in ("http_error", "not_woodpecker", "not_woodpecker"):
        with pytest.raises(AdapterError) as failure:
            await adapter.fetch("repos", CONFIG, {}, _ctx())
        assert failure.value.code == code


@respx.mock
async def test_the_connection_test() -> None:
    _server()
    assert await get_adapter("woodpecker").test(CONFIG, _ctx()) == "Woodpecker answers with 2 active repositories."


def test_the_demo() -> None:
    adapter = get_adapter("woodpecker")
    for tick in range(6):
        for kind in ("summary", "repos", "pipelines"):
            assert adapter.demo(kind, {}, tick).status in ("ok", "bad")
