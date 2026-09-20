"""Columns are a property of the board; sizes from adapters are in twelfths.

A board without the key is a 12-column board, which is what every board
arranged before HexDeck was, so nothing on it moves.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Page
from app.services.boards import place_widget
from app.services.layout import columns_of, normalise_settings, scale_layout, scale_size

from .conftest import CSRF, create_user, login, setup_admin


def test_a_board_without_the_key_has_twelve_columns() -> None:
    assert columns_of(None) == 12
    assert columns_of({}) == 12
    assert columns_of({"columns": "24"}) == 12
    assert columns_of({"columns": 13}) == 12
    assert columns_of({"columns": True}) == 12
    assert columns_of({"columns": 24}) == 24
    assert columns_of({"columns": 36}) == 36


def test_adapter_sizes_are_scaled_to_the_columns() -> None:
    assert scale_size((3, 2), 12) == (3, 2)
    assert scale_size((3, 2), 24) == (6, 2)
    assert scale_size((3, 2), 36) == (9, 2)


def test_growing_the_grid_is_exact() -> None:
    items = [{"i": "1", "x": 3, "y": 1, "w": 3, "h": 2, "minW": 2, "minH": 1}]
    assert scale_layout(items, 12, 24) == [{"i": "1", "x": 6, "y": 1, "w": 6, "h": 2, "minW": 4, "minH": 1}]
    assert items[0]["x"] == 3, "the input list is not touched"


def test_shrinking_rounds_and_keeps_every_card_on_the_grid() -> None:
    items = [{"i": "1", "x": 23, "y": 0, "w": 1, "h": 1}, {"i": "2", "x": 0, "y": 0, "w": 3, "h": 1}]
    shrunk = scale_layout(items, 24, 12)
    assert shrunk[0] == {"i": "1", "x": 11, "y": 0, "w": 1, "h": 1}
    assert shrunk[1] == {"i": "2", "x": 0, "y": 0, "w": 2, "h": 1}


def test_settings_are_normalised_and_the_rest_left_alone() -> None:
    raw = {"columns": 24, "max_width": "full", "fit_screen": 1, "compact": True}
    assert normalise_settings(raw) == {"columns": 24, "max_width": "full", "fit_screen": True, "compact": True}
    assert normalise_settings({"columns": 7, "max_width": "wide", "fit_screen": "yes"}) == {}
    assert normalise_settings({"max_width": 1920}) == {"max_width": 1920}
    assert normalise_settings({"max_width": 100}) == {}
    assert normalise_settings(None) == {}


def _page() -> Page:
    return Page(board_id=1, name="p", slug="p", position=0, layouts={"lg": [], "md": [], "sm": []})


def test_a_new_card_on_a_24_column_board_is_twice_as_wide() -> None:
    page = _page()
    place_widget(page, 7, (3, 2), (2, 1), columns=24)
    lg = page.layouts["lg"][0]
    assert (lg["x"], lg["w"], lg["h"], lg["minW"], lg["minH"]) == (0, 6, 2, 4, 1)
    # The phone stack is in its own four columns whatever the board has.
    assert page.layouts["sm"][0]["w"] == 2


def test_a_twelve_column_board_places_as_before() -> None:
    page = _page()
    place_widget(page, 7, (3, 2), (2, 1))
    assert page.layouts["lg"][0]["w"] == 3


def test_the_last_row_is_filled_in_the_board_columns() -> None:
    page = _page()
    place_widget(page, 1, (6, 2), (2, 1), columns=24)
    place_widget(page, 2, (6, 2), (2, 1), columns=24)
    one, two = page.layouts["lg"]
    assert (one["x"], one["w"], two["x"], two["y"]) == (0, 12, 12, 0)


def test_switching_a_board_to_24_columns_doubles_every_page(client: TestClient) -> None:
    setup_admin(client)
    board = client.post("/api/v1/boards", json={"name": "Old"}, headers=CSRF).json()
    page_id = board["pages"][0]["id"]
    # A board from before: no columns in its settings means twelve.
    assert client.patch(f"/api/v1/boards/{board['slug']}", json={"settings": {}}, headers=CSRF).status_code == 200
    widget = client.post(f"/api/v1/pages/{page_id}/widgets", json={"kind": "core.clock"}, headers=CSRF).json()["widget"]
    client.put(f"/api/v1/pages/{page_id}/layouts", json={"lg": [{"i": str(widget["id"]), "x": 3, "y": 0, "w": 3, "h": 2}]}, headers=CSRF)

    answer = client.put(f"/api/v1/boards/{board['slug']}/columns", json={"columns": 24}, headers=CSRF)
    assert answer.status_code == 200, answer.text
    lg = answer.json()["pages"][0]["layouts"]["lg"][0]
    assert (lg["x"], lg["w"], lg["h"]) == (6, 6, 2)
    after = client.get(f"/api/v1/boards/{board['slug']}").json()
    assert after["settings"]["columns"] == 24
    assert after["pages"][0]["layout_version"] == 2


def test_the_columns_have_to_be_one_of_the_three(client: TestClient) -> None:
    setup_admin(client)
    board = client.post("/api/v1/boards", json={"name": "Odd"}, headers=CSRF).json()
    assert client.put(f"/api/v1/boards/{board['slug']}/columns", json={"columns": 13}, headers=CSRF).status_code == 422


def test_the_generic_patch_cannot_change_the_columns(client: TestClient) -> None:
    """The layouts would be stranded in the old unit; only the endpoint that rescales them may."""
    setup_admin(client)
    board = client.post("/api/v1/boards", json={"name": "Stuck"}, headers=CSRF).json()
    client.patch(f"/api/v1/boards/{board['slug']}", json={"settings": {"columns": 12, "fit_screen": 1, "max_width": "wide"}}, headers=CSRF)
    settings = client.get(f"/api/v1/boards/{board['slug']}").json()["settings"]
    assert settings == {"columns": 24, "fit_screen": True}


def test_a_viewer_may_not_change_the_columns(client: TestClient) -> None:
    setup_admin(client)
    board = client.post("/api/v1/boards", json={"name": "Mine"}, headers=CSRF).json()
    create_user(client, "kim")
    other = TestClient(client.app)
    login(other, "kim", "another-long-password")
    assert other.put(f"/api/v1/boards/{board['slug']}/columns", json={"columns": 24}, headers=CSRF).status_code in (403, 404)
