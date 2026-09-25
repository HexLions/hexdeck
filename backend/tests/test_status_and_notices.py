"""Two cards that read what HexDeck itself already knows: the reachability of
everything on a board, and the notices it has sent.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.adapters import get_adapter
from app.adapters.base import Context
from app.db import db_session
from app.models import HealthCheck, Notice

from .conftest import setup_admin
from .test_boards import _board, _widget


@pytest.fixture
def ctx_for():
    import httpx

    def make(widget_id: int) -> Context:
        return Context(httpx.AsyncClient(), integration_id=None, widget_id=widget_id, cache={})

    return make


def _check(widget_id: int, *, ok: bool | None, latency: int = 12, error: str = "", target: str = "https://example.com") -> None:
    from app.services import history

    with db_session() as db:
        check = db.query(HealthCheck).filter_by(widget_id=widget_id).one_or_none()
        if check is None:
            check = HealthCheck(widget_id=widget_id, target=target)
            db.add(check)
        check.enabled = True
        check.last_ok = ok
        check.last_latency_ms = latency
        check.last_error = error
        db.commit()
        if ok is not None:
            now = int(time.time())
            for minute in range(6):
                history.record(db, widget_id, {"up": 1.0 if ok else 0.0}, ts=now - minute * 60)
            db.commit()


async def test_the_status_card_lists_every_checked_card_of_the_board(client: TestClient, ctx_for) -> None:
    setup_admin(client)
    board = _board(client)
    page = board["pages"][0]["id"]
    up = _widget(client, page, "core.app", title="Radarr", link="https://radarr.lan")
    down = _widget(client, page, "core.app", title="Sonarr", link="https://sonarr.lan")
    status = _widget(client, page, "core.status")
    _check(up["id"], ok=True, latency=18)
    _check(down["id"], ok=False, latency=0, error="HTTP 502")
    data = await get_adapter("core").fetch("status", {}, {}, ctx_for(status["id"]))
    titles = [(item["title"], item["status"]) for item in data.items]
    assert titles == [("Sonarr", "bad"), ("Radarr", "ok")], "what is down comes first"
    assert data.status == "bad"
    assert data.primary == {"label": "Up", "value": "1 / 2"}
    radarr = next(item for item in data.items if item["title"] == "Radarr")
    assert radarr["subtitle"] == "18 ms" and radarr["uptime"] == 100.0
    assert len(radarr["bars"]) > 10, "the availability row comes along"
    assert next(item for item in data.items if item["title"] == "Sonarr")["subtitle"] == "HTTP 502"


async def test_the_status_card_can_look_at_every_board(client: TestClient, ctx_for) -> None:
    setup_admin(client)
    here = _board(client, "Here")
    there = _board(client, "There")
    mine = _widget(client, here["pages"][0]["id"], "core.app", title="Mine", link="https://mine.lan")
    theirs = _widget(client, there["pages"][0]["id"], "core.app", title="Theirs", link="https://theirs.lan")
    status = _widget(client, here["pages"][0]["id"], "core.status")
    _check(mine["id"], ok=True)
    _check(theirs["id"], ok=True)
    on_this_board = await get_adapter("core").fetch("status", {}, {}, ctx_for(status["id"]))
    assert [item["title"] for item in on_this_board.items] == ["Mine"]
    everywhere = await get_adapter("core").fetch("status", {}, {"scope": "all"}, ctx_for(status["id"]))
    assert sorted(item["title"] for item in everywhere.items) == ["Mine", "Theirs"]


async def test_the_status_card_says_so_when_nothing_is_checked(client: TestClient, ctx_for) -> None:
    setup_admin(client)
    board = _board(client)
    status = _widget(client, board["pages"][0]["id"], "core.status")
    data = await get_adapter("core").fetch("status", {}, {}, ctx_for(status["id"]))
    assert data.items == [] and data.status == "warn"
    assert "check" in str(data.meta.get("empty", "")).lower()


async def test_the_notices_card_shows_the_newest_first(client: TestClient, ctx_for) -> None:
    setup_admin(client)
    board = _board(client)
    card = _widget(client, board["pages"][0]["id"], "core.notices")
    with db_session() as db:
        db.add_all([
            Notice(event="outage", title="Radarr is down", body="No answer", level="error", link="/b/lab"),
            Notice(event="maintenance_due", title="Test the backup restore", body="Homelab · due today", level="warning"),
        ])
        db.commit()
    data = await get_adapter("core").fetch("notices", {}, {}, ctx_for(card["id"]))
    assert [item["title"] for item in data.items] == ["Test the backup restore", "Radarr is down"], "newest first"
    assert data.items[1]["status"] == "bad" and data.items[0]["status"] == "warn"
    assert data.items[1]["url"] == "/b/lab"
    assert data.status == "bad", "an unread error colours the card"
    filtered = await get_adapter("core").fetch("notices", {}, {"level": "error"}, ctx_for(card["id"]))
    assert [item["title"] for item in filtered.items] == ["Radarr is down"]


async def test_the_notices_card_counts_what_is_unread(client: TestClient, ctx_for) -> None:
    setup_admin(client)
    board = _board(client)
    card = _widget(client, board["pages"][0]["id"], "core.notices")
    with db_session() as db:
        db.add_all([Notice(event="x", title="One", level="info"), Notice(event="x", title="Two", level="info", read_at=None)])
        db.commit()
    data = await get_adapter("core").fetch("notices", {}, {}, ctx_for(card["id"]))
    assert data.primary == {"label": "Unread", "value": 2}


def test_both_cards_draw_in_the_demo() -> None:
    core = get_adapter("core")
    for kind in ("status", "notices"):
        demo = core.demo(kind, {}, 0)
        assert demo.items, kind


async def test_the_status_card_needs_no_board_to_survive(ctx_for) -> None:
    """A card whose widget is gone must not take the collector down."""
    data = await get_adapter("core").fetch("status", {}, {}, ctx_for(999999))
    assert data.items == []
