"""Steam: the three read-only calls, and the private profile that answers nothing."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

API = "https://api.steampowered.com"
CONFIG = {"api_key": "k", "account": "76561197960287930"}

SUMMARY = {"response": {"players": [{"personaname": "hexlions", "personastate": 1, "gameextrainfo": "Baldur's Gate 3"}]}}
RECENT = {"response": {"total_count": 2, "games": [
    {"appid": 620, "name": "Portal 2", "playtime_2weeks": 90, "playtime_forever": 1480},
    {"appid": 1086940, "name": "Baldur's Gate 3", "playtime_2weeks": 300, "playtime_forever": 9820},
]}}
OWNED = {"response": {"game_count": 312, "games": [
    {"appid": 620, "playtime_forever": 1480}, {"appid": 1, "playtime_forever": 0},
]}}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _mock(summary: dict[str, Any] = SUMMARY, recent: dict[str, Any] = RECENT, owned: dict[str, Any] = OWNED) -> None:
    respx.get(f"{API}/ISteamUser/GetPlayerSummaries/v2/").mock(return_value=httpx.Response(200, json=summary))
    respx.get(f"{API}/IPlayerService/GetRecentlyPlayedGames/v1/").mock(return_value=httpx.Response(200, json=recent))
    respx.get(f"{API}/IPlayerService/GetOwnedGames/v1/").mock(return_value=httpx.Response(200, json=owned))


@respx.mock
async def test_the_player_card_says_what_is_being_played_now() -> None:
    _mock()
    data = await get_adapter("steam").fetch("player", CONFIG, {}, _ctx())
    assert data.primary["value"] == "In a game"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Playing"] == "Baldur's Gate 3"
    assert labels["Two weeks"] == 6.5, "90 and 300 minutes are 6.5 hours"
    assert data.metrics["hours"] == 6.5


@respx.mock
async def test_offline_is_a_fact_not_a_fault() -> None:
    _mock(summary={"response": {"players": [{"personaname": "hexlions", "personastate": 0}]}})
    data = await get_adapter("steam").fetch("player", CONFIG, {}, _ctx())
    assert data.primary["value"] == "Offline" and data.status == "unknown"


@respx.mock
async def test_the_recent_card_is_ordered_by_the_last_two_weeks() -> None:
    _mock()
    data = await get_adapter("steam").fetch("recent", CONFIG, {}, _ctx())
    assert [item["title"] for item in data.items] == ["Baldur's Gate 3", "Portal 2"]
    assert data.items[0]["value"] == "5.0 h" and "163.7 h in total" == data.items[0]["subtitle"]
    assert data.items[0]["url"].endswith("/app/1086940")
    assert all(not item.get("icon") for item in data.items), "no artwork is fetched from Valve by the browser"


@respx.mock
async def test_the_library_card_counts_what_was_ever_started() -> None:
    _mock()
    data = await get_adapter("steam").fetch("library", CONFIG, {}, _ctx())
    assert data.primary["value"] == 312
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Started"] == "1 of 312" and labels["Hours"] == 24.7


@respx.mock
async def test_a_private_profile_is_said_in_words() -> None:
    _mock(recent={"response": {}}, owned={"response": {}})
    library = await get_adapter("steam").fetch("library", CONFIG, {}, _ctx())
    assert library.status == "unknown" and "private" in library.meta["empty"]
    recent = await get_adapter("steam").fetch("recent", CONFIG, {}, _ctx())
    assert recent.items == [] and "private" in recent.meta["empty"]


@respx.mock
async def test_a_custom_profile_name_is_resolved_once() -> None:
    route = respx.get(f"{API}/ISteamUser/ResolveVanityURL/v1/").mock(
        return_value=httpx.Response(200, json={"response": {"success": 1, "steamid": "76561197960287930"}}))
    _mock()
    ctx = _ctx()
    await get_adapter("steam").fetch("player", {"api_key": "k", "account": "hexlions"}, {}, ctx)
    await get_adapter("steam").fetch("recent", {"api_key": "k", "account": "hexlions"}, {}, ctx)
    assert route.call_count == 1, "the name is kept for a day"


@respx.mock
async def test_a_name_steam_does_not_know_is_named() -> None:
    respx.get(f"{API}/ISteamUser/ResolveVanityURL/v1/").mock(
        return_value=httpx.Response(200, json={"response": {"success": 42, "message": "No match"}}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("steam").fetch("player", {"api_key": "k", "account": "nobody-here"}, {}, _ctx())
    assert failure.value.code == "no_such_account"


@respx.mock
async def test_a_profile_address_pasted_whole_still_works() -> None:
    _mock()
    data = await get_adapter("steam").fetch("player", {"api_key": "k", "account": "https://steamcommunity.com/profiles/76561197960287930/"}, {}, _ctx())
    assert data.primary["value"] == "In a game"


@respx.mock
async def test_a_refused_key_is_an_authentication_failure() -> None:
    respx.get(f"{API}/ISteamUser/GetPlayerSummaries/v2/").mock(return_value=httpx.Response(403, text="Forbidden"))
    respx.get(f"{API}/IPlayerService/GetRecentlyPlayedGames/v1/").mock(return_value=httpx.Response(403, text="Forbidden"))
    with pytest.raises(AuthFailed):
        await get_adapter("steam").fetch("player", CONFIG, {}, _ctx())


@respx.mock
async def test_the_test_button_names_the_player() -> None:
    _mock()
    assert "hexlions" in await get_adapter("steam").test(CONFIG, _ctx())


def test_the_demo_draws() -> None:
    for kind in ("player", "recent", "library"):
        assert get_adapter("steam").demo(kind, {}, 3).primary
