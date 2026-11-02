"""The daily report (roadmap M2): a summary of the last 24 hours, on a card and once a day on a channel."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.adapters import get_adapter
from app.adapters.base import Context, WidgetData
from app.db import db_session
from app.models import Notice, User, utcnow
from app.services import chronicle, report
from app.services.collector import collector
from app.services.state import live

from .conftest import CSRF, setup_admin


def _widget(client: TestClient, page_id: int, kind: str = "core.clock", **extra: object) -> dict:
    response = client.post(f"/api/v1/pages/{page_id}/widgets", json={"kind": kind, **extra}, headers=CSRF)
    assert response.status_code == 201, response.text
    widget = response.json()["widget"]
    collector.unschedule(widget["id"])
    return widget


@pytest.fixture
def day(client: TestClient) -> dict:
    """A board with a card that is red now, and a day of news."""
    setup_admin(client)
    board = client.post("/api/v1/boards", json={"name": "Lab"}, headers=CSRF).json()
    nas = _widget(client, board["pages"][0]["id"], title="Synology")
    live.set(nas["id"], WidgetData(status="ok"))
    live.set(nas["id"], WidgetData(status="bad", error="Refused"))
    with db_session() as db:
        admin = db.query(User).filter(User.username == "admin").one()
        chronicle.record("outage", level="bad", widget_id=nas["id"], name="Synology", db=db)
        chronicle.record("card", level="bad", widget_id=nas["id"], name="Synology", before="ok", after="bad", db=db)
        chronicle.record("version", integration_id=None, board_id=board["id"], name="Sonarr", before="4.0.15", after="4.0.16", db=db)
        chronicle.record("outage", level="bad", widget_id=nas["id"], name="Old news", at=utcnow() - timedelta(days=2), db=db)
        db.add(Notice(user_id=admin.id, event="backup_stale", level="warn", title="Photos: the last good backup is too old"))
        admin_id = admin.id
    return {"board": board, "nas": nas, "admin": admin_id}


def test_the_summary_holds_the_last_day_and_what_is_red_now(day: dict) -> None:
    with db_session() as db:
        found = report.summary(db, boards={day["board"]["id"]}, owner_id=day["admin"])
    assert found["outages"] == ["Synology"], "two days ago is not the last day"
    assert found["red"] == ["Synology"] and found["now_red"] == ["Synology"]
    assert found["versions"] == [{"name": "Sonarr", "before": "4.0.15", "after": "4.0.16"}]
    assert found["backups"] == ["Photos: the last good backup is too old"]


def test_the_message_says_the_most_in_its_title() -> None:
    quiet = {key: [] for key in ("outages", "red", "now_red", "versions", "containers", "restarted", "backups")}
    assert report.words(quiet) == ("Daily report: all quiet", "Nothing happened since yesterday, and nothing is red.")
    busy = {**quiet, "outages": ["Paperless"], "now_red": ["Paperless"], "versions": [{"name": "Sonarr", "before": "4.0.15", "after": "4.0.16"}]}
    title, body = report.words(busy)
    assert title == "Daily report: 1 outage(s), 1 still red, 1 update(s)"
    assert "Red now: Paperless" in body and "Sonarr 4.0.15 → 4.0.16" in body


async def test_the_report_card_reads_the_summary(client: TestClient, day: dict) -> None:
    card = _widget(client, day["board"]["pages"][0]["id"], kind="core.report")
    ctx = Context(httpx.AsyncClient(), widget_id=card["id"], cache={})
    data = await get_adapter("core").fetch("report", {}, {}, ctx)
    assert data.meta["report"]["now_red"] == ["Synology"]
    assert data.status == "ok", "the report tells; it does not turn the board red"

