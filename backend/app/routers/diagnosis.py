"""Why is this broken (roadmap M6): one page per service and its runbook.

Administrators only: the page shows how everything hangs together, every
check's address.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from ..deps import AdminUser, DbSession, error
from ..services import diagnosis

router = APIRouter(prefix="/api/v1", tags=["diagnosis"])
logger = logging.getLogger("hexdeck.diagnosis")

class RunbookIn(BaseModel):
    ref: str = Field(min_length=3, max_length=200)
    text: str = Field(default="", max_length=diagnosis.RUNBOOK_TEXT)
    link: str = Field(default="", max_length=diagnosis.RUNBOOK_LINK)


def _missing() -> Exception:
    return error("not_found", "There is no such service, container or guest.", status.HTTP_404_NOT_FOUND)


@router.get("/diagnosis", summary="Why a service is in trouble: since when, what came before, what it hangs on")
def read(user: AdminUser, db: DbSession, ref: str = Query(min_length=3, max_length=200),
         at: float | None = Query(default=None, description="A moment of a past trouble, in epoch seconds")) -> dict:
    moment = datetime.fromtimestamp(at, UTC) if at is not None else None
    page = diagnosis.build(db, ref, moment)
    if page is None:
        raise _missing()
    return page


@router.put("/diagnosis/runbook", summary="Write what to do when this service breaks; empty removes it")
def write_runbook(body: RunbookIn, user: AdminUser, db: DbSession) -> dict:
    if diagnosis.subject(db, body.ref) is None:
        raise _missing()
    try:
        kept = diagnosis.write_runbook(db, body.ref, body.text, body.link, user)
    except ValueError:
        raise error("bad_link", "A link starts with http:// or https://.") from None
    logger.info("%s %s the runbook of %s.", user.username, "wrote" if kept["text"] or kept["link"] else "removed", body.ref)
    return kept
