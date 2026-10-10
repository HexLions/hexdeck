"""Ombi: requests by state, the latest requests, and open issues.

``ombi_recently_requested.json`` is ``/api/v2/Requests/recentlyRequested`` as
Ombi 4.53.10 answered it locally on 2026-10-10 (the overviews cut short): a
series and a film requested by a plain user, still pending, and two films
requested through the API key, one of them denied after it was approved. The
counts and the refusal of a wrong key were measured the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://ombi.local:5000"
CONFIG = {"url": BASE, "api_key": "made-up-key"}
RECENT = json.loads((Path(__file__).parent / "fixtures" / "ombi_recently_requested.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _ombi() -> None:
    respx.get(f"{BASE}/api/v1/Request/count").mock(return_value=httpx.Response(200, json={"pending": 2, "approved": 2, "available": 0, "denied": 1}))
    respx.get(f"{BASE}/api/v1/Issues/count").mock(return_value=httpx.Response(200, json={"pending": 1, "inProgress": 1, "resolved": 4}))
    respx.get(f"{BASE}/api/v2/Requests/recentlyRequested").mock(return_value=httpx.Response(200, json=RECENT))


@respx.mock
async def test_the_key_goes_in_an_apikey_header() -> None:
    _ombi()
    await get_adapter("ombi").test(CONFIG, _ctx())
    assert respx.calls[0].request.headers["apikey"] == "made-up-key"


@respx.mock
async def test_the_test_asks_the_issue_count_which_checks_the_key() -> None:
    respx.get(f"{BASE}/api/v1/Issues/count").mock(return_value=httpx.Response(401, text="Invalid API Key"))
    count = respx.get(f"{BASE}/api/v1/Request/count").mock(return_value=httpx.Response(200, json={"pending": 0}))
    with pytest.raises(AuthFailed):
        await get_adapter("ombi").test(CONFIG, _ctx())
    assert not count.called


@respx.mock
async def test_the_summary() -> None:
    _ombi()
    data = await get_adapter("ombi").fetch("summary", CONFIG, {}, _ctx())
    assert data.status == "warn"
    assert data.primary == {"label": "Pending", "value": 2, "metric": "pending"}
    assert {"label": "Open issues", "value": 2} in data.secondary
    assert {"label": "Denied", "value": 1} in data.secondary


@respx.mock
async def test_the_latest_requests_and_where_each_stands() -> None:
    _ombi()
    data = await get_adapter("ombi").fetch("requests", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in data.items] == [
        ("Breaking Bad", "pending"), ("Interstellar", "pending"), ("Fight Club", "denied"), ("The Matrix", "approved")]
    assert data.items[0]["subtitle"].startswith("series · sam")
    assert data.items[1]["subtitle"].startswith("film · sam")
    assert data.items[2]["status"] == "bad"


@respx.mock
async def test_only_the_pending_ones() -> None:
    _ombi()
    data = await get_adapter("ombi").fetch("requests", CONFIG, {"pending_only": True, "limit": 1}, _ctx())
    assert [row["title"] for row in data.items] == ["Breaking Bad"]


@respx.mock
async def test_a_wrong_address_gets_the_web_page() -> None:
    respx.get(f"{BASE}/api/v1/Issues/count").mock(return_value=httpx.Response(200, text="<!DOCTYPE html><html></html>"))
    with pytest.raises(AdapterError, match="JSON"):
        await get_adapter("ombi").test(CONFIG, _ctx())
