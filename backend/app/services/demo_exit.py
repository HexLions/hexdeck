"""Leaving demo mode, with the invented data taken along.

The demo makes connections marked ``demo`` and a board of cards that read
them. Switching the flag off alone leaves all of that behind, still
inventing numbers: every demo connection keeps doing so on its own. So
leaving demo mode removes the demo connections, every card that read one,
and every board that had nothing else to say once those cards were gone.
A board with a real connection on it keeps everything but its demo cards.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Board, Integration, Page, Setting, Widget
from ..services import history
from ..services.boards import remove_from_layouts
from ..services.collector import collector, set_demo_flag

logger = logging.getLogger("hexdeck.demo")


def leave(db: Session) -> dict[str, int]:
    demo_ids = set(db.scalars(select(Integration.id).where(Integration.demo.is_(True))))
    boards_removed = widgets_removed = 0
    gone_widgets: list[int] = []
    if demo_ids:
        for board in list(db.scalars(select(Board))):
            widgets = [w for page in board.pages for w in page.widgets]
            demo_widgets = [w for w in widgets if w.integration_id in demo_ids]
            if not demo_widgets:
                continue
            real = any(w.integration_id is not None and w.integration_id not in demo_ids for w in widgets)
            if real:
                # Somebody has built on this board: only the demo cards go.
                for widget in demo_widgets:
                    page = db.get(Page, widget.page_id)
                    if page is not None:
                        remove_from_layouts(page, widget.id)
                    gone_widgets.append(widget.id)
                    db.delete(widget)
                widgets_removed += len(demo_widgets)
            else:
                gone_widgets.extend(w.id for w in widgets)
                widgets_removed += len(widgets)
                boards_removed += 1
                db.delete(board)
        for integration in list(db.scalars(select(Integration).where(Integration.id.in_(demo_ids)))):
            db.delete(integration)
    row = db.get(Setting, "general")
    general = dict(row.value) if row is not None else {}
    general["demo"] = False
    db.merge(Setting(key="general", value=general))
    db.flush()
    for widget_id in gone_widgets:
        history.forget_widget(db, widget_id)
    db.commit()
    set_demo_flag(False)
    for widget_id in gone_widgets:
        collector.unschedule(widget_id)
    for integration_id in demo_ids:
        collector.forget_integration(integration_id)
    for widget_id in list(db.scalars(select(Widget.id))):
        collector.schedule(widget_id)
    summary = {"integrations_removed": len(demo_ids), "widgets_removed": widgets_removed, "boards_removed": boards_removed}
    logger.info("Demo mode left: %d connection(s), %d card(s) and %d board(s) removed.", *summary.values())
    return summary
