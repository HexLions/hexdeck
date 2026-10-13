"""mailcow: containers, the Postfix queue, storage and mailboxes.

``mailcow_api.json`` is written in the shape mailcow 2026-09a answers with,
as ``json_api.php`` builds it and ``openapi.yaml`` shows it: eighteen
containers with ClamAV stopped, a deferred mail and an active one with two
recipients, storage at 61 %, and three mailboxes (one nearly full, one
without a quota, one inactive). The two refusals are the ones in
``sessions.inc.php``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "https://mail.example.com"
CONFIG = {"url": BASE, "api_key": "MADE-UP-KEY"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "mailcow_api.json").read_text())
PATHS = {"status/containers": "containers", "mailq/all": "queue", "status/vmail": "vmail", "status/version": "version", "mailbox/all": "mailboxes"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _mailcow() -> None:
    for path, name in PATHS.items():
        respx.get(f"{BASE}/api/v1/get/{path}").mock(return_value=httpx.Response(200, json=ANSWERS[name]))


@respx.mock
async def test_a_stopped_container_turns_it_red() -> None:
    _mailcow()
    data = await get_adapter("mailcow").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.primary == {"label": "Containers", "value": "17/18"}
    assert data.secondary == [{"label": "Queue", "value": 2, "metric": "queue"}, {"label": "Storage", "value": "61 %"},
                              {"label": "Version", "value": "2026-09a"}]
    assert data.meta["notice"] == "Not running: clamd"
    assert respx.calls[0].request.headers["x-api-key"] == "MADE-UP-KEY"


@respx.mock
async def test_the_queue_with_the_reason_it_waits() -> None:
    _mailcow()
    data = await get_adapter("mailcow").fetch("queue", CONFIG, {}, _ctx())
    deferred, active = data.items
    assert deferred["title"] == "newsletter@example.com → anna@example.org"
    assert deferred["subtitle"].startswith("deferred · connect to mx.example.org[203.0.113.7]:25: Connection timed out")
    assert deferred["status"] == "warn"
    assert active["title"] == "ben@example.com → carla@example.net +1"


@respx.mock
async def test_the_fullest_mailbox_first_and_one_without_a_quota() -> None:
    _mailcow()
    data = await get_adapter("mailcow").fetch("mailboxes", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["anna@example.com", "ben@example.com", "info@example.com"]
    assert data.items[0]["status"] == "bad" and data.items[0]["progress"] == 92.0
    assert data.items[1]["subtitle"].startswith("inactive")
    assert data.items[2]["progress"] is None and " of " not in data.items[2]["value"]


@respx.mock
async def test_the_two_refusals_are_told_apart() -> None:
    route = respx.get(f"{BASE}/api/v1/get/status/version").mock(return_value=httpx.Response(401, json=ANSWERS["denied_ip"]))
    with pytest.raises(AuthFailed, match=r"from this address \(192.168.1.20\)"):
        await get_adapter("mailcow").test(CONFIG, _ctx())
    route.mock(return_value=httpx.Response(401, json=ANSWERS["wrong_key"]))
    with pytest.raises(AuthFailed, match="refused the API key"):
        await get_adapter("mailcow").test(CONFIG, _ctx())


@respx.mock
async def test_the_connection_test() -> None:
    _mailcow()
    assert await get_adapter("mailcow").test(CONFIG, _ctx()) == "mailcow 2026-09a answers with 18 containers."
