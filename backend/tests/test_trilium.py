"""Trilium Notes: the counts, and the notes changed last or searched for.

``trilium_metrics.json`` and ``trilium_notes.json`` are ``/etapi/metrics``
and a search for the six notes changed last, as Trilium Notes 0.106.0
answered them locally on 2026-10-10 on a new document with four notes added.
The refusal of a wrong token was measured the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://trilium.local:8080"
CONFIG = {"url": BASE, "token": "made-up-token"}
FIXTURES = Path(__file__).parent / "fixtures"
METRICS = json.loads((FIXTURES / "trilium_metrics.json").read_text())
NOTES = json.loads((FIXTURES / "trilium_notes.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


@respx.mock
async def test_the_token_goes_in_as_it_is() -> None:
    respx.get(f"{BASE}/etapi/app-info").mock(return_value=httpx.Response(200, json={"appVersion": "0.106.0"}))
    assert await get_adapter("trilium").test(CONFIG, _ctx()) == "Trilium 0.106.0 answers."
    assert respx.calls[0].request.headers["authorization"] == "made-up-token"


@respx.mock
async def test_a_wrong_token() -> None:
    respx.get(f"{BASE}/etapi/app-info").mock(return_value=httpx.Response(401, json={"status": 401, "code": "NOT_AUTHENTICATED"}))
    with pytest.raises(AuthFailed):
        await get_adapter("trilium").test(CONFIG, _ctx())


@respx.mock
async def test_the_overview_from_the_metrics() -> None:
    respx.get(f"{BASE}/etapi/metrics").mock(return_value=httpx.Response(200, json=METRICS))
    data = await get_adapter("trilium").fetch("overview", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Notes", "value": METRICS["database"]["activeNotes"], "metric": "notes"}
    assert {"label": "Attachments", "value": 19} in data.secondary
    assert {"label": "Version", "value": "0.106.0"} in data.secondary
    assert respx.calls[0].request.url.params["format"] == "json"


@respx.mock
async def test_an_older_trilium_without_metrics_shows_its_version() -> None:
    respx.get(f"{BASE}/etapi/metrics").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/etapi/app-info").mock(return_value=httpx.Response(200, json={"appVersion": "0.63.7"}))
    data = await get_adapter("trilium").fetch("overview", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Version", "value": "0.63.7"} and "0.94" in data.meta["notice"]


@respx.mock
async def test_the_notes_changed_last() -> None:
    route = respx.get(f"{BASE}/etapi/notes").mock(return_value=httpx.Response(200, json=NOTES))
    data = await get_adapter("trilium").fetch("notes", CONFIG, {"limit": 3}, _ctx())
    assert [row["title"] for row in data.items] == ["Meeting 2026-10-09", "Reading notes", "Homelab plan"]
    assert data.items[0]["url"] == f"{BASE}/#root/{NOTES['results'][0]['noteId']}"
    params = route.calls[0].request.url.params
    assert params["search"] == "note.title != ''" and params["orderBy"] == "dateModified" and params["orderDirection"] == "desc"


@respx.mock
async def test_a_search_of_ones_own_and_the_hidden_notes_left_out() -> None:
    hidden = {"results": [{"noteId": "_options", "title": "Options", "type": "book"}, {"noteId": "root", "title": "root"}, *NOTES["results"][:1]]}
    route = respx.get(f"{BASE}/etapi/notes").mock(return_value=httpx.Response(200, json=hidden))
    data = await get_adapter("trilium").fetch("notes", CONFIG, {"search": "#todo"}, _ctx())
    assert route.calls[0].request.url.params["search"] == "#todo"
    assert [row["title"] for row in data.items] == ["Meeting 2026-10-09"]
