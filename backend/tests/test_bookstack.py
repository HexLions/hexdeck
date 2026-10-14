"""BookStack: counts and the pages changed last.

``bookstack_api.json`` is written in the shape BookStack v26.09.1 lists
with (``{"data": [...], "total": n}`` from ``ListingResponseBuilder``, the
page fields of ``PageApiController::list``) and the error bodies of its
exception handler. One page belongs to a book the token cannot see.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "https://wiki.example.com"
CONFIG = {"url": BASE, "token_id": "made-up-id", "token_secret": "made-up-secret"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "bookstack_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _bookstack() -> None:
    def listing(request: httpx.Request) -> httpx.Response:
        what = request.url.path.rsplit("/", 1)[-1]
        if request.url.params.get("count") == "1":
            return httpx.Response(200, json={"data": [], "total": ANSWERS["totals"][what]})
        return httpx.Response(200, json=ANSWERS[what])

    respx.get(url__regex=rf"{BASE}/api/(shelves|books|chapters|pages)").mock(side_effect=listing)


@respx.mock
async def test_the_counts() -> None:
    _bookstack()
    data = await get_adapter("bookstack").fetch("overview", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Pages", "value": 214, "metric": "pages"}
    assert data.secondary == [{"label": "Books", "value": 12}, {"label": "Shelves", "value": 4}, {"label": "Chapters", "value": 37}]
    assert respx.calls[0].request.headers["authorization"] == "Token made-up-id:made-up-secret"


@respx.mock
async def test_the_pages_changed_last_open_the_page() -> None:
    _bookstack()
    data = await get_adapter("bookstack").fetch("recent", CONFIG, {"limit": 4}, _ctx())
    first, draft, recipe, unseen = data.items
    assert first["title"] == "Proxmox upgrade notes" and first["url"] == f"{BASE}/books/homelab/page/proxmox-upgrade-notes"
    assert first["subtitle"].startswith("Homelab · changed")
    assert "draft" in draft["subtitle"]
    assert recipe["url"] == f"{BASE}/books/recipes/page/sourdough"
    assert unseen["url"] is None, "no address without the book's slug"
    pages_call = next(call for call in respx.calls if call.request.url.path.endswith("/pages"))
    assert pages_call.request.url.params["sort"] == "-updated_at"


@respx.mock
async def test_a_token_without_the_api_permission() -> None:
    respx.get(f"{BASE}/api/books").mock(return_value=httpx.Response(403, json=ANSWERS["no_permission"]))
    with pytest.raises(AuthFailed, match="does not have permission to make API calls"):
        await get_adapter("bookstack").test(CONFIG, _ctx())


@respx.mock
async def test_the_connection_test() -> None:
    _bookstack()
    assert await get_adapter("bookstack").test(CONFIG, _ctx()) == "BookStack answers with 12 books and 214 pages."
