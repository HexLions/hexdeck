"""A notepad: a card you write in, on the board itself.

Different from the Notes card of the basics, which is Markdown edited in
the card's settings. This one is plain text, typed straight into the card
by anyone who may edit the board, and kept in the card's options like any
other setting.
"""

from __future__ import annotations

from typing import Any

from .base import Adapter, Context, Field, WidgetData, WidgetType


class NotepadAdapter(Adapter):
    kind = "notepad"
    label = "Notepad"
    category = "basics"
    description = "A card to type into, right on the board. Plain text, saved as you write."
    icon = "lucide:notebook-pen"
    beta = False
    needs_integration = False
    widgets = (
        WidgetType(
            kind="pad",
            label="Notepad",
            description="Write on the board. Whoever may edit the board may write, and everyone who may see it when the card says so.",
            renderer="notepad",
            default_size=(3, 3),
            min_size=(2, 2),
            refresh_seconds=3600,
            options=(
                Field("content", "Text", type="textarea", default="", help="Typed into the card itself; this is the same text."),
                Field("mono", "Fixed-width type", type="bool", default=False, help="For lists, commands and anything that lines up."),
                Field("open", "Anyone who may see the board may write", type="bool", default=False,
                      help="For a shared list at home: guests and viewers type in this card, and change nothing else on the board."),
            ),
        ),
    )

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        return "The notepad lives on the board."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        return WidgetData(meta={"content": str(options.get("content") or ""), "mono": bool(options.get("mono")), "open": bool(options.get("open"))})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        return WidgetData(meta={"content": "Rack notes\n\n- UPS battery replaced 2026-08\n- Switch firmware due\n- Ask about the second NAS", "mono": False, "demo": True})


ADAPTER = NotepadAdapter()
