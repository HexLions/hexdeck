"""Key figures: a card that holds number cards of its page and draws each as a tile."""

from __future__ import annotations

from app.adapters import get_adapter


def test_key_figures_are_a_holder_of_cards_that_draws_from_the_board() -> None:
    widget = get_adapter("core").widget("figures")
    assert widget.client_only
    assert widget.renderer == "figures"
    cards = next(field for field in widget.options if field.name == "cards")
    # The picker offers the number cards of the page only.
    assert (cards.type, cards.helper) == ("cards", "numbers")


def test_key_figures_pass_on_only_the_looks_they_have() -> None:
    meta = get_adapter("core").demo("figures", {"cards": [1, 2], "columns": "5", "tone": "loud", "history": 1}, 0).meta
    assert meta == {"holder": "figures", "holds": 2, "columns": "auto", "tone": "fill", "history": True}
    meta = get_adapter("core").demo("figures", {"columns": "4", "tone": "border"}, 0).meta
    assert (meta["columns"], meta["tone"], meta["history"]) == ("4", "border", False)
