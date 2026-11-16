"""Medusa: the library, the missed episodes and the ones coming up.

``medusa_api.json`` holds what Medusa v1.0.27 answered locally on 2026-11-16 with three series added from TVmaze
(two that ended, one airing every weekday), trimmed to two episodes per category, and its answer to a wrong key.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context
from app.adapters.medusa import MedusaAdapter

BASE = "http://medusa.local:8081"
CONFIG = {"url": BASE, "api_key": "made-up-key"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "medusa_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _medusa() -> None:
    respx.get(f"{BASE}/api/v2/stats").mock(return_value=httpx.Response(200, json=ANSWERS["stats"]))
    respx.get(url__startswith=f"{BASE}/api/v2/schedule").mock(return_value=httpx.Response(200, json=ANSWERS["schedule"]))
    respx.get(f"{BASE}/api/v2/config/system").mock(return_value=httpx.Response(200, json={"release": ANSWERS["system_release"], "dataDir": "/config"}))


@respx.mock
async def test_the_key_goes_in_its_header_and_never_in_the_address() -> None:
    _medusa()
    assert await get_adapter("medusa").test(CONFIG, _ctx()) == "Medusa 1.0.27 answers."
    request = respx.calls[0].request
    assert request.headers["X-Api-Key"] == "made-up-key"
    assert "made-up-key" not in str(request.url)


@respx.mock
async def test_a_wrong_key() -> None:
    respx.get(f"{BASE}/api/v2/config/system").mock(return_value=httpx.Response(401, json=ANSWERS["wrong_key"]))
    with pytest.raises(AuthFailed):
        await get_adapter("medusa").test(CONFIG, _ctx())


@respx.mock
async def test_every_category_is_asked_for_by_name() -> None:
    """Without a category Medusa answers the missed episodes alone."""
    _medusa()
    await get_adapter("medusa").fetch("calendar", CONFIG, {}, _ctx())
    asked = respx.calls[0].request.url
    assert asked.params.get_list("category[]") == ["missed", "today", "soon", "later"]
    assert asked.params["paused"] == "false"


@respx.mock
async def test_the_library_counts_series_and_the_missed_episodes() -> None:
    _medusa()
    data = await get_adapter("medusa").fetch("status", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Series", "value": 3}
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values == {"Continuing": 1, "Downloaded": 0, "Snatched": 0, "Missed": len(ANSWERS["schedule"]["missed"])}
    assert data.status == "warn", "an aired episode that is still wanted"
    assert data.metrics == {"missed": 2.0}


def test_nothing_missed_is_green() -> None:
    data = MedusaAdapter._library(ANSWERS["stats"], 0)
    assert data.status == "ok"


def test_the_calendar_keeps_to_the_days_asked_and_leaves_the_missed_out() -> None:
    schedule = {name: ANSWERS["schedule"].get(name, []) for name in ("missed", "today", "soon", "later")}
    data = MedusaAdapter._calendar(schedule, 7, date(2026, 11, 16))
    assert [(item["date"], item["subtitle"]) for item in data.items] == [
        ("2026-11-16", "S2026E224 Ep. #13508"),
        ("2026-11-17", "S2026E225 Ep. #13509"),
        ("2026-11-18", "S2026E226 Ep. #13510"),
    ]
    assert {item["title"] for item in data.items} == {"The Young and the Restless"}
    assert MedusaAdapter._calendar(schedule, 1, date(2026, 11, 16)).items[-1]["date"] == "2026-11-17"


@pytest.mark.parametrize("kind", ["status", "calendar"])
def test_every_card_has_a_demo(kind: str) -> None:
    data = get_adapter("medusa").demo(kind, {}, 3)
    assert data.primary or data.items
