"""One list of everything that has a newer version: the containers WUD and Cup
watch, the releases of the repositories somebody follows, and HexDeck itself.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.adapters import get_adapter
from app.adapters.base import Context

from .conftest import CSRF, setup_admin

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
    data = await get_adapter("core").fetch("updates", {}, {"sources": [7], "repos": "o/r", "services": "none", "hexdeck": False}, _ctx(resolve))
    titles = [item["title"] for item in data.items]
    assert "radarr" in titles, "a container with a newer image"
    assert "sonarr" not in titles, "one that is up to date is not news"
    assert any("o/r" in title for title in titles), "a release of a repository that is followed"
    radarr = next(item for item in data.items if item["title"] == "radarr")
    assert radarr["value"] == "5.2.0 → 5.3.0" and radarr["status"] == "warn"
    assert data.primary["value"] == len(data.items)


def _asking(client: TestClient) -> None:
    """The operator's switch under System, About: without it GitHub is never asked."""
    setup_admin(client)
    assert client.patch("/api/v1/settings", json={"update_check": True}, headers=CSRF).status_code == 200


@respx.mock
async def test_hexdeck_says_when_it_is_behind(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import system

    _asking(client)

    async def newer(force: bool = False) -> str:
        return "99.0.0"

    monkeypatch.setattr(system, "latest_version", newer)
    data = await get_adapter("core").fetch("updates", {}, {"hexdeck": True}, _ctx(None))
    assert [item["title"] for item in data.items] == ["HexDeck"]
    assert data.items[0]["value"].endswith("99.0.0")
    assert data.status == "warn"


@respx.mock
async def test_nothing_to_do_is_said_plainly(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers import system

    _asking(client)

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
    data = await get_adapter("core").fetch("updates", {}, {"sources": [9], "repos": "o/r", "services": "none", "hexdeck": False}, _ctx(resolve))
    assert [item["title"] for item in data.items] == ["o/r"], "the repository still comes through"
    assert any("Cup" in warning for warning in data.meta["failures"])
    assert data.status == "warn"


def test_the_demo_draws() -> None:
    assert get_adapter("core").demo("updates", {}, 0).items


async def test_hexdeck_asks_github_nothing_while_the_switch_is_off(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ The card used to ask GitHub whatever the switch under System said."""
    from app.routers import system

    async def never(force: bool = False) -> str:
        raise AssertionError("GitHub was asked with the switch off")

    setup_admin(client)
    monkeypatch.setattr(system, "latest_version", never)
    data = await get_adapter("core").fetch("updates", {}, {"hexdeck": True}, _ctx(None))
    assert data.items == [] and not data.meta["failures"]


async def test_a_card_asks_the_connected_services_unless_told_not_to(client: TestClient) -> None:
    """Every connection that names its version is a source by default; without any the card is up to date."""
    _asking(client)
    from app.services.collector import collector

    ctx = Context(collector.client, widget_id=1, cache={}, resolve_integration=collector.resolve_integration)
    card = await get_adapter("core").fetch("updates", {}, {"hexdeck": False}, ctx)
    assert card.items == [] and not card.meta["failures"]


# -- Arcane and Dockhand as container sources, from nexdeck (issue #51) ------------------------


class _Source:
    """A connection whose image updates are already read: Arcane's or Dockhand's own card data."""

    def __init__(self, kind: str, data: Any) -> None:
        adapter = get_adapter(kind)
        self.kind, self.label, self.icon, self.data, self.asked = kind, adapter.label, adapter.icon, data, []

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> Any:
        self.asked.append(widget_kind)
        return self.data


def _sources(sources: dict[int, _Source]):
    async def resolve(integration_id: int):
        return sources[integration_id], {}, _ctx(None)

    return resolve


async def test_arcane_and_dockhand_bring_their_image_updates_without_buttons() -> None:
    arcane = get_adapter("arcane").demo("updates", {}, 0)
    dockhand = get_adapter("dockhand").demo("updates", {}, 0)
    assert arcane.items and dockhand.items, "the demo of each lists an update, or this test proves nothing"
    assert any(row.get("actions") for row in arcane.items), "Arcane's own card has buttons; they must stay behind"
    sources = {1: _Source("arcane", arcane), 2: _Source("dockhand", dockhand)}
    card = await get_adapter("core").fetch("updates", {}, {"sources": ["1", "2"], "services": "none", "hexdeck": False, "limit": 50}, _ctx(_sources(sources)))
    assert sources[1].asked == ["updates"] and sources[2].asked == ["updates"]
    assert len(card.items) == len(arcane.items) + len(dockhand.items)
    assert all("actions" not in row for row in card.items)
    assert {row["source"] for row in card.items} == {"Arcane", "Dockhand"}
    assert card.status == "warn"


async def test_a_source_that_has_not_checked_says_so_instead_of_up_to_date() -> None:
    from app.adapters.arcane import ArcaneAdapter
    from app.adapters.dockhand import DockhandAdapter

    arcane = ArcaneAdapter._update_rows([({"id": "0", "name": "Local"}, [{"id": "a", "names": ["web"], "image": "nginx"}])], False, False)
    dockhand = DockhandAdapter._update_rows([({"id": 1, "name": "Local", "updateCheckEnabled": False}, {"pendingUpdates": []})], False, False)
    assert arcane.status == dockhand.status == "unknown"
    sources = {1: _Source("arcane", arcane), 2: _Source("dockhand", dockhand)}
    card = await get_adapter("core").fetch("updates", {}, {"sources": ["1", "2"], "services": "none", "hexdeck": False}, _ctx(_sources(sources)))
    assert [(row["title"], row["status"]) for row in card.items] == [("Arcane", "unknown"), ("Dockhand", "unknown")]
    assert card.items[0]["subtitle"] == "Arcane has not checked these images for updates yet."
    assert card.status == "ok", "not checked is no reason to colour the card"
    checked = DockhandAdapter._update_rows([({"id": 1, "name": "Local", "updateCheckEnabled": True}, {"pendingUpdates": []})], False, False)
    quiet = await get_adapter("core").fetch("updates", {}, {"sources": ["1"], "services": "none", "hexdeck": False}, _ctx(_sources({1: _Source("dockhand", checked)})))
    assert quiet.items == [], "checked and nothing waiting is simply up to date"


async def test_environments_that_did_not_answer_are_a_line() -> None:
    from app.adapters.base import WidgetData

    partial = WidgetData(status="warn", items=[], meta={"notice": "Some environments did not answer; their rows are missing.", "empty": "Everything is up to date."})
    card = await get_adapter("core").fetch("updates", {}, {"sources": ["1"], "services": "none", "hexdeck": False}, _ctx(_sources({1: _Source("arcane", partial)})))
    assert [(row["title"], row["subtitle"], row["status"]) for row in card.items] == [("Arcane", "Some environments did not answer; their rows are missing.", "warn")]


def test_the_card_offers_arcane_and_dockhand_as_sources() -> None:
    field = next(one for one in get_adapter("core").widget("updates").options if one.name == "sources")
    assert {"arcane", "dockhand"} <= {kind for kind, _label in field.options}
