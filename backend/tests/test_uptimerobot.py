"""UptimeRobot: monitors, the last day's uptime, and the incidents still open.

The answers below follow UptimeRobot's OpenAPI spec for API 3.0
(cdn.uptimerobot.com/api/openapi.yaml) as of 2026-10-09, which has no
example answers: the shapes are its schemas, the status words are the ones
its monitor filter names. The refusal of a made-up key was measured against
api.uptimerobot.com the same day.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

API = "https://api.uptimerobot.com/v3"
CONFIG = {"token": "made-up-key"}


def _day(value: float) -> dict:
    return {"bucketSize": 3600, "totalChanges": 0, "histogram": [{"timestamp": 1791500000 + hour * 3600, "uptime": value} for hour in range(24)]}


MONITORS = {"nextLink": None, "data": [
    {"id": 1, "friendlyName": "Home Assistant", "type": "HTTP", "url": "https://ha.example.com", "status": "UP", "interval": 300,
     "lastDayUptimes": _day(100), "httpPassword": "secret-password", "apiKey": "monitor-key", "customHttpHeaders": {"X-Token": "secret"}},
    {"id": 2, "friendlyName": "Blog", "type": "HTTP", "url": "https://blog.example.com", "status": "DOWN", "interval": 300, "lastDayUptimes": _day(97.5)},
    {"id": 3, "friendlyName": "Mail", "type": "PORT", "url": "mail.example.com", "status": "LOOKS_DOWN", "interval": 60},
    {"id": 4, "friendlyName": "Backups", "type": "HEARTBEAT", "url": None, "status": "PAUSED", "interval": 86400},
    {"id": 5, "friendlyName": "New one", "type": "PING", "url": "203.0.113.5", "status": "STARTED", "interval": 300},
]}
INCIDENTS = {"nextLink": None, "data": [
    {"id": "77", "status": "OPEN", "type": "DOWNTIME", "cause": 1, "reason": "Connection Timeout",
     "monitor": {"id": 2, "friendlyName": "Blog"}, "commentsCount": 0, "startedAt": "2026-10-09T10:00:00Z", "resolvedAt": None, "duration": None},
]}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _uptimerobot() -> None:
    respx.get(f"{API}/monitors").mock(return_value=httpx.Response(200, json=MONITORS))
    respx.get(f"{API}/incidents").mock(return_value=httpx.Response(200, json=INCIDENTS))


@respx.mock
async def test_the_key_goes_in_as_bearer() -> None:
    _uptimerobot()
    await get_adapter("uptimerobot").fetch("summary", CONFIG, {}, _ctx())
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-key"


@respx.mock
async def test_the_summary_counts_up_against_what_is_not_paused() -> None:
    _uptimerobot()
    data = await get_adapter("uptimerobot").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Up", "value": "1 / 4"}
    assert {row["label"]: row["value"] for row in data.secondary} == {"Down": 1, "Looks down": 1, "Paused": 1, "Monitors": 5}
    assert data.status == "bad" and data.metrics == {"down": 1.0}


@respx.mock
async def test_the_monitors_put_down_first_and_show_uptime_for_the_ones_up() -> None:
    _uptimerobot()
    data = await get_adapter("uptimerobot").fetch("monitors", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"], row["status"]) for row in data.items] == [
        ("Blog", "down", "bad"), ("Mail", "looks down", "warn"), ("Backups", "paused", "unknown"),
        ("New one", "not checked yet", "unknown"), ("Home Assistant", "100.00%", "ok")]
    assert data.items[0]["subtitle"] == "http · https://blog.example.com"


@respx.mock
async def test_only_what_is_not_up_leaves_the_rest_out() -> None:
    _uptimerobot()
    data = await get_adapter("uptimerobot").fetch("monitors", CONFIG, {"down_only": True}, _ctx())
    assert "Home Assistant" not in [row["title"] for row in data.items]


@respx.mock
async def test_the_secrets_a_monitor_carries_never_reach_a_card() -> None:
    """⚠️ A monitor's answer holds its HTTP password, headers and API key."""
    _uptimerobot()
    adapter = get_adapter("uptimerobot")
    said = ""
    for widget in adapter.widgets:
        said += json.dumps((await adapter.fetch(widget.kind, CONFIG, {}, _ctx())).__dict__, default=str)
    assert "secret" not in said and "monitor-key" not in said


@respx.mock
async def test_open_incidents_name_the_monitor_and_the_reason() -> None:
    _uptimerobot()
    data = await get_adapter("uptimerobot").fetch("incidents", CONFIG, {}, _ctx())
    assert respx.calls[0].request.url.params["status"] == "open"
    assert data.items[0]["title"] == "Blog" and data.items[0]["subtitle"].startswith("Connection Timeout")
    assert data.status == "bad" and data.primary == {"label": "Open", "value": 1}


@respx.mock
async def test_a_refused_key_as_measured() -> None:
    respx.get(f"{API}/monitors").mock(return_value=httpx.Response(401, json={"message": "Invalid token.", "code": "003-005"}))
    with pytest.raises(AuthFailed):
        await get_adapter("uptimerobot").fetch("monitors", CONFIG, {}, _ctx())


@respx.mock
async def test_the_rate_limit_is_said_for_what_it_is() -> None:
    respx.get(f"{API}/monitors").mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("uptimerobot").fetch("monitors", CONFIG, {}, _ctx())
    assert failure.value.code == "rate_limited"


@respx.mock
async def test_the_connection_test_counts_the_monitors() -> None:
    _uptimerobot()
    assert await get_adapter("uptimerobot").test(CONFIG, _ctx()) == "UptimeRobot answers with 5 monitors."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("uptimerobot")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary or data.primary, widget.kind
