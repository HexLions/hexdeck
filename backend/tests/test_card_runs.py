"""System > Card status: how every card's last read went, for the administrator.

An unexpected failure used to be visible only in the server log: the card
says "the server log says why", because every viewer of the board sees it.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.services import collector as collector_module
from app.services.state import live

from .conftest import create_user, login, setup_admin


async def test_an_unexpected_failure_keeps_its_reason_until_the_card_reads_again(monkeypatch: pytest.MonkeyPatch) -> None:
    service = collector_module.Collector()
    answers = iter([RuntimeError("socket closed by the far side"), 30.0])

    async def read(widget_id: int) -> float | None:
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        live.set(widget_id, collector_module.WidgetData())
        return answer

    monkeypatch.setattr(service, "_refresh", read)
    monkeypatch.setattr(collector_module.random, "uniform", lambda a, b: 0.0)
    live.clear()
    service.running = True
    service._tasks[1] = asyncio.get_running_loop().create_task(asyncio.sleep(3600))

    started = time.time()
    interval = await service.refresh(1)
    service._note_run(1, started, interval)
    run = service.runs()[1]
    assert run.reason == "RuntimeError: socket closed by the far side"
    assert run.interval == collector_module.RECOVERY_INTERVAL
    assert "socket" not in (live.get(1).error or ""), "the card itself keeps the reason to itself"

    interval = await service.refresh(1)
    service._note_run(1, time.time(), interval)
    assert service.runs()[1].reason == "" and service.runs()[1].interval == 30.0

    service._cancel(1)
    assert 1 not in service.runs(), "a card that is no longer read leaves the list"


def test_the_list_is_for_the_administrator(client: TestClient) -> None:
    setup_admin(client, demo=True)
    deadline = time.monotonic() + 10
    rows: list[dict] = []
    while time.monotonic() < deadline:
        answer = client.get("/api/v1/system/cards")
        assert answer.status_code == 200, answer.text
        rows = answer.json()
        if rows:
            break
        time.sleep(0.2)
    assert rows, "the demo board's cards are read within seconds"
    row = rows[0]
    assert {"id", "title", "kind", "board", "board_slug", "page", "integration", "status", "error",
            "started", "seconds", "interval", "next", "failures", "reason"} <= set(row)

    create_user(client, "kim")
    kim = TestClient(client.app)
    login(kim, "kim", "another-long-password")
    assert kim.get("/api/v1/system/cards").status_code == 403
