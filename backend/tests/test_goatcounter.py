"""GoatCounter: visitors, pages and referrers.

``goatcounter_api.json`` holds what GoatCounter v2.7.0 answered locally on
2026-10-15 after 40 pageviews sent through ``/api/v0/count``: the totals of
the last seven days, the four pages and the three referrers plus direct
traffic.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "https://stats.example.com"
CONFIG = {"url": BASE, "token": "made-up-token"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "goatcounter_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _goatcounter() -> None:
    respx.get(f"{BASE}/api/v0/stats/total").mock(return_value=httpx.Response(200, json=ANSWERS["total"]))
    respx.get(f"{BASE}/api/v0/stats/hits").mock(return_value=httpx.Response(200, json=ANSWERS["hits"]))
    respx.get(f"{BASE}/api/v0/stats/toprefs").mock(return_value=httpx.Response(200, json=ANSWERS["toprefs"]))


@respx.mock
async def test_visitors_today_and_this_week() -> None:
    _goatcounter()
    data = await get_adapter("goatcounter").fetch("visitors", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Today", "value": 31, "metric": "today"}
    assert data.secondary == [{"label": "7 days", "value": 31}, {"label": "Yesterday", "value": 0}]
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-token"


@respx.mock
async def test_the_pages_read_most() -> None:
    _goatcounter()
    data = await get_adapter("goatcounter").fetch("pages", CONFIG, {"days": "1", "limit": 3}, _ctx())
    assert [(row["title"], row["subtitle"], row["value"]) for row in data.items] == [
        ("Home", "/", 10), ("VLAN plan", "/blog/vlan-plan", 10), ("Backups that restore", "/blog/backups-that-restore", 8)]
    query = respx.calls[0].request.url.params
    assert query["limit"] == "3" and query["start"].endswith("T00:00:00Z")


@respx.mock
async def test_the_referrers_with_direct_traffic_named() -> None:
    _goatcounter()
    data = await get_adapter("goatcounter").fetch("referrers", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in data.items] == [
        ("https://news.ycombinator.com", 11), ("https://lemmy.ml/post/1", 9), ("https://duckduckgo.com", 7), ("Direct", 4)]


@respx.mock
@pytest.mark.parametrize(("status", "error", "words"), [(401, AuthFailed, "does not know"), (403, AuthFailed, "may not read"),
                                                        (429, AdapterError, "slow down")])
async def test_refusals(status: int, error: type, words: str) -> None:
    respx.get(f"{BASE}/api/v0/stats/total").mock(return_value=httpx.Response(status, text="<html>Error</html>"))
    with pytest.raises(error, match=words):
        await get_adapter("goatcounter").test(CONFIG, _ctx())
