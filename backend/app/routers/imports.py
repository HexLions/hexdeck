"""Boards from other dashboards: a plan first, then the board."""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from sqlalchemy import select

from ..deps import AdminUser, DbSession, error
from ..models import Page, Widget
from ..schemas import ImportApply, ImportPreview
from ..services import imports
from ..services.boards import board_view
from ..services.collector import collector

router = APIRouter(prefix="/api/v1/imports", tags=["boards"])
logger = logging.getLogger("hexdeck.imports")


@router.post("/preview", summary="Read another dashboard's files and say what they would become")
def preview(body: ImportPreview, user: AdminUser) -> dict:
    """An administrator's job: the plan makes connections. Nothing is written."""
    try:
        return imports.plan_for(body.source, body.files)
    except imports.DashboardImportError as failure:
        raise error("bad_import", str(failure)) from failure


@router.post("/apply", status_code=status.HTTP_201_CREATED, summary="Make the connections and the board a plan describes")
def apply(body: ImportApply, user: AdminUser, db: DbSession) -> dict:
    try:
        board = imports.apply(db, body.plan, user=user)
    except imports.DashboardImportError as failure:
        raise error("bad_import", str(failure)) from failure
    db.commit()
    cards = list(db.scalars(select(Widget.id).join(Page).where(Page.board_id == board.id)))
    for widget_id in cards:
        collector.schedule(widget_id)
    logger.info("Board %r imported from %s by %s with %d card(s).", board.name, body.plan.get("source", "another dashboard"), user.username, len(cards))
    return board_view(db, board, "owner")
