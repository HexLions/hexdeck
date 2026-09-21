"""Cards move from one page to another, on the same board or another one."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import CSRF, create_user, login, setup_admin
from .test_boards import _board, _widget


def _page(client: TestClient, slug: str, name: str) -> dict:
    made = client.post(f"/api/v1/boards/{slug}/pages", json={"name": name}, headers=CSRF)
    assert made.status_code == 201, made.text
    return next(p for p in made.json()["pages"] if p["name"] == name)


def test_cards_move_to_another_page_and_take_their_size_along(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    first = board["pages"][0]["id"]
    second = _page(client, board["slug"], "Second")["id"]
    clock = _widget(client, first, "core.clock", w=6, h=3)
    note = _widget(client, first, "notepad.pad")
    stays = _widget(client, first, "core.problems")
    before = client.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["layouts"]["lg"]
    size_before = next((i["w"], i["h"]) for i in before if i["i"] == str(clock["id"]))
    moved = client.post("/api/v1/widgets/move", json={"ids": [clock["id"], note["id"]], "page_id": second}, headers=CSRF)
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] == 2
    view = client.get(f"/api/v1/boards/{board['slug']}").json()
    pages = {p["id"]: p for p in view["pages"]}
    assert [w["id"] for w in pages[first]["widgets"]] == [stays["id"]]
    assert {w["id"] for w in pages[second]["widgets"]} == {clock["id"], note["id"]}
    assert [i["i"] for i in pages[first]["layouts"]["lg"]] == [str(stays["id"])], "gone from the old page's layout"
    spot = next(i for i in pages[second]["layouts"]["lg"] if i["i"] == str(clock["id"]))
    assert (spot["w"], spot["h"]) == size_before, "the size comes along, the place is new"
    assert len(pages[second]["layouts"]["sm"]) == 2


def test_cards_move_to_a_page_of_another_board_the_owner_may_edit(client: TestClient) -> None:
    setup_admin(client)
    here = _board(client, "Here")
    there = _board(client, "There")
    card = _widget(client, here["pages"][0]["id"], "core.clock")
    moved = client.post("/api/v1/widgets/move", json={"ids": [card["id"]], "page_id": there["pages"][0]["id"]}, headers=CSRF)
    assert moved.status_code == 200, moved.text
    assert [w["id"] for w in client.get(f"/api/v1/boards/{there['slug']}").json()["pages"][0]["widgets"]] == [card["id"]]
    assert client.get(f"/api/v1/boards/{here['slug']}").json()["pages"][0]["widgets"] == []


def test_a_move_needs_edit_on_both_ends_and_a_real_page(client: TestClient) -> None:
    setup_admin(client)
    mine = _board(client, "Mine")
    card = _widget(client, mine["pages"][0]["id"], "core.clock")
    assert client.post("/api/v1/widgets/move", json={"ids": [card["id"]], "page_id": 9999}, headers=CSRF).status_code == 404
    assert client.post("/api/v1/widgets/move", json={"ids": [9999], "page_id": mine["pages"][0]["id"]}, headers=CSRF).status_code == 404
    create_user(client, "kim")
    viewer = TestClient(client.app)
    login(viewer, "kim", "another-long-password")
    theirs = _board(viewer, "Theirs")
    # Kim may not take a card off the administrator's board, nor put one onto it.
    assert viewer.post("/api/v1/widgets/move", json={"ids": [card["id"]], "page_id": theirs["pages"][0]["id"]}, headers=CSRF).status_code == 403
    own = _widget(viewer, theirs["pages"][0]["id"], "core.clock")
    assert viewer.post("/api/v1/widgets/move", json={"ids": [own["id"]], "page_id": mine["pages"][0]["id"]}, headers=CSRF).status_code == 403
