"""Readeck: the counts of the reading list, and the latest bookmarks.

``readeck_bookmarks.json`` is ``/api/bookmarks?sort=-created`` as Readeck
0.23.4 answered it locally on 2026-10-10: four bookmarks, one read, one
archived and marked, two that failed to load. The counts below are the
``Total-Count`` headers it gave for each filter the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://readeck.local:8000"
CONFIG = {"url": BASE, "token": "made-up-token"}
BOOKMARKS = json.loads((Path(__file__).parent / "fixtures" / "readeck_bookmarks.json").read_text())
#: The Total-Count that Readeck answered for each set of filters.
MEASURED = {"read_status=unread&is_archived=false": 2, "": 4, "is_archived=true": 1, "is_marked=true": 1, "has_errors=true": 2}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _answer(request: httpx.Request) -> httpx.Response:
    params = [(key, value) for key, value in request.url.params.multi_items() if key not in ("limit", "sort")]
    key = "&".join(f"{name}={value}" for name, value in params)
    if key in MEASURED:
        return httpx.Response(200, json=BOOKMARKS[:1], headers={"Total-Count": str(MEASURED[key])})
    return httpx.Response(200, json=BOOKMARKS, headers={"Total-Count": str(len(BOOKMARKS))})


@respx.mock
async def test_the_token_goes_in_as_bearer() -> None:
    respx.get(f"{BASE}/api/bookmarks").mock(side_effect=_answer)
    assert await get_adapter("readeck").test(CONFIG, _ctx()) == "Readeck answers, with 4 bookmarks."
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-token"


@respx.mock
async def test_a_wrong_token() -> None:
    respx.get(f"{BASE}/api/bookmarks").mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(AuthFailed):
        await get_adapter("readeck").test(CONFIG, _ctx())


@respx.mock
async def test_the_overview_counts_from_the_headers() -> None:
    respx.get(f"{BASE}/api/bookmarks").mock(side_effect=_answer)
    data = await get_adapter("readeck").fetch("overview", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Unread", "value": 2, "metric": "unread"}
    assert data.secondary == [{"label": "Bookmarks", "value": 4}, {"label": "Archived", "value": 1},
                              {"label": "Marked", "value": 1}, {"label": "Failed to save", "value": 2}]
    assert data.status == "warn"


@respx.mock
async def test_still_to_read_asks_unread_and_started_not_archived() -> None:
    route = respx.get(f"{BASE}/api/bookmarks").mock(return_value=httpx.Response(200, json=BOOKMARKS))
    data = await get_adapter("readeck").fetch("bookmarks", CONFIG, {}, _ctx())
    params = route.calls[0].request.url.params
    assert params.get_list("read_status") == ["unread", "reading"] and params["is_archived"] == "false"
    assert params["sort"] == "-created"
    titles = {row["title"]: row for row in data.items}
    assert titles["The Best Code is No Code At All"]["subtitle"] == "Coding Horror · 3 min"
    assert titles["Example Domain"]["value"] == "★"
    failed = titles["https://httpbin.org/status/404"]
    assert failed["status"] == "warn" and failed["url"] == f"{BASE}/bookmarks/{failed['id']}"


@respx.mock
async def test_the_marked_ones() -> None:
    route = respx.get(f"{BASE}/api/bookmarks").mock(return_value=httpx.Response(200, json=[]))
    data = await get_adapter("readeck").fetch("bookmarks", CONFIG, {"show": "marked"}, _ctx())
    assert route.calls[0].request.url.params["is_marked"] == "true"
    assert data.meta["empty"] == "No marked bookmark"
