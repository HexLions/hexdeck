"""Cloudflare: tunnels, zones, a day of traffic, and a refusal that comes as 400.

The answers below follow Cloudflare's OpenAPI schema (cloudflare/api-schemas)
and the GraphQL Analytics API documentation as of 2026-10-08. The refusals were
measured against api.cloudflare.com with a made-up token the same day.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

API = "https://api.cloudflare.com/client/v4"
CONFIG = {"token": "made-up-token"}

ZONES = [
    {"id": "z1", "name": "example.com", "status": "active", "paused": False, "type": "full",
     "plan": {"name": "Free Website"}, "account": {"id": "acc1", "name": "Home"}},
    {"id": "z2", "name": "example.net", "status": "active", "paused": True, "type": "full",
     "plan": {"name": "Free Website"}, "account": {"id": "acc1", "name": "Home"}},
    {"id": "z3", "name": "example.org", "status": "moved", "paused": False, "type": "full",
     "plan": {"name": "Pro Website"}, "account": {"id": "acc1", "name": "Home"}},
]
TUNNELS = [
    {"id": "t1", "name": "homelab", "status": "healthy", "tun_type": "cfd_tunnel", "deleted_at": None, "connections": [
        {"colo_name": "fra06", "client_version": "2026.9.1"}, {"colo_name": "zrh01", "client_version": "2026.10.0"}]},
    {"id": "t2", "name": "nas", "status": "down", "tun_type": "cfd_tunnel", "deleted_at": None, "connections": [],
     "conns_inactive_at": "2026-10-08T07:00:00Z"},
    {"id": "t3", "name": "office", "status": "degraded", "tun_type": "cfd_tunnel", "deleted_at": None,
     "connections": [{"colo_name": "mil01", "client_version": "2026.9.1"}]},
    {"id": "t4", "name": "test", "status": "inactive", "tun_type": "cfd_tunnel", "deleted_at": None, "connections": []},
]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _ok(result: object) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "errors": [], "messages": [], "result": result})


def _cloudflare(graphql: dict | None = None) -> None:
    respx.get(f"{API}/zones").mock(return_value=_ok(ZONES))
    respx.get(f"{API}/accounts/acc1/cfd_tunnel").mock(return_value=_ok(TUNNELS))
    respx.get(f"{API}/accounts/typed/cfd_tunnel").mock(return_value=_ok(TUNNELS[:1]))
    respx.post(f"{API}/graphql").mock(return_value=httpx.Response(200, json=graphql or {
        "data": {"viewer": {"zones": [{"requests": [{"count": 48231}], "security": [{"count": 312}]}]}}, "errors": None}))


@respx.mock
async def test_the_token_goes_in_as_bearer() -> None:
    _cloudflare()
    await get_adapter("cloudflare").fetch("zones", CONFIG, {}, _ctx())
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-token"


@respx.mock
async def test_the_tunnels_put_the_one_that_is_down_first_and_leave_out_the_one_never_run() -> None:
    _cloudflare()
    data = await get_adapter("cloudflare").fetch("tunnels", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["nas", "office", "homelab"]
    assert [row["status"] for row in data.items] == ["bad", "warn", "ok"]
    assert data.status == "bad" and data.primary["value"] == "1 / 3"
    assert data.metrics == {"down": 1.0}


@respx.mock
async def test_a_tunnel_names_its_connections_data_centres_and_newest_cloudflared() -> None:
    """⚠️ cloudflared numbers itself by date: 2026.10.0 is newer than 2026.9.1."""
    _cloudflare()
    data = await get_adapter("cloudflare").fetch("tunnels", CONFIG, {}, _ctx())
    homelab = next(row for row in data.items if row["title"] == "homelab")
    assert homelab["subtitle"] == "2 connections · fra06, zrh01 · cloudflared 2026.10.0"


@respx.mock
async def test_a_tunnel_that_never_ran_shows_when_asked() -> None:
    _cloudflare()
    data = await get_adapter("cloudflare").fetch("tunnels", CONFIG, {"hide_inactive": False}, _ctx())
    assert data.items[-1]["title"] == "test" and data.items[-1]["status"] == "unknown"


@respx.mock
async def test_the_account_is_taken_from_the_first_zone_when_none_is_typed() -> None:
    _cloudflare()
    await get_adapter("cloudflare").fetch("tunnels", CONFIG, {}, _ctx())
    assert respx.calls[-1].request.url.path == "/client/v4/accounts/acc1/cfd_tunnel"
    assert respx.calls[-1].request.url.params["is_deleted"] == "false"


@respx.mock
async def test_a_typed_account_is_used_without_reading_the_zones() -> None:
    _cloudflare()
    await get_adapter("cloudflare").fetch("tunnels", {**CONFIG, "account_id": "typed"}, {}, _ctx())
    assert [call.request.url.path for call in respx.calls] == ["/client/v4/accounts/typed/cfd_tunnel"]


@respx.mock
async def test_the_zones_mark_a_paused_one_and_a_moved_one() -> None:
    _cloudflare()
    data = await get_adapter("cloudflare").fetch("zones", CONFIG, {}, _ctx())
    assert [(row["value"], row["status"]) for row in data.items] == [("active", "ok"), ("paused", "warn"), ("moved", "bad")]
    assert data.items[0]["subtitle"] == "Free Website · full"
    assert data.status == "bad"


@respx.mock
async def test_the_traffic_card_asks_graphql_for_the_last_day_of_one_zone() -> None:
    _cloudflare()
    data = await get_adapter("cloudflare").fetch("traffic", CONFIG, {"zone": "example.net"}, _ctx())
    sent = json.loads(respx.calls[-1].request.content)
    assert sent["variables"]["zoneTag"] == "z2"
    assert "httpRequestsAdaptiveGroups" in sent["query"] and "firewallEventsAdaptiveGroups" in sent["query"]
    assert data.primary["value"] == 48231
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels == {"Security events": 312, "Zone": "example.net"}


@respx.mock
async def test_a_day_without_traffic_is_nought_not_unknown() -> None:
    _cloudflare({"data": {"viewer": {"zones": [{"requests": [], "security": []}]}}, "errors": None})
    data = await get_adapter("cloudflare").fetch("traffic", CONFIG, {}, _ctx())
    assert data.primary["value"] == 0


@respx.mock
async def test_a_graphql_refusal_in_a_200_is_still_a_refusal() -> None:
    """⚠️ GraphQL answers 200 with errors and no data when the permission is missing."""
    _cloudflare({"data": None, "errors": [{"message": "not authorized for that account"}]})
    with pytest.raises(AdapterError) as failure:
        await get_adapter("cloudflare").fetch("traffic", CONFIG, {}, _ctx())
    assert failure.value.code == "analytics_refused"


@respx.mock
async def test_an_unreadable_token_comes_back_as_400_and_is_said_as_a_refused_token() -> None:
    """Measured: a made-up token gets 400 with 6003/6111 on REST and 9106 on GraphQL."""
    respx.get(f"{API}/zones").mock(return_value=httpx.Response(400, json={
        "success": False, "errors": [{"code": 6003, "message": "Invalid request headers",
                                      "error_chain": [{"code": 6111, "message": "Invalid format for Authorization header"}]}],
        "messages": [], "result": None}))
    with pytest.raises(AuthFailed):
        await get_adapter("cloudflare").fetch("zones", CONFIG, {}, _ctx())
    respx.post(f"{API}/graphql").mock(return_value=httpx.Response(400, json={
        "success": False, "errors": [{"code": 9106, "message": "Authentication failed (status: 400)"}], "messages": [], "result": None}))
    respx.get(f"{API}/zones").mock(return_value=_ok(ZONES))
    with pytest.raises(AuthFailed):
        await get_adapter("cloudflare").fetch("traffic", CONFIG, {}, _ctx())


@respx.mock
async def test_an_unknown_zone_is_named() -> None:
    _cloudflare()
    with pytest.raises(AdapterError) as failure:
        await get_adapter("cloudflare").fetch("traffic", CONFIG, {"zone": "nowhere.example"}, _ctx())
    assert failure.value.code == "no_zone"


@respx.mock
async def test_the_connection_test_counts_the_zones() -> None:
    _cloudflare()
    assert await get_adapter("cloudflare").test(CONFIG, _ctx()) == "Cloudflare answers with 3 zones."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("cloudflare")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
