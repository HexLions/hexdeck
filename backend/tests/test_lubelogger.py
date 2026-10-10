"""LubeLogger: the garage and the maintenance coming due.

``lubelogger_garage.json`` holds what LubeLogger 1.7.3 answered locally on
2026-10-10 with its login on: the vehicles, every reminder, the latest
odometer of the first vehicle and ``/api/vehicle/info`` without a vehicle.
Two vehicles, four reminders: one past due, one urgent, one by date and one
by distance only. A refused login, a 401 with the login page, was measured
the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context
from app.adapters.lubelogger import _due

BASE = "http://lubelogger.local:8080"
CONFIG = {"url": BASE, "username": "admin", "password": "made-up-password"}
GARAGE = json.loads((Path(__file__).parent / "fixtures" / "lubelogger_garage.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _lubelogger() -> None:
    respx.get(f"{BASE}/api/vehicles").mock(return_value=httpx.Response(200, json=GARAGE["vehicles"]))
    respx.get(f"{BASE}/api/version").mock(return_value=httpx.Response(200, json={"currentVersion": "1.7.3", "latestVersion": "1.7.3"}))
    respx.get(f"{BASE}/api/vehicle/info").mock(return_value=httpx.Response(200, json=GARAGE["info"]))
    respx.get(f"{BASE}/api/vehicle/reminders/all").mock(return_value=httpx.Response(200, json=GARAGE["reminders"]))


@respx.mock
async def test_basic_auth_when_a_user_is_set_and_none_without() -> None:
    _lubelogger()
    assert await get_adapter("lubelogger").test(CONFIG, _ctx()) == "LubeLogger 1.7.3 answers, with 2 vehicles."
    assert respx.calls[0].request.headers["authorization"].startswith("Basic ")
    await get_adapter("lubelogger").test({"url": BASE}, _ctx())
    assert "authorization" not in respx.calls[-1].request.headers


@respx.mock
async def test_a_refused_login_is_the_login_page_with_401() -> None:
    respx.get(f"{BASE}/api/vehicles").mock(return_value=httpx.Response(401, text="<!DOCTYPE html><html></html>"))
    with pytest.raises(AuthFailed):
        await get_adapter("lubelogger").test(CONFIG, _ctx())


@respx.mock
async def test_the_overview() -> None:
    _lubelogger()
    data = await get_adapter("lubelogger").fetch("overview", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.primary == {"label": "Past due", "value": 1, "metric": "past_due"}
    assert data.secondary == [{"label": "Urgent", "value": 1}, {"label": "Vehicles", "value": 2}]


@respx.mock
async def test_the_reminders_most_urgent_first() -> None:
    _lubelogger()
    data = await get_adapter("lubelogger").fetch("reminders", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"], row["status"]) for row in data.items] == [
        ("Tyre rotation", "10 d late", "bad"), ("Oil change", "in 9 d", "warn"),
        ("Timing belt", "500 to go", "ok"), ("Inspection (revisione)", "in 141 d", "ok")]
    assert data.items[0]["subtitle"] == "2019 Mazda CX-5 · past due"


@respx.mock
async def test_only_the_urgent_ones() -> None:
    _lubelogger()
    data = await get_adapter("lubelogger").fetch("reminders", CONFIG, {"urgent_only": True}, _ctx())
    assert [row["title"] for row in data.items] == ["Tyre rotation", "Oil change"]


def test_when_a_reminder_is_due() -> None:
    assert _due({"metric": "Date", "dueDays": "0"}) == "today"
    assert _due({"metric": "Odometer", "dueDistance": "-1200"}) == "1,200 over"
    assert _due({"metric": "Date", "dueDays": "soon"}) == ""
