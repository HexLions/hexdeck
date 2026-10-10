"""Caddy: the sites, the upstreams, and what the admin API does and does not say.

fixtures/caddy_servers.json is the answer of /config/apps/http/servers from
Caddy v2.11.7 running locally on 2026-10-09, with three sites behind its
proxy and one fixed response. The upstream answers below are the shape the
same instance gave before and after a dead upstream was asked for.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.caddy import sites

FIXTURES = Path(__file__).parent / "fixtures"
SERVERS = json.loads((FIXTURES / "caddy_servers.json").read_text())
ADMIN = "http://caddy:2019"
CONFIG = {"url": ADMIN}
HEALTHY = [{"address": "127.0.0.1:8099", "num_requests": 0, "fails": 0},
           {"address": "127.0.0.1:8098", "num_requests": 0, "fails": 0},
           {"address": "127.0.0.1:8092", "num_requests": 2, "fails": 0}]
FAILING = [{"address": "127.0.0.1:8099", "num_requests": 0, "fails": 1},
           {"address": "127.0.0.1:8098", "num_requests": 0, "fails": 1},
           {"address": "127.0.0.1:8092", "num_requests": 0, "fails": 0}]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _caddy(upstreams: list | None = None) -> None:
    respx.get(f"{ADMIN}/config/apps/http/servers").mock(return_value=httpx.Response(200, json=SERVERS))
    respx.get(f"{ADMIN}/reverse_proxy/upstreams").mock(return_value=httpx.Response(200, json=upstreams if upstreams is not None else HEALTHY))


def test_the_sites_are_read_through_subroutes_one_per_host() -> None:
    """A Caddyfile puts each site's proxy inside a subroute; both hosts of one block are sites."""
    found = {one["host"]: one for one in sites(SERVERS)}
    assert set(found) == {"static.localhost", "dead.localhost", "old.localhost", "app.localhost"}
    assert found["app.localhost"]["kind"] == "proxy" and found["app.localhost"]["upstreams"] == ["127.0.0.1:8092"]
    assert found["dead.localhost"]["upstreams"] == ["127.0.0.1:8099", "127.0.0.1:8098"]
    assert found["static.localhost"]["kind"] == "static"


def test_a_route_without_a_host_is_named_after_its_server() -> None:
    found = sites({"srv1": {"listen": [":8080"], "routes": [{"handle": [{"handler": "file_server"}]}]}})
    assert found == [{"host": "srv1 (:8080)", "server": "srv1", "listen": [":8080"], "kind": "files", "upstreams": []}]


@respx.mock
async def test_upstreams_with_remembered_failures_are_yellow_not_down() -> None:
    """⚠️ Caddy reports failures, not availability; the card does not call them down."""
    _caddy(FAILING)
    data = await get_adapter("caddy").fetch("upstreams", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"], row["status"]) for row in data.items] == [
        ("127.0.0.1:8098", "1 failed", "warn"), ("127.0.0.1:8099", "1 failed", "warn"), ("127.0.0.1:8092", "0 active", "ok")]
    assert data.items[0]["subtitle"] == "dead.localhost, old.localhost"
    assert data.status == "warn" and data.metrics == {"failing": 2.0}


@respx.mock
async def test_a_site_whose_every_upstream_fails_is_red() -> None:
    _caddy(FAILING)
    data = await get_adapter("caddy").fetch("sites", CONFIG, {}, _ctx())
    first = data.items[0]
    assert first["title"] in ("dead.localhost", "old.localhost")
    assert first["value"] == "2 of 2 failing" and first["status"] == "bad"
    assert data.status == "bad"
    static = next(row for row in data.items if row["title"] == "static.localhost")
    assert static["value"] == "static" and static["status"] == "ok"


@respx.mock
async def test_the_summary_counts_sites_upstreams_and_active_requests() -> None:
    _caddy(HEALTHY)
    data = await get_adapter("caddy").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Sites", "value": 4}
    assert {row["label"]: row["value"] for row in data.secondary} == {"Upstreams": 3, "Active requests": 2}
    assert data.status == "ok"


@respx.mock
async def test_only_get_is_ever_sent() -> None:
    """The admin API can rewrite the configuration; this adapter must not."""
    _caddy()
    adapter = get_adapter("caddy")
    for widget in adapter.widgets:
        await adapter.fetch(widget.kind, CONFIG, {}, _ctx())
    assert {call.request.method for call in respx.calls} == {"GET"}


@respx.mock
async def test_basic_authentication_is_sent_only_when_a_user_is_named() -> None:
    _caddy()
    await get_adapter("caddy").fetch("summary", {**CONFIG, "username": "hex", "password": "pw"}, {}, _ctx())
    assert respx.calls[0].request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_an_empty_configuration_has_no_sites_rather_than_an_error() -> None:
    respx.get(f"{ADMIN}/config/apps/http/servers").mock(return_value=httpx.Response(404))
    respx.get(f"{ADMIN}/reverse_proxy/upstreams").mock(return_value=httpx.Response(200, json=[]))
    data = await get_adapter("caddy").fetch("sites", CONFIG, {}, _ctx())
    assert data.items == [] and data.meta["empty"] == "Caddy serves no site over HTTP"


@respx.mock
async def test_a_refusal_is_said_as_one() -> None:
    respx.get(f"{ADMIN}/config/apps/http/servers").mock(return_value=httpx.Response(403))
    with pytest.raises(AuthFailed):
        await get_adapter("caddy").fetch("sites", CONFIG, {}, _ctx())


@respx.mock
async def test_a_site_instead_of_the_admin_api_is_named() -> None:
    respx.get(f"{ADMIN}/config/apps/http/servers").mock(return_value=httpx.Response(200, text="<html>hello</html>"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("caddy").fetch("sites", CONFIG, {}, _ctx())
    assert failure.value.code == "not_json"


@respx.mock
async def test_the_connection_test_counts_sites_and_upstreams() -> None:
    _caddy()
    assert await get_adapter("caddy").test(CONFIG, _ctx()) == "Caddy answers with 4 sites and 3 upstreams."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("caddy")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
