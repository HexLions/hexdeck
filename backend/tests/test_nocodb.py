"""NocoDB, against answers shaped as its OpenAPI file of release 2026.10.0 describes them.

Not recorded from a running NocoDB. The shapes are the examples and schemas of
packages/nocodb/src/schema/swagger-v2.json; the refusal is the message the
API token strategy gives.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "nocodb_v2.json").read_text())
URL = "https://nocodb.example.com"
CONFIG = {"url": URL, "token": "nc-token-123"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _nocodb() -> respx.Route:
    respx.get(f"{URL}/api/v2/meta/bases/").mock(return_value=httpx.Response(200, json=FIXTURE["bases"]))
    respx.get(f"{URL}/api/v2/meta/bases/p_shop0000000001/tables").mock(return_value=httpx.Response(200, json=FIXTURE["shop_tables"]))
    respx.get(f"{URL}/api/v2/meta/bases/p_home0000000002/tables").mock(return_value=httpx.Response(200, json=FIXTURE["home_tables"]))

    def count(request: httpx.Request) -> httpx.Response:
        assert request.headers["xc-token"] == "nc-token-123"
        table = request.url.path.split("/")[4]
        return httpx.Response(200, json={"count": FIXTURE["counts"][table]})

    return respx.get(url__regex=rf"{URL}/api/v2/tables/[^/]+/records/count").mock(side_effect=count)


@respx.mock
async def test_the_summary_adds_up_the_rows_of_every_table() -> None:
    counts = _nocodb()
    data = await get_adapter("nocodb").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Rows", "value": 1587}
    assert data.secondary == [{"label": "Bases", "value": 2}, {"label": "Tables", "value": 3}], "the deleted base and the link table left out"
    assert counts.call_count == 3


@respx.mock
async def test_the_tables_come_largest_first_with_their_base() -> None:
    _nocodb()
    data = await get_adapter("nocodb").fetch("tables", CONFIG, {"limit": 2}, _ctx())
    assert [(item["title"], item["subtitle"], item["value"]) for item in data.items] == [("Orders", "Shop", 1204), ("Customers", "Shop", 342)]


@respx.mock
async def test_past_the_counted_tables_the_rows_say_plus(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.adapters.nocodb.MAX_TABLES", 2)
    counts = _nocodb()
    data = await get_adapter("nocodb").fetch("summary", CONFIG, {}, _ctx())
    assert counts.call_count == 2
    assert data.primary == {"label": "Rows", "value": "1546+"}


@respx.mock
async def test_a_refused_token_says_where_to_make_one() -> None:
    respx.get(f"{URL}/api/v2/meta/bases/").mock(return_value=httpx.Response(401, json=FIXTURE["invalid_token"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("nocodb").test(CONFIG, _ctx())
    assert "Tokens" in refused.value.hint


@respx.mock
async def test_the_connection_test_counts_the_bases() -> None:
    respx.get(f"{URL}/api/v2/meta/bases/").mock(return_value=httpx.Response(200, json=FIXTURE["bases"]))
    assert await get_adapter("nocodb").test(CONFIG, _ctx()) == "NocoDB answers with 2 bases."


@respx.mock
async def test_something_else_at_the_address_is_said() -> None:
    respx.get(f"{URL}/api/v2/meta/bases/").mock(return_value=httpx.Response(200, text="<html></html>"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("nocodb").test(CONFIG, _ctx())
    assert refused.value.code == "not_nocodb"
