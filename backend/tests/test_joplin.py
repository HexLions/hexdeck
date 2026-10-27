"""Joplin Server, against answers shaped as its source (server-v3.7.3) writes them.

Not recorded from a running server: Joplin Server ships only as a container
image. The shapes come from routes/api/sessions.ts, users.ts and items.ts and
models/utils/pagination.ts; the error texts are the ones those files throw.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "joplin_server.json").read_text())
URL = "https://joplin.example.com"
CONFIG = {"url": URL, "email": "admin@example.com", "password": "secret"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _children(request: httpx.Request) -> httpx.Response:
    assert request.headers["X-API-AUTH"] == FIXTURE["session"]["id"]
    if "cursor" in request.url.params:
        assert request.url.params["cursor"] == FIXTURE["page1"]["cursor"]
        return httpx.Response(200, json=FIXTURE["page2"])
    assert request.url.params["limit"] == "100"
    return httpx.Response(200, json=FIXTURE["page1"])


@respx.mock
async def test_sync_counts_every_page_and_names_the_newest_change() -> None:
    login = respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, json=FIXTURE["session"]))
    respx.get(f"{URL}/api/items/root/children").mock(side_effect=_children)
    data = await get_adapter("joplin").fetch("sync", CONFIG, {}, _ctx())
    assert json.loads(login.calls[0].request.content) == {"email": "admin@example.com", "password": "secret"}
    assert data.secondary == [{"label": "Items", "value": 3}]
    assert data.metrics == {"items": 3.0}
    assert data.primary["label"] == "Last change" and data.primary["value"].endswith(" ago")
    assert data.status == "ok"


@respx.mock
async def test_sync_stops_counting_after_ten_pages() -> None:
    respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, json=FIXTURE["session"]))
    items = [{"id": f"i{n}", "name": f"{n:032x}.md", "updated_time": 1790000000000} for n in range(100)]
    pages = iter(range(100))
    # Every page hands out the cursor of the next, as the server does.
    route = respx.get(f"{URL}/api/items/root/children").mock(
        side_effect=lambda request: httpx.Response(200, json={"items": items, "has_more": True, "cursor": f"page{next(pages)}"}))
    data = await get_adapter("joplin").fetch("sync", CONFIG, {}, _ctx())
    assert route.call_count == 10
    assert data.secondary == [{"label": "Items", "value": "1000+"}]


@respx.mock
async def test_an_expired_session_signs_in_again_once() -> None:
    ctx = _ctx()
    ctx.cache["joplin:session"] = ("old-session", "u")
    login = respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, json=FIXTURE["session"]))

    def answer(request: httpx.Request) -> httpx.Response:
        if request.headers["X-API-AUTH"] == "old-session":
            return httpx.Response(403, json=FIXTURE["expired"])
        return httpx.Response(200, json=FIXTURE["page2"])

    respx.get(f"{URL}/api/items/root/children").mock(side_effect=answer)
    data = await get_adapter("joplin").fetch("sync", CONFIG, {}, ctx)
    assert login.call_count == 1
    assert data.secondary == [{"label": "Items", "value": 1}]


@respx.mock
async def test_users_shows_storage_and_quota_the_fullest_first_and_nothing_else() -> None:
    respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, json=FIXTURE["session"]))
    respx.get(f"{URL}/api/users").mock(return_value=httpx.Response(200, json=FIXTURE["users"]))
    data = await get_adapter("joplin").fetch("users", CONFIG, {}, _ctx())
    assert [(item["title"], item["value"], item["status"]) for item in data.items] == [
        ("Alex", "90%", "warn"), ("Admin", "100.0 MB", "ok"), ("old@example.com", "0 B", "unknown"),
    ]
    assert data.items[0]["subtitle"] == "alex@example.com · 1.8 GB of 2.0 GB"
    assert data.items[2]["subtitle"].endswith("disabled")
    assert "notarealhash" not in json.dumps(data.model_dump() if hasattr(data, "model_dump") else data.__dict__, default=str)
    assert data.secondary[0] == {"label": "Accounts", "value": 3}


@respx.mock
async def test_users_needs_an_administrator() -> None:
    respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, json=FIXTURE["session"]))
    respx.get(f"{URL}/api/users").mock(return_value=httpx.Response(403, json=FIXTURE["not_admin"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("joplin").fetch("users", CONFIG, {}, _ctx())
    assert "administrator" in str(refused.value)


@respx.mock
async def test_a_wrong_password_and_two_factor_are_told_apart() -> None:
    route = respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(403, json=FIXTURE["wrong_password"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("joplin").test(CONFIG, _ctx())
    assert "e-mail or password" in str(refused.value)
    route.mock(return_value=httpx.Response(403, json=FIXTURE["mfa"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("joplin").test(CONFIG, _ctx())
    assert "second factor" in str(refused.value)


@respx.mock
async def test_too_many_sign_ins_is_not_a_wrong_password() -> None:
    respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(429, text="Too many requests"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("joplin").test(CONFIG, _ctx())
    assert refused.value.code == "rate_limited"


@respx.mock
async def test_the_connection_test_names_the_account() -> None:
    respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, json=FIXTURE["session"]))
    respx.get(f"{URL}/api/users/{FIXTURE['session']['user_id']}").mock(return_value=httpx.Response(200, json=FIXTURE["me"]))
    assert await get_adapter("joplin").test(CONFIG, _ctx()) == "Joplin Server signs in Admin."


@respx.mock
async def test_something_else_at_the_address_is_said() -> None:
    respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, text="<html>hello</html>"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("joplin").test(CONFIG, _ctx())
    assert refused.value.code == "not_joplin"


@respx.mock
async def test_another_host_than_app_base_url_is_named() -> None:
    respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(404, json={"error": f"Invalid origin: {URL}"}))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("joplin").test(CONFIG, _ctx())
    assert refused.value.code == "wrong_host" and "APP_BASE_URL" in refused.value.hint


@respx.mock
async def test_a_normal_account_on_the_accounts_card_does_not_sign_in_again() -> None:
    ctx = _ctx()
    ctx.cache["joplin:session"] = (FIXTURE["session"]["id"], "u")
    login = respx.post(f"{URL}/api/sessions").mock(return_value=httpx.Response(200, json=FIXTURE["session"]))
    respx.get(f"{URL}/api/users").mock(return_value=httpx.Response(403, json=FIXTURE["not_admin"]))
    with pytest.raises(AuthFailed):
        await get_adapter("joplin").fetch("users", CONFIG, {}, ctx)
    assert login.call_count == 0, "sign-ins are rate limited; only an expired session earns a new one"
