"""Protezione Civile, against the criticality bulletin of 03.11.2026 at 14:30.

The zones are three of the real bulletin's 187, their municipality lists and
alerts as published, their polygons cut down to a triangle (the card reads
none). The page is a few lines of the real page's HTML around the id.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import dpc as module
from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dpc_bulletin.json").read_text())
TODAY = module.FILES.format("20261103_1430", "today")
TOMORROW = module.FILES.format("20261103_1430", "tomorrow")


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={})


@pytest.fixture
def bulletin(monkeypatch: pytest.MonkeyPatch):
    class Day(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 11, 3)

    monkeypatch.setattr(module, "date", Day)
    with respx.mock(assert_all_called=False) as router:
        router.get(module.PAGE).mock(return_value=httpx.Response(200, text=FIXTURE["page"]))
        router.get(TODAY).mock(return_value=httpx.Response(200, json=FIXTURE["today"]))
        router.get(TOMORROW).mock(return_value=httpx.Response(200, json=FIXTURE["tomorrow"]))
        yield router


async def test_the_latest_bulletin_named_on_the_page_is_read(bulletin) -> None:
    assert await get_adapter("dpc").test({}, _ctx()) == "The Civil Protection's latest bulletin is of 03.11.2026 at 14:30."


async def test_a_municipality_with_an_orange_tomorrow(bulletin) -> None:
    data = await get_adapter("dpc").fetch("alert", {}, {"municipality": "Aritzo"}, _ctx())
    assert data.primary == {"label": "Today", "value": "No alert"} and data.status == "ok"
    assert data.secondary == [{"label": "Zone", "value": "Bacini Flumendosa - Flumineddu"}, {"label": "Tomorrow", "value": "Orange"}]
    risks = await get_adapter("dpc").fetch("risks", {}, {"municipality": "Aritzo"}, _ctx())
    assert [(item["title"], item["subtitle"], item["value"]) for item in risks.items if item["subtitle"] == "Tomorrow"] == [
        ("Flooding", "Tomorrow", "No alert"), ("Landslides", "Tomorrow", "Orange"), ("Storms", "Tomorrow", "Yellow")]
    assert risks.status == "bad"


async def test_the_name_is_matched_without_case_or_apostrophe(bulletin) -> None:
    data = await get_adapter("dpc").fetch("alert", {}, {"municipality": "SANT OMERO"}, _ctx())
    assert data.secondary[0] == {"label": "Zone", "value": "Bacini Tordino Vomano"}


async def test_before_the_days_bulletin_yesterdays_tomorrow_is_today(bulletin, monkeypatch: pytest.MonkeyPatch) -> None:
    class Day(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 11, 4)

    monkeypatch.setattr(module, "date", Day)
    data = await get_adapter("dpc").fetch("alert", {}, {"municipality": "Aritzo"}, _ctx())
    assert data.primary == {"label": "Today", "value": "Orange"} and data.status == "bad"
    assert all(row["label"] != "Tomorrow" for row in data.secondary)


async def test_an_unknown_municipality_is_said(bulletin) -> None:
    with pytest.raises(AdapterError) as refused:
        await get_adapter("dpc").fetch("alert", {}, {"municipality": "Atlantide"}, _ctx())
    assert refused.value.code == "unknown_municipality"


@respx.mock
async def test_a_page_without_a_bulletin_is_said() -> None:
    respx.get(module.PAGE).mock(return_value=httpx.Response(200, text="<html>Manutenzione</html>"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("dpc").test({}, _ctx())
    assert refused.value.code == "no_bulletin"
