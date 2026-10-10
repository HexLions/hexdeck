"""Spoolman: what is left on each spool, and the ones about to run out.

fixtures/spoolman_spools.json is /api/v1/spool?sort=remaining_weight:asc from
Spoolman v0.27.0 running locally on 2026-10-09: three spools in use, one of
them down to 60 g; a fourth, archived, was left out by the API itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

FIXTURES = Path(__file__).parent / "fixtures"
SPOOLS = json.loads((FIXTURES / "spoolman_spools.json").read_text())
URL = "http://spoolman:7912"
CONFIG = {"url": URL}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _spoolman(spools: list | None = None, total: str | None = "3") -> None:
    headers = {"x-total-count": total} if total else {}
    respx.get(f"{URL}/api/v1/spool").mock(return_value=httpx.Response(200, json=spools if spools is not None else SPOOLS, headers=headers))
    respx.get(f"{URL}/api/v1/info").mock(return_value=httpx.Response(200, json={"version": "0.27.0"}))


@respx.mock
async def test_the_emptiest_spool_comes_first_with_its_bar_and_colour() -> None:
    _spoolman()
    data = await get_adapter("spoolman").fetch("spools", CONFIG, {}, _ctx())
    assert respx.calls[0].request.url.params["sort"] == "remaining_weight:asc"
    first = data.items[0]
    assert first["title"] == "Prusament Galaxy Black PLA"
    assert first["value"] == "60 g" and first["progress"] == 6.0 and first["status"] == "warn"
    assert first["subtitle"] == "Printer 1 · #1B1B1F"
    assert data.status == "warn"


@respx.mock
async def test_the_threshold_is_the_cards_to_choose() -> None:
    _spoolman()
    data = await get_adapter("spoolman").fetch("spools", CONFIG, {"low_grams": 50}, _ctx())
    assert all(row["status"] == "ok" for row in data.items)


@respx.mock
async def test_a_place_is_passed_on() -> None:
    _spoolman()
    await get_adapter("spoolman").fetch("spools", CONFIG, {"location": "Printer 1"}, _ctx())
    assert respx.calls[0].request.url.params["location"] == "Printer 1"


@respx.mock
async def test_a_spool_without_a_known_weight_has_no_bar_and_is_never_running_out() -> None:
    unknown = {**SPOOLS[0], "remaining_weight": None, "initial_weight": None}
    _spoolman([unknown], total="1")
    data = await get_adapter("spoolman").fetch("spools", CONFIG, {}, _ctx())
    assert "progress" not in data.items[0] and data.items[0]["status"] == "ok" and data.items[0]["value"] == ""
    summary = await get_adapter("spoolman").fetch("summary", CONFIG, {}, _ctx())
    assert "Running out" not in {row["label"] for row in summary.secondary}


@respx.mock
async def test_the_summary_adds_what_is_left_and_counts_from_the_header() -> None:
    _spoolman(total="12")
    data = await get_adapter("spoolman").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Spools in use", "value": 12}
    assert {row["label"]: row["value"] for row in data.secondary} == {"Left": "1.6 kg", "Running out": 1}
    assert data.metrics == {"left": 1.6, "low": 1.0}


@respx.mock
async def test_a_proxy_refusal_says_spoolman_itself_asks_for_nothing() -> None:
    respx.get(f"{URL}/api/v1/spool").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("spoolman").fetch("spools", CONFIG, {}, _ctx())
    assert "asks for nothing" in failure.value.hint


@respx.mock
async def test_a_wrong_address_is_named() -> None:
    respx.get(f"{URL}/api/v1/spool").mock(return_value=httpx.Response(404, text="Not Found"))
    with pytest.raises(AdapterError):
        await get_adapter("spoolman").fetch("spools", CONFIG, {}, _ctx())


@respx.mock
async def test_the_connection_test_names_the_version_and_the_count() -> None:
    _spoolman()
    assert await get_adapter("spoolman").test(CONFIG, _ctx()) == "Spoolman 0.27.0 answers with 3 spools in use."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("spoolman")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
