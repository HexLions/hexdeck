"""A notepad everyone who may see the board writes in, when the card says so."""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import CSRF, create_user, login, setup_admin
from .test_boards import _board, _widget


def _share(client: TestClient, slug: str, level: str = "view") -> None:
    shared = client.put(f"/api/v1/boards/{slug}/shares", json={"shares": [{"user_id": None, "role": "guest", "level": level}]}, headers=CSRF)
    assert shared.status_code in (200, 204), shared.text


def _guest(client: TestClient, name: str = "kim") -> TestClient:
    create_user(client, name, role="guest")
    guest = TestClient(client.app)
    login(guest, name, "another-long-password")
    return guest


def test_a_viewer_writes_in_a_notepad_that_is_open_to_everyone(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    pad = _widget(client, board["pages"][0]["id"], "notepad.pad", options={"content": "Rack notes", "open": True})
    _share(client, board["slug"])
    guest = _guest(client)
    written = guest.post(f"/api/v1/widgets/{pad['id']}/notepad", json={"content": "Rack notes\n- UPS battery"}, headers=CSRF)
    assert written.status_code == 200, written.text
    assert written.json()["content"] == "Rack notes\n- UPS battery"
    seen = guest.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["widgets"][0]
    assert seen["options"]["content"] == "Rack notes\n- UPS battery"


def test_a_notepad_that_is_not_open_stays_the_editors_own(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    pad = _widget(client, board["pages"][0]["id"], "notepad.pad", options={"content": "Mine"})
    _share(client, board["slug"])
    guest = _guest(client)
    assert guest.post(f"/api/v1/widgets/{pad['id']}/notepad", json={"content": "Not mine"}, headers=CSRF).status_code == 403
    # The administrator writes through the same address, open or not.
    assert client.post(f"/api/v1/widgets/{pad['id']}/notepad", json={"content": "Still mine"}, headers=CSRF).status_code == 200
    assert client.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["widgets"][0]["options"]["content"] == "Still mine"


def test_only_a_notepad_takes_this_address_and_only_its_text(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    clock = _widget(client, board["pages"][0]["id"], "core.clock", options={"seconds": False})
    assert client.post(f"/api/v1/widgets/{clock['id']}/notepad", json={"content": "x"}, headers=CSRF).status_code == 400
    pad = _widget(client, board["pages"][0]["id"], "notepad.pad", options={"content": "Notes", "mono": True, "open": True})
    _share(client, board["slug"])
    guest = _guest(client)
    guest.post(f"/api/v1/widgets/{pad['id']}/notepad", json={"content": "Changed"}, headers=CSRF)
    options = client.get(f"/api/v1/boards/{board['slug']}").json()["pages"][0]["widgets"][1]["options"]
    assert options == {"content": "Changed", "mono": True, "open": True}, "the text, and nothing else"


def test_a_board_nobody_shared_is_not_written_in(client: TestClient) -> None:
    setup_admin(client)
    board = _board(client)
    pad = _widget(client, board["pages"][0]["id"], "notepad.pad", options={"open": True})
    guest = _guest(client)
    assert guest.post(f"/api/v1/widgets/{pad['id']}/notepad", json={"content": "x"}, headers=CSRF).status_code == 403
