"""MySpeed: the last speed test, today's failures, and the tests before.

``myspeed_tests.json`` holds what MySpeed 1.0.9 answered locally on
2026-10-10: three tests (two over LibreSpeed, one failed over Cloudflare
with "Validation error"), the day's statistics, the config with the
expected speeds and the pause status. The password header and its refusal
were measured the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://myspeed.local:5216"
CONFIG = {"url": BASE, "password": "made-up-password"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "myspeed_tests.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _myspeed(tests: list | None = None, expected: dict | None = None) -> None:
    respx.get(f"{BASE}/api/speedtests").mock(return_value=httpx.Response(200, json=ANSWERS["tests"] if tests is None else tests))
    respx.get(f"{BASE}/api/speedtests/statistics").mock(return_value=httpx.Response(200, json=ANSWERS["statistics"]))
    respx.get(f"{BASE}/api/config").mock(return_value=httpx.Response(200, json=expected or ANSWERS["config"]))
    respx.get(f"{BASE}/api/speedtests/status").mock(return_value=httpx.Response(200, json=ANSWERS["status"]))


@respx.mock
async def test_the_password_goes_in_its_own_header() -> None:
    _myspeed()
    await get_adapter("myspeed").test(CONFIG, _ctx())
    assert respx.calls[0].request.headers["password"] == "made-up-password"
    await get_adapter("myspeed").test({"url": BASE}, _ctx())
    assert "password" not in respx.calls[-1].request.headers


@respx.mock
async def test_a_wrong_password() -> None:
    respx.get(f"{BASE}/api/speedtests").mock(return_value=httpx.Response(401, json={"message": "Please provide the correct password in the header"}))
    with pytest.raises(AuthFailed):
        await get_adapter("myspeed").test(CONFIG, _ctx())


@respx.mock
async def test_the_last_test_with_a_failure_today() -> None:
    _myspeed()
    data = await get_adapter("myspeed").fetch("latest", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Download", "value": "652.83 Mbit/s", "metric": "download"}
    assert {"label": "Ping", "value": "16 ms", "metric": "ping"} in data.secondary
    assert {"label": "Failed today", "value": 1} in data.secondary
    assert data.status == "warn"
    assert data.metrics == {"download": 652.83, "upload": 205.91, "ping": 16.0}


@respx.mock
async def test_a_last_test_that_failed_is_red_and_says_why() -> None:
    failed, *rest = [ANSWERS["tests"][-1], *ANSWERS["tests"][:-1]]
    _myspeed(tests=[failed, *rest])
    data = await get_adapter("myspeed").fetch("latest", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.meta["notice"] == "The last test failed: Validation error"
    assert data.primary["value"] == "652.83 Mbit/s"


@respx.mock
async def test_below_the_expected_speed_is_amber() -> None:
    good = [one for one in ANSWERS["tests"] if not one.get("error")]
    respx.get(f"{BASE}/api/speedtests/statistics").mock(return_value=httpx.Response(200, json={"tests": {"total": 2, "failed": 0}}))
    respx.get(f"{BASE}/api/speedtests").mock(return_value=httpx.Response(200, json=good))
    respx.get(f"{BASE}/api/speedtests/status").mock(return_value=httpx.Response(200, json={"paused": False, "running": False}))
    respx.get(f"{BASE}/api/config").mock(return_value=httpx.Response(200, json={**ANSWERS["config"], "download": "1000"}))
    assert (await get_adapter("myspeed").fetch("latest", CONFIG, {}, _ctx())).status == "warn"
    respx.get(f"{BASE}/api/config").mock(return_value=httpx.Response(200, json=ANSWERS["config"]))
    assert (await get_adapter("myspeed").fetch("latest", CONFIG, {}, _ctx())).status == "ok"


@respx.mock
async def test_no_test_yet() -> None:
    _myspeed(tests=[])
    data = await get_adapter("myspeed").fetch("latest", CONFIG, {}, _ctx())
    assert data.status == "unknown" and data.meta["notice"] == "No test in the last week."


@respx.mock
async def test_the_list_of_tests() -> None:
    _myspeed()
    data = await get_adapter("myspeed").fetch("tests", CONFIG, {}, _ctx())
    assert [row["status"] for row in data.items] == ["ok", "ok", "bad"]
    assert data.items[0]["title"] == "↓ 652.83  ↑ 205.91 Mbit/s" and data.items[0]["value"] == "16 ms"
    assert "Validation error" in data.items[2]["subtitle"]
