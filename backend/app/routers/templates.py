"""Board templates: list them, and make a board from one."""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from sqlalchemy import select

from ..deps import CurrentUser, DbSession, MemberUser, error
from ..models import Page, Widget
from ..schemas import TemplateInstall
from ..services import templates as template_service
from ..services.boards import board_view
from ..services.collector import collector
from ..services.templates import TemplateError

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])
logger = logging.getLogger("hexdeck.templates")


@router.get("", summary="The ready-made boards")
def list_templates(user: CurrentUser) -> list[dict]:
    return template_service.list_templates()


@router.get("/{template_id}", summary="One template, with the connections it could use here")
def get_template(template_id: str, user: CurrentUser, db: DbSession) -> dict:
    try:
        document = template_service.get_template(template_id)
    except TemplateError as failure:
        raise error("not_found", str(failure), status.HTTP_404_NOT_FOUND) from failure
    summary = template_service.summary(document)
    kinds = {entry["kind"] for entry in summary["integrations"]}
    summary["available"] = template_service.integrations_for(db, kinds, user)
    return summary


@router.post("/{template_id}", status_code=status.HTTP_201_CREATED, summary="Make a board from a template")
def install_template(template_id: str, body: TemplateInstall, user: MemberUser, db: DbSession) -> dict:
    try:
        board = template_service.install(db, template_id, user=user, name=body.name, connections=body.connections)
    except TemplateError as failure:
        raise error("bad_template", str(failure)) from failure
    db.commit()
    cards = list(db.scalars(select(Widget.id).join(Page).where(Page.board_id == board.id)))
    for widget_id in cards:
        collector.schedule(widget_id)
    logger.info("Board %r made from template %r by %s with %d card(s).", board.name, template_id, user.username, len(cards))
    return board_view(db, board, "owner")
