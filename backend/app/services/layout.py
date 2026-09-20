"""The grid a board is arranged on.

The number of columns is a setting of the board, not of the program. A board
without the setting has twelve, which is what every board arranged before
HexDeck was drawn on; new boards get twenty-four, half a column being the
difference between a card that fits and one that does not on a wide screen.

⚠️ Adapters declare their sizes in twelfths and are not touched: the 140 of
them are upstream's, and every one changed is a merge conflict. The scaling
happens here, at the two places a size meets a layout.
"""

from __future__ import annotations

from typing import Any

ALLOWED_COLUMNS: frozenset[int] = frozenset({12, 24, 36})
DEFAULT_COLUMNS = 12
#: What a new board is created with.
NEW_BOARD_COLUMNS = 24
MAX_WIDTH_PRESETS = ("1480", "full")
CUSTOM_WIDTH_RANGE = (320, 10000)


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def columns_of(settings: dict[str, Any] | None) -> int:
    """The columns of a board, from its settings; twelve when it does not say."""
    value = (settings or {}).get("columns")
    return value if _whole(value) and value in ALLOWED_COLUMNS else DEFAULT_COLUMNS


def scale_size(size: tuple[int, int], columns: int) -> tuple[int, int]:
    """An adapter's size, declared in twelfths, on a board of ``columns``."""
    return size[0] * (columns // DEFAULT_COLUMNS), size[1]


def scale_layout(items: list[dict[str, Any]], from_cols: int, to_cols: int) -> list[dict[str, Any]]:
    """The same arrangement on a grid of ``to_cols``.

    Growing is exact. Shrinking rounds, keeps every card at least one column
    wide and pulls one that would hang over the right edge back in.
    """
    factor = to_cols / from_cols
    scaled: list[dict[str, Any]] = []
    for item in items:
        w = max(1, round(item["w"] * factor))
        x = min(max(0, round(item["x"] * factor)), to_cols - w)
        new = {**item, "x": x, "w": w}
        if "minW" in item:
            new["minW"] = max(1, round(item["minW"] * factor))
        scaled.append(new)
    return scaled


def normalise_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """The settings with the three layout keys valid or gone; other keys as they came."""
    result = dict(settings or {})
    if "columns" in result and columns_of(result) != result["columns"]:
        del result["columns"]
    if "max_width" in result:
        width = result["max_width"]
        if not (width in MAX_WIDTH_PRESETS or (_whole(width) and CUSTOM_WIDTH_RANGE[0] <= width <= CUSTOM_WIDTH_RANGE[1])):
            del result["max_width"]
    if "fit_screen" in result:
        if isinstance(result["fit_screen"], bool | int):
            result["fit_screen"] = bool(result["fit_screen"])
        else:
            del result["fit_screen"]
    return result
