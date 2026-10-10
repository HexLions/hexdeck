"""Jackett: the indexers, the failures behind the login, and a refusal that comes as 200.

The answers below are what Jackett v0.24.2813 gave on 2026-10-10, running
locally with two public indexers configured and tested, both failing: the
Torznab t=indexers list (shortened to the fields read), the admin list with
last_error, Torznab's error for a wrong key, and the login, which hands out
a session at the end of its redirects or for the admin password.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context
from app.adapters.jackett import _reason, parse_indexers

URL = "http://jackett:9117"
CONFIG = {"url": URL, "api_key": "made-up-key"}
TORZNAB = """<?xml version="1.0" encoding="UTF-8"?>
<indexers>
  <indexer id="1337x" configured="true">
    <title>1337x</title>
    <description>1337x is a Public torrent site that offers verified torrent downloads</description>
    <link>https://1337x.to/</link>
    <language>en-US</language>
    <type>public</type>
    <caps><server title="Jackett" /><limits default="100" max="100" /></caps>
  </indexer>
  <indexer id="anisource" configured="true">
    <title>AniSource</title>
    <language>en-US</language>
    <type>public</type>
  </indexer>
  <indexer id="iptorrents" configured="true">
    <title>IPTorrents</title>
    <language>en-US</language>
    <type>private</type>
  </indexer>
