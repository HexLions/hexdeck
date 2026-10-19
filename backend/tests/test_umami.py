"""Umami, against answers written from its source at release v3.4.0.

The shapes come from ``src/app/api/websites`` (a page of ``data`` with
``count``), ``getActiveVisitors`` (``{"visitors": n}``), ``getWebsiteStats``
plus the route's ``comparison`` block, ``auth/login`` and the ``unauthorized``
answer of ``src/lib/response.ts``. Not yet run against an installation.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

UM = "http://umami.local:3000"
KEY = {"url": UM, "api_key": "umami_" + "a" * 32}
LOGIN = {"url": UM, "username": "admin", "password": "umami-password"}
SITES = {"data": [
    {"id": "0f1e2d3c-0000-4000-8000-000000000002", "name": "Shop", "domain": "shop.example.com"},
    {"id": "0f1e2d3c-0000-4000-8000-000000000001", "name": "Blog", "domain": "blog.example.com"},
], "count": 2, "page": 1, "pageSize": 100}
STATS = {"pageviews": 1290, "visitors": 412, "visits": 503, "bounces": 251, "totaltime": 88210,
         "comparison": {"pageviews": 1102, "visitors": 380, "visits": 455, "bounces": 230, "totaltime": 80122}}
UNAUTHORIZED = {"error": {"message": "Unauthorized", "code": "unauthorized", "status": 401}}


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _server(stats: dict | None = None) -> None:
    respx.get(f"{UM}/api/websites").mock(return_value=httpx.Response(200, json=SITES))
    respx.get(url__regex=rf"{UM}/api/websites/[^/]+/active").mock(return_value=httpx.Response(200, json={"visitors": 7}))
    respx.get(url__regex=rf"{UM}/api/websites/[^/]+/stats.*").mock(return_value=httpx.Response(200, json=stats or STATS))


@respx.mock
async def test_one_site_with_an_api_key(ctx: Context) -> None:
    _server()
    data = await get_adapter("umami").fetch("site", KEY, {"website": "blog.example.com"}, ctx)
    assert data.primary == {"label": "Right now", "value": 7}
    assert data.secondary == [{"label": "Visitors", "value": 412}, {"label": "Views", "value": 1290},
                              {"label": "Bounce rate", "value": 50, "unit": "%"}]
    stats = next(call.request for call in respx.calls if "/stats" in call.request.url.path)
    assert "0f1e2d3c-0000-4000-8000-000000000001" in stats.url.path
    assert int(stats.url.params["endAt"]) - int(stats.url.params["startAt"]) == 86_400_000
    assert stats.headers["authorization"] == "Bearer " + KEY["api_key"]


@respx.mock
async def test_numbers_that_come_as_strings(ctx: Context) -> None:
    """ClickHouse can hand UInt64 over as text."""
    _server(stats={"pageviews": "30", "visitors": "12", "visits": "0", "bounces": "0", "totaltime": "0"})
    data = await get_adapter("umami").fetch("site", KEY, {}, ctx)
    assert data.secondary == [{"label": "Visitors", "value": 12}, {"label": "Views", "value": 30}]


@respx.mock
async def test_the_site_list(ctx: Context) -> None:
    _server()
    data = await get_adapter("umami").fetch("sites", KEY, {}, ctx)
    assert [row["title"] for row in data.items] == ["Blog", "Shop"]
    assert data.items[0]["value"] == "7 now" and data.items[0]["subtitle"] == "412 visitors · 1290 views in 24 h"


@respx.mock
async def test_signing_in_and_again_after_a_refusal(ctx: Context) -> None:
    login = respx.post(f"{UM}/api/auth/login").mock(side_effect=[
        httpx.Response(200, json={"token": "first", "user": {"id": "u", "username": "admin"}}),
        httpx.Response(200, json={"token": "second", "user": {"id": "u", "username": "admin"}}),
    ])
    respx.get(f"{UM}/api/websites").mock(side_effect=[httpx.Response(401, json=UNAUTHORIZED), httpx.Response(200, json=SITES)])
    assert await get_adapter("umami").test(LOGIN, ctx) == "Umami answers with 2 sites."
    assert login.call_count == 2, "a token from before a password change is replaced once"
    assert json.loads(login.calls[0].request.content) == {"username": "admin", "password": "umami-password"}


@respx.mock
async def test_a_wrong_password(ctx: Context) -> None:
    respx.post(f"{UM}/api/auth/login").mock(return_value=httpx.Response(401, json={"error": {**UNAUTHORIZED["error"], "code": "incorrect-username-password"}}))
    with pytest.raises(AuthFailed):
        await get_adapter("umami").fetch("site", LOGIN, {}, ctx)


@respx.mock
async def test_two_factor_sign_in_is_said_as_such(ctx: Context) -> None:
    respx.post(f"{UM}/api/auth/login").mock(return_value=httpx.Response(200, json={"requiresTwoFactor": True, "partialToken": "x"}))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("umami").fetch("site", LOGIN, {}, ctx)
    assert "two-factor" in failure.value.message


@respx.mock
async def test_a_wrong_api_key_is_not_retried(ctx: Context) -> None:
    route = respx.get(f"{UM}/api/websites").mock(return_value=httpx.Response(401, json=UNAUTHORIZED))
    with pytest.raises(AuthFailed):
        await get_adapter("umami").fetch("site", KEY, {}, ctx)
    assert route.call_count == 1


async def test_neither_key_nor_password(ctx: Context) -> None:
    with pytest.raises(AuthFailed):
        await get_adapter("umami").fetch("site", {"url": UM}, {}, ctx)


@respx.mock
async def test_a_site_that_is_not_there(ctx: Context) -> None:
    _server()
    with pytest.raises(AdapterError) as failure:
        await get_adapter("umami").fetch("site", KEY, {"website": "nowhere"}, ctx)
    assert failure.value.code == "no_such_site"


def test_the_demo() -> None:
    adapter = get_adapter("umami")
    for tick in range(0, 100, 9):
        assert adapter.demo("site", {}, tick).primary["label"] == "Right now"
        assert len(adapter.demo("sites", {}, tick).items) == 3


@respx.mock
async def test_a_number_the_answer_did_not_carry(ctx: Context) -> None:
    """Shown as unknown and kept out of the history, not written down as 0."""
    respx.get(f"{UM}/api/websites").mock(return_value=httpx.Response(200, json=SITES))
    respx.get(url__regex=rf"{UM}/api/websites/[^/]+/active").mock(return_value=httpx.Response(200, json={}))
    respx.get(url__regex=rf"{UM}/api/websites/[^/]+/stats.*").mock(return_value=httpx.Response(200, json={"pageviews": 4}))
    data = await get_adapter("umami").fetch("site", KEY, {}, ctx)
    assert data.primary["value"] == "?" and data.secondary[0] == {"label": "Visitors", "value": "?"}
    assert data.metrics == {}


@respx.mock
async def test_more_sites_than_one_page(ctx: Context) -> None:
    _server()
    respx.get(f"{UM}/api/websites").mock(return_value=httpx.Response(200, json={**SITES, "count": 140}))
    data = await get_adapter("umami").fetch("sites", KEY, {}, ctx)
    assert data.secondary == [{"label": "Sites", "value": 140}]


@respx.mock
async def test_errors_and_something_else(ctx: Context) -> None:
    respx.get(f"{UM}/api/websites").mock(side_effect=[httpx.Response(500), httpx.Response(200, text="<html>")])
    with pytest.raises(AdapterError) as failure:
        await get_adapter("umami").fetch("site", KEY, {}, ctx)
    assert failure.value.code == "http_error"
    with pytest.raises(AdapterError) as failure:
        await get_adapter("umami").fetch("site", KEY, {}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
    assert failure.value.code == "not_umami"
