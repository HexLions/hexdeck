"""Memos: the latest notes, the open tasks, and a token the profile does not check.

fixtures/memos_list.json is the answer of /api/v1/memos from Memos v0.31.0
running locally on 2026-10-09: three memos, one of them with a task list that
has one task open, one with two tags.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.memos import _tag_filter

FIXTURES = Path(__file__).parent / "fixtures"
LIST = json.loads((FIXTURES / "memos_list.json").read_text())
API = "http://memos:5230/api/v1"
CONFIG = {"url": "http://memos:5230", "token": "memos_pat_made_up"}
PROFILE = {"version": "0.31.0", "demo": False, "needsSetup": False, "accessMode": "INSTANCE_ACCESS_MODE_PRIVATE"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _memos() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("filter") == "has_incomplete_tasks":
            open_ = [memo for memo in LIST["memos"] if memo["property"]["hasIncompleteTasks"]]
            return httpx.Response(200, json={"memos": open_, "nextPageToken": ""})
        return httpx.Response(200, json=LIST)

    respx.get(f"{API}/memos").mock(side_effect=answer)
    respx.get(f"{API}/instance/profile").mock(return_value=httpx.Response(200, json=PROFILE))


@respx.mock
async def test_the_token_goes_in_as_bearer() -> None:
    _memos()
    await get_adapter("memos").fetch("memos", CONFIG, {}, _ctx())
    assert respx.calls[0].request.headers["authorization"] == "Bearer memos_pat_made_up"


@respx.mock
async def test_the_latest_memos_ask_for_pinned_first_and_show_their_tags() -> None:
    _memos()
    data = await get_adapter("memos").fetch("memos", CONFIG, {}, _ctx())
    assert respx.calls[0].request.url.params["orderBy"] == "pinned desc, create_time desc"
    tagged = next(row for row in data.items if "#homelab" in row["subtitle"])
    assert tagged["subtitle"].startswith("#homelab #memos")
    # A memo with a heading is called by it rather than by its first line.
    assert "Rack" in [row["title"] for row in data.items]


@respx.mock
async def test_a_tag_becomes_a_cel_term_with_or_without_its_hash() -> None:
    _memos()
    await get_adapter("memos").fetch("memos", CONFIG, {"tag": "#homelab"}, _ctx())
    assert respx.calls[0].request.url.params["filter"] == '"homelab" in tags'


def test_a_quote_in_a_tag_cannot_end_the_filter_string() -> None:
    assert _tag_filter('a" || true || "b') == '"a || true || b" in tags'
    assert _tag_filter("  ") == ""


@respx.mock
async def test_open_tasks_are_asked_for_by_the_filter_field_not_the_answer_field() -> None:
    """⚠️ has_incomplete_tasks in the filter; property.hasIncompleteTasks is refused there."""
    _memos()
    data = await get_adapter("memos").fetch("tasks", CONFIG, {}, _ctx())
    assert respx.calls[0].request.url.params["filter"] == "has_incomplete_tasks"
    assert [(row["title"], row["value"]) for row in data.items] == [("Rack", "1 open")]
    assert data.primary == {"label": "Open", "value": 1} and data.metrics == {"open": 1.0}


@respx.mock
async def test_the_connection_test_asks_for_a_memo_because_the_profile_answers_anyone() -> None:
    respx.get(f"{API}/instance/profile").mock(return_value=httpx.Response(200, json=PROFILE))
    respx.get(f"{API}/memos").mock(return_value=httpx.Response(401, json={"code": 16, "message": "authentication required"}))
    with pytest.raises(AuthFailed):
        await get_adapter("memos").test(CONFIG, _ctx())


@respx.mock
async def test_the_connection_test_names_the_version() -> None:
    _memos()
    assert await get_adapter("memos").test(CONFIG, _ctx()) == "Memos 0.31.0 answers."


@respx.mock
async def test_a_refused_filter_says_why() -> None:
    respx.get(f"{API}/memos").mock(return_value=httpx.Response(400, json={"code": 3, "message": "invalid filter"}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("memos").fetch("memos", CONFIG, {"tag": "x"}, _ctx())
    assert "invalid filter" in failure.value.message


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("memos")
    for widget in adapter.widgets:
        assert adapter.demo(widget.kind, {}, 100).items, widget.kind
