"""Wiki.js 2: the counts, the version, and the pages changed last.

``wikijs_graphql.json`` holds what Wiki.js 2.5.315 answered locally on
2026-10-10, with SQLite and four pages: ``system.info`` and ``pages.list``
with a full-access key, and ``system.info`` without one, which is
"Forbidden" with HTTP 200 (the stack trace cut to its first line). A wrong
key was read as a guest's the same day.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://wiki.local:3000"
CONFIG = {"url": BASE, "api_key": "made-up-key"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "wikijs_graphql.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _wikijs(info: dict | None = None) -> respx.Route:
    def answer(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        return httpx.Response(200, json=info or ANSWERS["info"] if "system" in query else ANSWERS["pages"])
    return respx.post(f"{BASE}/graphql").mock(side_effect=answer)


@respx.mock
async def test_the_key_goes_in_as_bearer() -> None:
    route = _wikijs()
    assert await get_adapter("wikijs").test(CONFIG, _ctx()) == "Wiki.js 2.5.315 answers, with 4 pages."
    assert route.calls[0].request.headers["authorization"] == "Bearer made-up-key"


@respx.mock
async def test_a_key_read_as_a_guest_is_refused() -> None:
    _wikijs(info=ANSWERS["forbidden"])
    with pytest.raises(AuthFailed, match="guest"):
        await get_adapter("wikijs").test(CONFIG, _ctx())


@respx.mock
async def test_the_overview() -> None:
    _wikijs()
    data = await get_adapter("wikijs").fetch("overview", CONFIG, {}, _ctx())
    assert data.status == "ok" and data.primary == {"label": "Pages", "value": 4, "metric": "pages"}
    assert data.secondary == [{"label": "Users", "value": 2}, {"label": "Tags", "value": 1}, {"label": "Version", "value": "2.5.315"}]


@respx.mock
async def test_an_update_out() -> None:
    info = json.loads(json.dumps(ANSWERS["info"]))
    info["data"]["system"]["info"]["latestVersion"] = "2.5.316"
    _wikijs(info=info)
    data = await get_adapter("wikijs").fetch("overview", CONFIG, {}, _ctx())
    assert data.status == "warn" and data.meta["notice"] == "Wiki.js 2.5.316 is out."
    assert {"label": "Version", "value": "2.5.315 → 2.5.316"} in data.secondary


@respx.mock
async def test_the_pages_changed_last_link_to_each() -> None:
    route = _wikijs()
    data = await get_adapter("wikijs").fetch("pages", CONFIG, {"limit": 3}, _ctx())
    assert [row["title"] for row in data.items] == ["Sourdough bread", "Backup plan", "Network layout"]
    assert data.items[0]["url"] == f"{BASE}/en/recipes/bread"
    assert "limit: 3" in json.loads(route.calls[0].request.content)["query"]
