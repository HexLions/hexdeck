"""Kodi over JSON-RPC.

The answers here are written in the shapes Kodi's JSON-RPC schema
(``methods.json``, ``types.json``, API 14.1.0) gives the methods: an
episode playing, a song paused, nothing at all, and the library counts.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://kodi.local:8080"
CONFIG = {"url": BASE, "username": "kodi", "password": "made-up-password"}
EPISODE = {"type": "episode", "label": "The Long Tide", "title": "The Long Tide", "showtitle": "Harbour Lights", "season": 3, "episode": 4, "id": 812}
SONG = {"type": "song", "label": "Slow Water", "title": "Slow Water", "artist": ["Harbour Brass"], "album": "Low Tide", "id": 77}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _kodi(players: list, item: dict | None = None, speed: int = 1) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)
        sent.append(call)
        results = {
            "Player.GetActivePlayers": players,
            "Player.GetItem": {"item": item or {}},
            "Player.GetProperties": {"speed": speed, "percentage": 37.5, "time": {"hours": 0, "minutes": 16, "seconds": 30, "milliseconds": 0},
                                     "totaltime": {"hours": 0, "minutes": 44, "seconds": 0, "milliseconds": 0}},
            "Player.PlayPause": {"speed": 0}, "Player.Stop": "OK",
            "Application.GetProperties": {"name": "Kodi", "version": {"major": 21, "minor": 2, "revision": "20241203", "tag": "stable"}},
            "VideoLibrary.GetMovies": {"limits": {"start": 0, "end": 1, "total": 412}, "movies": []},
            "VideoLibrary.GetTVShows": {"limits": {"start": 0, "end": 1, "total": 38}, "tvshows": []},
            "VideoLibrary.GetEpisodes": {"limits": {"start": 0, "end": 1, "total": 2904}, "episodes": []},
            "AudioLibrary.GetAlbums": {"limits": {"start": 0, "end": 1, "total": 186}, "albums": []},
        }
        return httpx.Response(200, json={"id": call["id"], "jsonrpc": "2.0", "result": results[call["method"]]})

    respx.post(f"{BASE}/jsonrpc").mock(side_effect=answer)
    return sent


@respx.mock
async def test_an_episode_playing() -> None:
    _kodi([{"playerid": 1, "type": "video", "playertype": "internal"}], EPISODE)
    data = await get_adapter("kodi").fetch("now", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Now playing", "value": "Harbour Lights"}
    assert data.secondary == [{"label": "", "value": "S03E04 · The Long Tide"}, {"label": "Playing", "value": "16:30 / 44:00"}]
    assert [action.id for action in data.actions] == ["pause", "stop"]
    assert data.meta["progress"] == 37.5


@respx.mock
async def test_a_song_paused_and_pressing_play() -> None:
    sent = _kodi([{"playerid": 0, "type": "audio", "playertype": "internal"}], SONG, speed=0)
    data = await get_adapter("kodi").fetch("now", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Paused", "value": "Slow Water"}
    assert data.secondary[0]["value"] == "Harbour Brass · Low Tide"
    assert data.actions[0].id == "play"
    assert await get_adapter("kodi").action("now", "play", {}, CONFIG, {}, _ctx()) == "Playing."
    assert sent[-1] == {"jsonrpc": "2.0", "id": 1, "method": "Player.PlayPause", "params": {"playerid": 0, "play": True}}


@respx.mock
async def test_nothing_playing_and_a_slide_show_is_nothing() -> None:
    _kodi([{"playerid": 2, "type": "picture", "playertype": "internal"}])
    data = await get_adapter("kodi").fetch("now", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Now playing", "value": "Nothing"} and data.actions == []
    with pytest.raises(AdapterError, match="Nothing is playing"):
        await get_adapter("kodi").action("now", "stop", {}, CONFIG, {}, _ctx())


@respx.mock
async def test_the_library() -> None:
    _kodi([])
    data = await get_adapter("kodi").fetch("library", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Movies", "value": 412, "metric": "movies"}
    assert data.secondary == [{"label": "Shows", "value": 38}, {"label": "Episodes", "value": 2904}, {"label": "Albums", "value": 186}]


@respx.mock
async def test_a_wrong_password_and_an_rpc_error() -> None:
    route = respx.post(f"{BASE}/jsonrpc").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthFailed):
        await get_adapter("kodi").test(CONFIG, _ctx())
    assert route.calls[0].request.headers["authorization"].startswith("Basic ")
    route.mock(return_value=httpx.Response(200, json={"id": 1, "jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found."}}))
    with pytest.raises(AdapterError, match="Method not found"):
        await get_adapter("kodi").test(CONFIG, _ctx())


@respx.mock
async def test_the_connection_test_names_the_version() -> None:
    _kodi([])
    assert await get_adapter("kodi").test(CONFIG, _ctx()) == "Kodi 21.2 answers."
