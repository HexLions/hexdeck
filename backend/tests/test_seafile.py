"""Seafile, against answers shaped as Seahub's source (v14.0.9-server) writes them.

Not recorded from a running Seafile. The bodies follow seahub/api2/views.py
(ObtainAuthToken, AccountInfo, Repos); the refusals are those of
serializers.py and authentication.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "seafile_api2.json").read_text())
URL = "https://seafile.example.com"
TOKEN_CONFIG = {"url": URL, "token": "24fd3c026886e3121b2ca630805ed425c272cb96"}
LOGIN_CONFIG = {"url": URL, "username": "alex@example.com", "password": "secret"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _seafile(account: str = "account") -> None:
    respx.get(f"{URL}/api2/account/info/").mock(return_value=httpx.Response(200, json=FIXTURE[account]))
    respx.get(f"{URL}/api2/repos/", params={"type": "mine"}).mock(return_value=httpx.Response(200, json=FIXTURE["repos"]))


@respx.mock
async def test_space_is_the_share_of_the_quota() -> None:
    _seafile()
    data = await get_adapter("seafile").fetch("space", TOKEN_CONFIG, {}, _ctx())
    assert data.primary == {"label": "Used", "value": "66%"}
    assert data.secondary == [{"label": "Used", "value": "66.1 GB"}, {"label": "Quota", "value": "100.0 GB"}, {"label": "Libraries", "value": 3}]
    assert respx.calls[0].request.headers["Authorization"] == f"Token {TOKEN_CONFIG['token']}"


@respx.mock
async def test_a_negative_quota_is_no_limit() -> None:
    _seafile("account_unlimited")
    data = await get_adapter("seafile").fetch("space", TOKEN_CONFIG, {}, _ctx())
    assert data.primary == {"label": "Used", "value": "5.0 GB"}
    assert data.secondary[0] == {"label": "Quota", "value": "No limit"}
    assert data.status == "ok"


@respx.mock
async def test_libraries_newest_change_first_with_size() -> None:
    _seafile()
    data = await get_adapter("seafile").fetch("libraries", TOKEN_CONFIG, {}, _ctx())
    assert [(item["title"], item["value"]) for item in data.items] == [("Photos", "61.2 GB"), ("Documents", "4.0 GB"), ("Tax returns", "900.0 MB")]
    assert data.items[2]["subtitle"].startswith("Alex · ") and data.items[2]["subtitle"].endswith(" · encrypted")


@respx.mock
async def test_a_sign_in_asks_for_the_token_once_as_a_form() -> None:
    login = respx.post(f"{URL}/api2/auth-token/").mock(return_value=httpx.Response(200, json=FIXTURE["token"]))
    _seafile()
    ctx = _ctx()
    await get_adapter("seafile").fetch("space", LOGIN_CONFIG, {}, ctx)
    await get_adapter("seafile").fetch("libraries", LOGIN_CONFIG, {}, ctx)
    assert login.call_count == 1
    assert login.calls[0].request.content.decode() in ("username=alex%40example.com&password=secret", "password=secret&username=alex%40example.com")


@respx.mock
async def test_a_wrong_password_is_400_and_two_factor_is_said() -> None:
    route = respx.post(f"{URL}/api2/auth-token/").mock(return_value=httpx.Response(400, json=FIXTURE["wrong_password"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("seafile").test(LOGIN_CONFIG, _ctx())
    assert "user name or password" in str(refused.value)
    route.mock(return_value=httpx.Response(400, json=FIXTURE["wrong_password"], headers={"X-Seafile-OTP": "required"}))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("seafile").test(LOGIN_CONFIG, _ctx())
    assert "second factor" in str(refused.value) and "Web API Auth Token" in refused.value.hint


@respx.mock
async def test_a_refused_token_is_said() -> None:
    respx.get(f"{URL}/api2/account/info/").mock(return_value=httpx.Response(401, json=FIXTURE["invalid_token"]))
    with pytest.raises(AuthFailed):
        await get_adapter("seafile").test(TOKEN_CONFIG, _ctx())


@respx.mock
async def test_the_connection_test_names_the_account() -> None:
    respx.get(f"{URL}/api2/account/info/").mock(return_value=httpx.Response(200, json=FIXTURE["account"]))
    assert await get_adapter("seafile").test(TOKEN_CONFIG, _ctx()) == "Seafile answers for Alex."


@respx.mock
async def test_something_else_at_the_address_is_said() -> None:
    respx.get(f"{URL}/api2/account/info/").mock(return_value=httpx.Response(200, text="<html></html>"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("seafile").test(TOKEN_CONFIG, _ctx())
    assert refused.value.code == "not_seafile"


@respx.mock
async def test_a_token_from_a_sign_in_that_was_deleted_signs_in_once_more() -> None:
    ctx = _ctx()
    ctx.cache["seafile:token"] = "deleted-token"
    login = respx.post(f"{URL}/api2/auth-token/").mock(return_value=httpx.Response(200, json=FIXTURE["token"]))

    def account(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Token deleted-token":
            return httpx.Response(401, json=FIXTURE["invalid_token"])
        return httpx.Response(200, json=FIXTURE["account"])

    respx.get(f"{URL}/api2/account/info/").mock(side_effect=account)
    assert await get_adapter("seafile").test(LOGIN_CONFIG, ctx) == "Seafile answers for Alex."
    assert login.call_count == 1
