"""A list to tick off, kept on the server so it is the same list everywhere."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.adapters import get_adapter
from app.adapters.base import Context

from .conftest import CSRF, create_user, login, setup_admin
from .test_boards import _board, _widget


def _drawn(options: dict) -> dict:
    """What the card draws for these options, without waiting for the collector."""
    import asyncio

    ctx = Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={})
    return asyncio.run(get_adapter("core").fetch("todo", {}, options, ctx)).model_dump()


def _share(client: TestClient, slug: str) -> None:
    shared = client.put(f"/api/v1/boards/{slug}/shares", json={"shares": [{"user_id": None, "role": "guest", "level": "view"}]}, headers=CSRF)
    assert shared.status_code in (200, 204), shared.text


def test_items_are_added_ticked_and_cleared(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    card = _widget(client, board["pages"][0]["id"], "core.todo", options={"items": "Milk\nBread"})
    listed = _drawn(card["options"])
    assert [row["title"] for row in listed["items"]] == ["Milk", "Bread"]
    assert all(row["status"] == "unknown" for row in listed["items"])

    written = client.post(f"/api/v1/widgets/{card['id']}/todo", json={"items": [{"title": "Milk", "done": True}, {"title": "Bread", "done": False}, {"title": "Eggs", "done": False}]}, headers=CSRF)
    assert written.status_code == 200, written.text
    assert written.json()["left"] == 2
    stored = client.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["widgets"][0]["options"]
    again = _drawn(stored)
    assert [(row["title"], row["status"]) for row in again["items"]] == [("Milk", "ok"), ("Bread", "unknown"), ("Eggs", "unknown")]
    assert again["primary"] == {"label": "Left", "value": 2}
    assert client.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["widgets"][0]["options"]["items"] == "x Milk\nBread\nEggs"


def test_a_viewer_ticks_only_where_the_card_says_so(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    shut = _widget(client, board["pages"][0]["id"], "core.todo", options={"items": "Mine"})
    open_one = _widget(client, board["pages"][0]["id"], "core.todo", options={"items": "Ours", "open": True})
    _share(client, board["slug"])
    create_user(client, "kim", role="guest")
    guest = TestClient(client.app)
    login(guest, "kim", "another-long-password")
    assert guest.post(f"/api/v1/widgets/{shut['id']}/todo", json={"items": [{"title": "Mine", "done": True}]}, headers=CSRF).status_code == 403
    assert guest.post(f"/api/v1/widgets/{open_one['id']}/todo", json={"items": [{"title": "Ours", "done": True}]}, headers=CSRF).status_code == 200


def test_only_a_todo_card_takes_this_address_and_only_its_items(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    clock = _widget(client, board["pages"][0]["id"], "core.clock")
    assert client.post(f"/api/v1/widgets/{clock['id']}/todo", json={"items": []}, headers=CSRF).status_code == 400
    card = _widget(client, board["pages"][0]["id"], "core.todo", options={"items": "One", "hide_done": True, "open": True})
    client.post(f"/api/v1/widgets/{card['id']}/todo", json={"items": [{"title": "Two", "done": False}]}, headers=CSRF)
    options = client.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["widgets"][1]["options"]
    assert options == {"items": "Two", "hide_done": True, "open": True}, "the list, and nothing else"


def test_what_is_done_can_be_kept_out_of_sight(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    card = _widget(client, board["pages"][0]["id"], "core.todo", options={"items": "x Milk\nBread", "hide_done": True})
    data = _drawn(card["options"])
    assert [row["title"] for row in data["items"]] == ["Bread"]
    assert data["primary"] == {"label": "Left", "value": 1}
