"""Cards are copied: duplicated on their page, or onto a page of another board."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import CSRF, create_user, login, setup_admin
from .test_boards import _board, _widget
from .test_locked_connections import _locked


def _cards(client: TestClient, slug: str) -> list[dict]:
    return client.get(f"/api/v1/boards/{slug}").json()["pages"][0]["widgets"]


def test_a_card_is_duplicated_with_its_settings_and_size(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    page = board["pages"][0]["id"]
    note = _widget(client, page, "notepad.pad", w=6, h=3)
    client.patch(f"/api/v1/widgets/{note['id']}", json={"title": "Shopping", "options": {"content": "milk"}}, headers=CSRF)
    copied = client.post("/api/v1/widgets/copy", json={"ids": [note["id"]], "page_id": page}, headers=CSRF)
    assert copied.status_code == 201, copied.text
    assert copied.json()["copied"] == 1
    [new_id] = copied.json()["ids"]
    cards = {card["id"]: card for card in _cards(client, board["slug"])}
    assert set(cards) == {note["id"], new_id}, "the original stays"
    assert cards[new_id]["title"] == "Shopping" and cards[new_id]["options"]["content"] == "milk"
    lg = client.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["layouts"]["lg"]
    sizes = {item["i"]: (item["w"], item["h"]) for item in lg}
    assert sizes[str(new_id)] == sizes[str(note["id"])]
    client.patch(f"/api/v1/widgets/{new_id}", json={"options": {"content": "eggs"}}, headers=CSRF)
    assert {card["id"]: card for card in _cards(client, board["slug"])}[note["id"]]["options"]["content"] == "milk", "two cards, not one"


def test_a_card_is_copied_onto_another_board(client: TestClient) -> None:
    setup_admin(client)
    here, there = _board(client, "Here"), _board(client, "There")
    card = _widget(client, here["pages"][0]["id"], "core.clock")
    copied = client.post("/api/v1/widgets/copy", json={"ids": [card["id"]], "page_id": there["pages"][0]["id"]}, headers=CSRF)
    assert copied.status_code == 201, copied.text
    assert len(_cards(client, here["slug"])) == 1 and len(_cards(client, there["slug"])) == 1


def test_a_copy_needs_edit_on_both_ends(client: TestClient) -> None:
    setup_admin(client)
    mine = _board(client, "Mine")
    card = _widget(client, mine["pages"][0]["id"], "core.clock")
    assert client.post("/api/v1/widgets/copy", json={"ids": [card["id"]], "page_id": 9999}, headers=CSRF).status_code == 404
    create_user(client, "kim")
    kim = TestClient(client.app)
    login(kim, "kim", "another-long-password")
    theirs = _board(kim, "Theirs")
    answer = kim.post("/api/v1/widgets/copy", json={"ids": [card["id"]], "page_id": theirs["pages"][0]["id"]}, headers=CSRF)
    assert answer.status_code in (403, 404), "kim may not read a card of a board kim cannot edit"
    assert _cards(kim, theirs["slug"]) == []


def test_a_locked_connection_does_not_travel_by_copy(client: TestClient) -> None:
    """An administrator's card on a locked engine, copied by an editor of that board."""
    setup_admin(client)
    locked = _locked(client)
    board = _board(client, "Ops")
    made = client.post(f"/api/v1/pages/{board['pages'][0]['id']}/widgets", json={
        "kind": "docker.containers", "title": "Containers", "integration_id": locked["id"], "options": {},
    }, headers=CSRF)
    assert made.status_code == 201, made.text
    card_id = made.json().get("id") or made.json()["widget"]["id"]
    sam = create_user(client, "sam")
    assert client.put(f"/api/v1/boards/{board['slug']}/shares", json={"shares": [{"user_id": sam["id"], "level": "edit"}]}, headers=CSRF).status_code == 200
    editor = TestClient(client.app)
    login(editor, "sam", "another-long-password")
    answer = editor.post("/api/v1/widgets/copy", json={"ids": [card_id], "page_id": board["pages"][0]["id"]}, headers=CSRF)
    assert answer.status_code == 403, answer.text
    assert len(_cards(client, board["slug"])) == 1