</indexers>"""
WRONG_KEY = '<?xml version="1.0" encoding="UTF-8"?>\n<error code="100" description="Invalid API Key" />'
ADMIN = [
    {"id": "1337x", "name": "1337x", "type": "public", "configured": True, "language": "en-US",
     "last_error": "Exception (1337x): Challenge detected but FlareSolverr is not configured: Challenge detected but FlareSolverr is not configured"},
    {"id": "anisource", "name": "AniSource", "type": "public", "configured": True, "language": "en-US",
     "last_error": "Exception (anisource): Network is unreachable: Network is unreachable (asnet.pw:443)"},
    {"id": "iptorrents", "name": "IPTorrents", "type": "private", "configured": True, "language": "en-US", "last_error": ""},
]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _jackett(password: str | None = None) -> dict:
    """Jackett with its login: the admin list answers only once a session cookie was handed out."""
    state = {"logins": []}
    respx.get(f"{URL}/api/v2.0/indexers/all/results/torznab/api").mock(return_value=httpx.Response(200, text=TORZNAB))

    def admin(request: httpx.Request) -> httpx.Response:
        if "Jackett=session" in request.headers.get("cookie", ""):
            return httpx.Response(200, json=ADMIN)
        return httpx.Response(302, headers={"Location": f"{URL}/UI/Login?ReturnUrl=%2Fapi%2Fv2.0%2Findexers"})

    def dashboard_get(request: httpx.Request) -> httpx.Response:
        state["logins"].append("get")
        if password is None:
            return httpx.Response(200, text="<html>dashboard</html>", headers={"Set-Cookie": "Jackett=session; path=/"})
        return httpx.Response(302, headers={"Location": f"{URL}/UI/Login"})

    def dashboard_post(request: httpx.Request) -> httpx.Response:
        state["logins"].append("post")
        if password is not None and request.content.decode() == f"password={password}":
            return httpx.Response(302, headers={"Location": "Dashboard", "Set-Cookie": "Jackett=session; path=/"})
        return httpx.Response(302, headers={"Location": "Dashboard"})

    respx.get(f"{URL}/api/v2.0/indexers").mock(side_effect=admin)
    respx.get(f"{URL}/UI/Dashboard").mock(side_effect=dashboard_get)
    respx.get(f"{URL}/UI/Login").mock(return_value=httpx.Response(200, text="<html>login</html>"))
    respx.post(f"{URL}/UI/Dashboard").mock(side_effect=dashboard_post)
    return state


def test_the_torznab_list_is_read_with_type_and_language() -> None:
    assert parse_indexers(TORZNAB) == [
        {"id": "1337x", "title": "1337x", "type": "public", "language": "en-US"},
        {"id": "anisource", "title": "AniSource", "type": "public", "language": "en-US"},
        {"id": "iptorrents", "title": "IPTorrents", "type": "private", "language": "en-US"},
    ]


def test_a_wrong_key_comes_as_200_and_is_still_a_refusal() -> None:
    """⚠️ Torznab answers a wrong key with HTTP 200 and error 100 in the body."""
    with pytest.raises(AuthFailed):
        parse_indexers(WRONG_KEY)


def test_jacketts_own_prefix_and_doubled_message_are_cut() -> None:
    assert _reason(ADMIN[0]["last_error"], "1337x") == "Challenge detected but FlareSolverr is not configured"
    assert _reason(ADMIN[1]["last_error"], "anisource") == "Network is unreachable: Network is unreachable (asnet.pw:443)"


@respx.mock
async def test_without_an_admin_password_the_session_comes_from_the_dashboard() -> None:
    state = _jackett(password=None)
    adapter = get_adapter("jackett")
    ctx = _ctx()
    data = await adapter.fetch("indexers", CONFIG, {}, ctx)
    assert state["logins"] == ["get"]
    assert [(row["title"], row["status"]) for row in data.items] == [("1337x", "bad"), ("AniSource", "bad"), ("IPTorrents", "ok")]
    assert data.items[0]["subtitle"] == "Challenge detected but FlareSolverr is not configured"
    assert data.items[2]["subtitle"] == "private · en-US"
    assert data.status == "warn"
    await ctx.cache["jackett_client"].aclose()


@respx.mock
async def test_the_admin_password_is_posted_to_the_login_form() -> None:
    state = _jackett(password="secret")
    ctx = _ctx()
    data = await get_adapter("jackett").fetch("summary", {**CONFIG, "admin_password": "secret"}, {}, ctx)
    assert state["logins"] == ["post"]
    assert {row["label"]: row["value"] for row in data.secondary} == {"Failing": 2, "Private": 1, "Public": 2}
    await ctx.cache["jackett_client"].aclose()


@respx.mock
async def test_a_wrong_admin_password_is_said_as_one() -> None:
    _jackett(password="secret")
    ctx = _ctx()
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("jackett").fetch("summary", {**CONFIG, "admin_password": "nope"}, {}, ctx)
    assert "admin password" in failure.value.message
    await ctx.cache["jackett_client"].aclose()


@respx.mock
async def test_with_a_password_set_and_none_given_the_list_shows_without_failures() -> None:
    _jackett(password="secret")
    ctx = _ctx()
    data = await get_adapter("jackett").fetch("indexers", CONFIG, {}, ctx)
    assert all(row["status"] == "ok" for row in data.items)
    assert data.meta["notice"] == "Which indexers fail needs Jackett's admin password."
    summary = await get_adapter("jackett").fetch("summary", CONFIG, {}, ctx)
    assert "Failing" not in {row["label"] for row in summary.secondary}
    await ctx.cache["jackett_client"].aclose()


@respx.mock
async def test_the_api_key_goes_into_the_torznab_query() -> None:
    _jackett(password=None)
    ctx = _ctx()
    await get_adapter("jackett").fetch("summary", CONFIG, {}, ctx)
    torznab = next(call for call in respx.calls if call.request.url.path.endswith("/torznab/api")).request.url.params
    assert torznab["apikey"] == "made-up-key" and torznab["t"] == "indexers" and torznab["configured"] == "true"
    await ctx.cache["jackett_client"].aclose()


@respx.mock
async def test_only_the_failing_ones_when_asked() -> None:
    _jackett(password=None)
    ctx = _ctx()
    data = await get_adapter("jackett").fetch("indexers", CONFIG, {"failing_only": True}, ctx)
    assert [row["title"] for row in data.items] == ["1337x", "AniSource"]
    await ctx.cache["jackett_client"].aclose()


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("jackett")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
