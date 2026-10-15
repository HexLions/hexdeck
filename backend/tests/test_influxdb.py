"""InfluxDB 2: health, tasks and buckets.

``influxdb_api.json`` holds what InfluxDB v2.9.1 answered locally on
2026-10-15 to a read-only token: health, four buckets (two of them the
system's own), and three tasks (one succeeding, one failing on a missing
bucket, one inactive), and the refusal of a wrong token.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://influxdb.local:8086"
CONFIG = {"url": BASE, "org": "home", "token": "made-up-token"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "influxdb_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _influx() -> None:
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json=ANSWERS["health"]))
    respx.get(f"{BASE}/api/v2/tasks").mock(return_value=httpx.Response(200, json=ANSWERS["tasks"]))
    respx.get(f"{BASE}/api/v2/buckets").mock(return_value=httpx.Response(200, json=ANSWERS["buckets"]))


@respx.mock
async def test_the_status_counts_the_failing_task() -> None:
    _influx()
    data = await get_adapter("influxdb").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.primary == {"label": "Failing tasks", "value": 1, "metric": "failing"}
    assert data.secondary == [{"label": "Tasks", "value": "2/3"}, {"label": "Buckets", "value": 2}, {"label": "Version", "value": "v2.9.1"}]
    assert data.meta["notice"] == "Failed last time: copy to archive"
    assert respx.calls[0].request.headers["authorization"] == "Token made-up-token"


@respx.mock
async def test_the_tasks_with_the_error() -> None:
    _influx()
    data = await get_adapter("influxdb").fetch("tasks", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["copy to archive", "downsample cpu", "old export"]
    assert 'could not find bucket "archive-that-does-not-exist"' in data.items[0]["subtitle"]
    assert data.items[2]["subtitle"].startswith("inactive")


@respx.mock
async def test_the_buckets_without_the_system_ones() -> None:
    _influx()
    data = await get_adapter("influxdb").fetch("buckets", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in data.items] == [("downsampled", "keeps forever"), ("homelab", "keeps 30d")]


@respx.mock
async def test_a_wrong_token_and_an_unknown_organisation() -> None:
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, json=ANSWERS["health"]))
    route = respx.get(f"{BASE}/api/v2/tasks").mock(return_value=httpx.Response(401, json=ANSWERS["unauthorized"]))
    with pytest.raises(AuthFailed):
        await get_adapter("influxdb").test(CONFIG, _ctx())
    route.mock(return_value=httpx.Response(400, json={"code": "invalid", "message": 'failed to decode request: organization name "nope" not found'}))
    with pytest.raises(AdapterError, match="no organisation of that name"):
        await get_adapter("influxdb").test({**CONFIG, "org": "nope"}, _ctx())
