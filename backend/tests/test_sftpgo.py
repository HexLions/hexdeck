"""SFTPGo, against the answers of SFTPGo v2.7.6 run locally on 20.10.2026.

The fixture is what that server answered an admin API key with three users
(one disabled, one with a quota and a 3 MB upload), one open SFTP session and
an address with failed sign-ins in the defender. Paths, the client address
and the public key are replaced.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

SG = "http://sftpgo.local:8080"
API = f"{SG}/api/v2"
CONFIG = {"url": SG, "api_key": "test-key-id.test-key-secret"}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sftpgo_server.json").read_text())


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _server(users: httpx.Response | None = None, defender: httpx.Response | None = None) -> None:
    respx.get(f"{API}/status").mock(return_value=httpx.Response(200, json=FIXTURE["status"]))
    respx.get(f"{API}/connections").mock(return_value=httpx.Response(200, json=FIXTURE["connections"]))
    respx.get(f"{API}/users").mock(return_value=users or httpx.Response(200, json=FIXTURE["users"]))
    respx.get(f"{API}/defender/hosts").mock(return_value=defender or httpx.Response(200, json=FIXTURE["defender"]))
    respx.get(f"{API}/version").mock(return_value=httpx.Response(200, json=FIXTURE["version"]))


@respx.mock
async def test_the_server(ctx: Context) -> None:
    _server()
    data = await get_adapter("sftpgo").fetch("status", CONFIG, {}, ctx)
    assert data.status == "ok"
    assert data.primary == {"label": "Connections", "value": 1}
    assert data.secondary == [{"label": "Users", "value": 2}, {"label": "Services", "value": "SFTP"}, {"label": "Banned", "value": 0}]
    assert respx.calls[0].request.headers["x-sftpgo-api-key"] == CONFIG["api_key"]


@respx.mock
async def test_a_banned_address_turns_it_amber(ctx: Context) -> None:
    _server(defender=httpx.Response(200, json=[{"id": "x", "ip": "203.0.113.7", "ban_time": "2026-10-20T11:05:17Z"}]))
    data = await get_adapter("sftpgo").fetch("status", CONFIG, {}, ctx)
    assert data.status == "warn" and data.secondary[-1] == {"label": "Banned", "value": 1}


@respx.mock
async def test_an_admin_that_may_see_less(ctx: Context) -> None:
    """403 Forbidden for the users and the defender: the card leaves them out."""
    forbidden = httpx.Response(403, json=FIXTURE["forbidden"])
    _server(users=forbidden, defender=forbidden)
    data = await get_adapter("sftpgo").fetch("status", CONFIG, {}, ctx)
    assert data.secondary == [{"label": "Services", "value": "SFTP"}]
    with pytest.raises(AuthFailed):
        await get_adapter("sftpgo").fetch("quotas", CONFIG, {}, ctx)


@respx.mock
async def test_the_connections(ctx: Context) -> None:
    _server()
    [row] = (await get_adapter("sftpgo").fetch("connections", CONFIG, {}, ctx)).items
    assert row["title"] == "anna" and row["subtitle"] == "SFTP · 192.168.1.20"


@respx.mock
async def test_the_quotas(ctx: Context) -> None:
    """Only users with a limit; 0 means none."""
    _server()
    rows = (await get_adapter("sftpgo").fetch("quotas", CONFIG, {}, ctx)).items
    assert [row["title"] for row in rows] == ["anna", "ben"], "fullest first; carl has no limit"
    assert rows[0]["value"] == "29%" and rows[0]["subtitle"] == "2.9 MB of 10.0 MB"
    assert rows[1]["value"] == "0%", "a user who never uploaded has no used_quota_size"


@respx.mock
async def test_a_wrong_key_and_a_ban(ctx: Context) -> None:
    respx.get(f"{API}/status").mock(side_effect=[httpx.Response(400, json=FIXTURE["wrong_key"]), httpx.Response(403, json=FIXTURE["blocked"]),
                                                 httpx.Response(401, json=FIXTURE["no_key"])])
    adapter = get_adapter("sftpgo")
    for words in ("refused the API key", "banned", "refused the API key"):
        with pytest.raises(AuthFailed) as failure:
            await adapter.fetch("status", CONFIG, {}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
        assert words in failure.value.message
        assert failure.value.hint


@respx.mock
async def test_errors_and_something_else(ctx: Context) -> None:
    respx.get(f"{API}/connections").mock(side_effect=[httpx.Response(500), httpx.Response(200, text="<html>")])
    for code in ("http_error", "not_sftpgo"):
        with pytest.raises(AdapterError) as failure:
            await get_adapter("sftpgo").fetch("connections", CONFIG, {}, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
        assert failure.value.code == code


@respx.mock
async def test_the_connection_test(ctx: Context) -> None:
    _server()
    assert await get_adapter("sftpgo").test(CONFIG, ctx) == "SFTPGo 2.7.6 answers with 1 connection open."


def test_the_demo() -> None:
    adapter = get_adapter("sftpgo")
    for tick in range(10):
        for kind in ("status", "connections", "quotas"):
            assert adapter.demo(kind, {}, tick).status in ("ok", "warn")


@respx.mock
async def test_any_401_is_a_refusal_and_a_test_that_gets_no_list(ctx: Context) -> None:
    respx.get(f"{API}/status").mock(return_value=httpx.Response(401, json={"message": "Unauthorized"}))
    with pytest.raises(AuthFailed):
        await get_adapter("sftpgo").fetch("status", CONFIG, {}, ctx)
    respx.get(f"{API}/version").mock(return_value=httpx.Response(200, json=FIXTURE["version"]))
    respx.get(f"{API}/connections").mock(return_value=httpx.Response(200, json={"not": "a list"}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("sftpgo").test(CONFIG, Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={}))
    assert failure.value.code == "not_sftpgo"
