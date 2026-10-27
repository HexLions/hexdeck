"""Outline, against answers shaped as its source (v1.12.0) writes them.

Not recorded from a running Outline. The bodies follow
server/presenters/document.ts and middlewares/pagination.ts; the refusals
are the messages of server/middlewares/authentication.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "outline_api.json").read_text())
URL = "https://wiki.example.com"
CONFIG = {"url": URL, "api_key": "ol_api_example"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


@respx.mock
async def test_the_summary_reads_the_three_totals() -> None:
    documents = respx.post(f"{URL}/api/documents.list").mock(return_value=httpx.Response(200, json=FIXTURE["documents_total"]))
    respx.post(f"{URL}/api/collections.list").mock(return_value=httpx.Response(200, json=FIXTURE["collections"]))
    respx.post(f"{URL}/api/users.list").mock(return_value=httpx.Response(200, json=FIXTURE["users_total"]))
    data = await get_adapter("outline").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Documents", "value": 128}
    assert data.secondary == [{"label": "Collections", "value": 6}, {"label": "Users", "value": 3}]
    request = documents.calls[0].request
    assert request.headers["Authorization"] == "Bearer ol_api_example"
    assert "x-client-version" not in request.headers, "with it Outline only estimates the total"
    assert json.loads(request.content) == {"limit": 1}


@respx.mock
async def test_recent_names_the_collection_who_and_when() -> None:
    documents = respx.post(f"{URL}/api/documents.list").mock(return_value=httpx.Response(200, json=FIXTURE["documents_recent"]))
    respx.post(f"{URL}/api/collections.list").mock(return_value=httpx.Response(200, json=FIXTURE["collections"]))
    data = await get_adapter("outline").fetch("recent", CONFIG, {"limit": 2}, _ctx())
    assert json.loads(documents.calls[0].request.content) == {"sort": "updatedAt", "direction": "DESC", "limit": 2}
    assert [item["title"] for item in data.items] == ["Backup rotation", "Untitled"]
    assert data.items[0]["subtitle"].startswith("Homelab · Alex · ") and data.items[0]["subtitle"].endswith(" ago")
    assert data.items[1]["subtitle"].startswith("Sam · "), "a collection the key cannot see is left out"


@respx.mock
async def test_a_wrong_key_and_a_scoped_key_are_told_apart() -> None:
    route = respx.post(f"{URL}/api/collections.list").mock(return_value=httpx.Response(401, json=FIXTURE["invalid_key"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("outline").test(CONFIG, _ctx())
    assert "refused the API key" in str(refused.value)
    route.mock(return_value=httpx.Response(403, json=FIXTURE["scoped_key"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("outline").test(CONFIG, _ctx())
    assert "collections.list" in str(refused.value) and "users.list" in refused.value.hint


@respx.mock
async def test_slowing_down_is_not_a_refusal() -> None:
    respx.post(f"{URL}/api/collections.list").mock(return_value=httpx.Response(429, json={"ok": False}))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("outline").test(CONFIG, _ctx())
    assert refused.value.code == "rate_limited"


@respx.mock
async def test_the_connection_test_counts_the_collections() -> None:
    respx.post(f"{URL}/api/collections.list").mock(return_value=httpx.Response(200, json=FIXTURE["collections"]))
    assert await get_adapter("outline").test(CONFIG, _ctx()) == "Outline answers with 6 collections."


@respx.mock
async def test_something_else_at_the_address_is_said() -> None:
    respx.post(f"{URL}/api/collections.list").mock(return_value=httpx.Response(200, text="<html></html>"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("outline").test(CONFIG, _ctx())
    assert refused.value.code == "not_outline"
