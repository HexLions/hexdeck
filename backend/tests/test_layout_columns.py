"""Columns are a property of the board; sizes from adapters are in twelfths.

A board without the key is a 12-column board, which is what every board
arranged before HexDeck was, so nothing on it moves.
"""

from __future__ import annotations

from app.services.layout import columns_of, normalise_settings, scale_layout, scale_size


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
