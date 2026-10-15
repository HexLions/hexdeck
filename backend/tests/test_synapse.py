"""Matrix Synapse: users, rooms, reports and federation through the admin API.

``synapse_admin.json`` holds what Synapse 1.163.0 answered locally on
2026-10-15 to a server admin's token: three users, two rooms, one reported
message, and the two refusals. The federation list was empty there; the
three destinations here (one fine, two failing) are written in the shape of
Synapse's admin API documentation.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "https://matrix.example.com"
CONFIG = {"url": BASE, "token": "made-up-token"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "synapse_admin.json").read_text())
ADMIN = f"{BASE}/_synapse/admin"


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _synapse() -> None:
    respx.get(f"{ADMIN}/v1/server_version").mock(return_value=httpx.Response(200, json=ANSWERS["version"]))
    respx.get(f"{ADMIN}/v2/users").mock(return_value=httpx.Response(200, json=ANSWERS["users"]))
    respx.get(f"{ADMIN}/v1/rooms").mock(return_value=httpx.Response(200, json={**ANSWERS["rooms"], "total_rooms": 2}))
    respx.get(f"{ADMIN}/v1/event_reports").mock(return_value=httpx.Response(200, json=ANSWERS["reports"]))
    respx.get(f"{ADMIN}/v1/federation/destinations").mock(return_value=httpx.Response(200, json=ANSWERS["destinations"]))


@respx.mock
async def test_the_server_card() -> None:
    _synapse()
    data = await get_adapter("synapse").fetch("server", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Users", "value": 3, "metric": "users"}
    assert data.secondary == [{"label": "Rooms", "value": 2, "metric": "rooms"}, {"label": "Reports", "value": 1},
                              {"label": "Unreachable", "value": "2/3"}, {"label": "Version", "value": "1.163.0"}]
    assert data.status == "warn"
    destinations = next(call.request for call in respx.calls if "destinations" in call.request.url.path)
    assert "order_by" not in destinations.url.params, "ordered by name only, as Synapse's documentation advises"


@respx.mock
async def test_the_rooms_by_members() -> None:
    _synapse()
    data = await get_adapter("synapse").fetch("rooms", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in data.items] == [("Homelab", 2), ("Family", 1)]
    assert data.items[0]["subtitle"] == "#homelab:matrix.example.com"


@respx.mock
async def test_the_servers_it_cannot_reach_oldest_failure_first() -> None:
    _synapse()
    data = await get_adapter("synapse").fetch("federation", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["old.example.org", "chat.example.net"]
    assert all(row["subtitle"].startswith("failing for") for row in data.items)


@respx.mock
@pytest.mark.parametrize(("status", "body", "words"), [(401, "unknown_token", "does not know"), (403, "not_admin", "not a server admin")])
async def test_refusals(status: int, body: str, words: str) -> None:
    respx.get(f"{ADMIN}/v1/server_version").mock(return_value=httpx.Response(200, json=ANSWERS["version"]))
    respx.get(f"{ADMIN}/v2/users").mock(return_value=httpx.Response(status, json=ANSWERS[body]))
    with pytest.raises(AuthFailed, match=words):
        await get_adapter("synapse").test(CONFIG, _ctx())
