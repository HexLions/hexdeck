"""Planka, against Planka 2.2.1 run locally on 21.10.2026.

The fixture is what that server answered an API key for one project with one
board of three lists and four cards (overdue, due the next day, due in twenty
days, overdue but ticked done), plus the answer to the first sign-in of an
account that has not accepted the terms. One card was copied into the board's
trash list by hand, to see that it is left out.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

PL = "https://planka.local"
KEY = {"url": PL, "api_key": "test-api-key"}
LOGIN = {"url": PL, "username": "admin", "password": "planka-password"}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "planka_board.json").read_text())
#: When the fixture was captured.
NOW = datetime(2026, 10, 21, 14, 53, tzinfo=UTC)


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _server() -> respx.Route:
    route = respx.get(f"{PL}/api/projects").mock(return_value=httpx.Response(200, json=FIXTURE["projects"]))
    respx.get(url__regex=rf"{PL}/api/boards/\d+").mock(return_value=httpx.Response(200, json=FIXTURE["board"]))
    return route


async def _cards() -> tuple[list[dict], int]:
    with respx.mock:
        _server()
        return await get_adapter("planka")._cards(KEY, _ctx())


async def test_the_cards_of_the_active_lists_only() -> None:
    cards, boards = await _cards()
    assert boards == 1
    assert sorted(card["name"] for card in cards) == ["Clean the dust filters", "Move Plex to the new NAS", "Renew certificates", "Replace UPS battery"]
    assert {card["_board"] for card in cards} == {"Upgrades"}


async def test_the_summary() -> None:
    cards, boards = await _cards()
    data = get_adapter("planka")._summary(cards, boards, NOW)
    assert data.status == "bad" and data.primary == {"label": "Overdue", "value": 1}, "the one ticked done does not count"
    assert data.secondary == [{"label": "Due this week", "value": 1}, {"label": "Open cards", "value": 4}, {"label": "Boards", "value": 1}]


async def test_the_due_list() -> None:
    cards, _boards = await _cards()
    rows = get_adapter("planka")._due(cards, NOW, 10).items
    assert [(row["title"], row["value"], row["status"]) for row in rows] == [
        ("Replace UPS battery", "2 d overdue", "bad"), ("Renew certificates", "in 1 d", "warn"), ("Move Plex to the new NAS", "in 20 d", "ok")]
    assert rows[0]["subtitle"] == "Upgrades · To do"


@respx.mock
async def test_the_key_goes_in_its_header() -> None:
    route = _server()
    await get_adapter("planka").fetch("summary", KEY, {}, _ctx())
    assert route.calls[0].request.headers["x-api-key"] == "test-api-key"


@respx.mock
async def test_signing_in_and_again_after_a_401() -> None:
    login = respx.post(f"{PL}/api/access-tokens").mock(return_value=httpx.Response(200, json=FIXTURE["login_ok"]))
    respx.get(f"{PL}/api/projects").mock(side_effect=[httpx.Response(401, json=FIXTURE["unauthorized"]), httpx.Response(200, json=FIXTURE["projects"])])
    assert await get_adapter("planka").test(LOGIN, _ctx()) == "Planka answers with 1 board."
    assert login.call_count == 2
    assert json.loads(login.calls[0].request.content) == {"emailOrUsername": "admin", "password": "planka-password"}


@respx.mock
async def test_terms_not_yet_accepted() -> None:
    respx.post(f"{PL}/api/access-tokens").mock(return_value=httpx.Response(403, json=FIXTURE["login_terms"]))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("planka").fetch("summary", LOGIN, {}, _ctx())
    assert "terms" in failure.value.message and "API key" in failure.value.hint


@respx.mock
async def test_refusals_and_something_else() -> None:
    respx.post(f"{PL}/api/access-tokens").mock(return_value=httpx.Response(401, json=FIXTURE["unauthorized"]))
    with pytest.raises(AuthFailed):
        await get_adapter("planka").fetch("summary", LOGIN, {}, _ctx())
    respx.get(f"{PL}/api/projects").mock(side_effect=[httpx.Response(401, json=FIXTURE["unauthorized"]), httpx.Response(500),
                                                      httpx.Response(200, text="<html>"), httpx.Response(200, json={"items": []})])
    with pytest.raises(AuthFailed):
        await get_adapter("planka").fetch("summary", KEY, {}, _ctx())
    for code in ("http_error", "not_planka", "not_planka"):
        with pytest.raises(AdapterError) as failure:
            await get_adapter("planka").fetch("summary", KEY, {}, _ctx())
        assert failure.value.code == code
    with pytest.raises(AuthFailed):
        await get_adapter("planka").fetch("summary", {"url": PL}, {}, _ctx())


def test_the_demo() -> None:
    adapter = get_adapter("planka")
    for tick in range(6):
        assert adapter.demo("summary", {}, tick).primary["label"] == "Overdue"
        assert adapter.demo("due", {}, tick).items
