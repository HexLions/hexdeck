"""One list of everything that has a newer version: the containers WUD and Cup
watch, the releases of the repositories somebody follows, and HexDeck itself.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import Context

WUD = "http://wud.lan:3000"
CUP = "http://cup.lan:8000"
GITHUB = "https://api.github.com"


def _ctx(resolve) -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=resolve)


async def _resolver(kinds: dict[int, tuple[str, dict[str, Any]]]):
    async def resolve(integration_id: int):
        kind, config = kinds[integration_id]
        return get_adapter(kind), config, _ctx(None)

    return resolve


@respx.mock
async def test_the_card_merges_the_containers_the_repositories_and_hexdeck_itself() -> None:
    respx.get(f"{WUD}/api/containers").mock(return_value=httpx.Response(200, json=[
        {"id": "a", "name": "radarr", "image": {"name": "linuxserver/radarr", "tag": {"value": "5.2.0"}},
         "updateKind": {"kind": "tag", "semverDiff": "minor", "localValue": "5.2.0", "remoteValue": "5.3.0"}, "updateAvailable": True},
        {"id": "b", "name": "sonarr", "image": {"name": "linuxserver/sonarr", "tag": {"value": "4.0.0"}}, "updateAvailable": False},
    ]))
    respx.get(url__regex=rf"{WUD}/api/containers/.*/triggers").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{GITHUB}/repos/o/r/releases/latest").mock(return_value=httpx.Response(200, json={
        "tag_name": "v1.2.0", "name": "1.2.0", "html_url": "https://github.com/o/r/releases/v1.2.0", "published_at": "2026-09-20T10:00:00Z", "prerelease": False,
    }, headers={"X-RateLimit-Remaining": "42"}))
    resolve = await _resolver({7: ("wud", {"url": WUD, "token": "t"})})
    data = await get_adapter("core").fetch("updates", {}, {"sources": [7], "repos": "o/r"}, _ctx(resolve))
    titles = [item["title"] for item in data.items]
    assert "radarr" in titles, "a container with a newer image"
    assert "sonarr" not in titles, "one that is up to date is not news"
    assert any("o/r" in title for title in titles), "a release of a repository that is followed"
    radarr = next(item for item in data.items if item["title"] == "radarr")
    assert radarr["value"] == "5.2.0 → 5.3.0" and radarr["status"] == "warn"
    assert data.primary["value"] == len(data.items)


@respx.mock
async def test_hexdeck_says_when_it_is_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import system

    async def newer(force: bool = False) -> str:
        return "99.0.0"

    monkeypatch.setattr(system, "latest_version", newer)
    data = await get_adapter("core").fetch("updates", {}, {"hexdeck": True}, _ctx(None))
    assert [item["title"] for item in data.items] == ["HexDeck"]
    assert data.items[0]["value"].endswith("99.0.0")
    assert data.status == "warn"


@respx.mock
async def test_nothing_to_do_is_said_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import system

    async def same(force: bool = False) -> str | None:
        return None

    monkeypatch.setattr(system, "latest_version", same)
    data = await get_adapter("core").fetch("updates", {}, {"hexdeck": True}, _ctx(None))
    assert data.items == [] and data.status == "ok"
    assert "up to date" in str(data.meta.get("empty", "")).lower()


@respx.mock
async def test_a_source_that_will_not_answer_does_not_take_the_card_with_it() -> None:
    respx.get(f"{CUP}/api/v3/json").mock(return_value=httpx.Response(500, text="boom"))
    respx.get(f"{GITHUB}/repos/o/r/releases/latest").mock(return_value=httpx.Response(200, json={
        "tag_name": "v2.0.0", "name": "2.0.0", "html_url": "u", "published_at": "2026-09-20T10:00:00Z", "prerelease": False,
    }, headers={"X-RateLimit-Remaining": "42"}))
    resolve = await _resolver({9: ("cup", {"url": CUP})})
    data = await get_adapter("core").fetch("updates", {}, {"sources": [9], "repos": "o/r"}, _ctx(resolve))
    assert [item["title"] for item in data.items] == ["o/r"], "the repository still comes through"
    assert any("Cup" in warning for warning in data.meta["failures"])
    assert data.status == "warn"


def test_the_demo_draws() -> None:
    assert get_adapter("core").demo("updates", {}, 0).items
