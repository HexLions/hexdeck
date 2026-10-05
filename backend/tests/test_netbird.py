"""NetBird: the machines, the keys, and the word the token goes in with.

The answers below follow NetBird's own OpenAPI spec at v0.80.0
(shared/management/http/api/openapi.yml).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

NB = "https://api.netbird.io"
CONFIG = {"url": NB, "token": "nbp_made_up_token"}


def _stamp(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat().replace("+00:00", "Z")


PEERS = [
    {"id": "a", "hostname": "nas", "ip": "100.84.0.3", "os": "Debian 13", "version": "0.80.0",
     "connected": True, "last_seen": _stamp(0), "groups": [{"id": "g1", "name": "servers"}]},
    {"id": "b", "hostname": "laptop", "ip": "100.84.0.9", "os": "macOS 15", "version": "0.78.1",
     "connected": False, "last_seen": _stamp(-9), "login_expired": True,
     "groups": [{"id": "g2", "name": "workstations"}]},
    {"id": "c", "hostname": "pi", "ip": "100.84.0.12", "os": "Raspberry Pi OS", "version": "0.80.0",
     "connected": False, "last_seen": _stamp(-1.5), "groups": [{"id": "g1", "name": "servers"}]},
    {"id": "d", "hostname": "newphone", "ip": "100.84.0.20", "os": "Android 16", "version": "0.80.0",
     "connected": True, "last_seen": _stamp(0), "approval_required": True, "groups": []},
]
KEYS = [
    {"id": "k1", "name": "servers", "state": "valid", "type": "reusable", "used_times": 4,
     "usage_limit": 0, "expires": _stamp(120), "valid": True, "revoked": False},
    {"id": "k2", "name": "one laptop", "state": "valid", "type": "one-off", "used_times": 0,
     "usage_limit": 1, "expires": _stamp(6), "valid": True, "revoked": False},
    {"id": "k3", "name": "old demo", "state": "expired", "type": "reusable", "used_times": 11,
     "usage_limit": 20, "expires": _stamp(-30), "valid": False, "revoked": False},
    {"id": "k4", "name": "withdrawn", "state": "revoked", "type": "reusable", "used_times": 2,
     "usage_limit": 0, "expires": _stamp(90), "valid": False, "revoked": True},
]
VERSION = {"management_current_version": "0.80.0", "management_available_version": "0.80.0",
           "dashboard_available_version": "2.20.0", "management_update_available": False}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _netbird(peers: list | None = None, keys: list | None = None, version: dict | None = VERSION) -> None:
    respx.get(f"{NB}/api/peers").mock(return_value=httpx.Response(200, json=peers if peers is not None else PEERS))
    respx.get(f"{NB}/api/setup-keys").mock(return_value=httpx.Response(200, json=keys if keys is not None else KEYS))
    respx.get(f"{NB}/api/instance/version").mock(
        return_value=httpx.Response(200, json=version) if version is not None else httpx.Response(404, text="Not Found"))


@respx.mock
async def test_the_token_goes_in_with_the_word_token_not_bearer() -> None:
    """⚠️ A personal access token sent as Bearer is refused, and the message
    does not say which word was wrong."""
    _netbird()
    await get_adapter("netbird").fetch("peers", CONFIG, {}, _ctx())
    assert respx.calls[0].request.headers["authorization"] == "Token nbp_made_up_token"


@respx.mock
async def test_the_mesh_card_counts_what_is_wrong_as_well_as_what_is_on() -> None:
    _netbird()
    data = await get_adapter("netbird").fetch("mesh", CONFIG, {}, _ctx())
    assert data.primary["value"] == "2 / 4"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Login expired"] == 1 and labels["To approve"] == 1 and labels["Machines"] == 4
    assert labels["Server"] == "0.80.0"
    assert data.status == "bad", "a machine off the mesh until somebody authenticates it is a fault"
    assert data.metrics == {"connected": 2.0, "expired": 1.0}


@respx.mock
async def test_a_mesh_in_order_is_green_and_says_nothing_extra() -> None:
    _netbird(peers=[PEERS[0], PEERS[2]])
    data = await get_adapter("netbird").fetch("mesh", CONFIG, {}, _ctx())
    assert data.status == "ok" and data.meta["notice"] == ""
    assert [row["label"] for row in data.secondary] == ["Machines", "Server"]


@respx.mock
async def test_a_newer_management_server_is_said_once() -> None:
    _netbird(version={**VERSION, "management_update_available": True, "management_available_version": "0.81.0"})
    data = await get_adapter("netbird").fetch("mesh", CONFIG, {}, _ctx())
    assert "0.81.0" in data.meta["notice"]


@respx.mock
async def test_the_cloud_has_no_version_of_yours_and_that_is_not_an_error() -> None:
    _netbird(version=None)
    data = await get_adapter("netbird").fetch("mesh", CONFIG, {}, _ctx())
    assert data.primary["value"] == "2 / 4"
    assert "Server" not in [row["label"] for row in data.secondary]


@respx.mock
async def test_what_needs_attention_comes_first_and_asleep_is_not_a_fault() -> None:
    _netbird()
    data = await get_adapter("netbird").fetch("peers", CONFIG, {}, _ctx())
    titles = [item["title"] for item in data.items]
    assert titles[:2] == ["laptop", "newphone"], "the expired login and the one waiting for approval"
    rows = {item["title"]: item for item in data.items}
    assert rows["laptop"]["status"] == "bad" and rows["laptop"]["value"] == "Login expired"
    assert rows["newphone"]["value"] == "Waiting for approval"
    assert rows["pi"]["status"] == "unknown" and "seen " in rows["pi"]["subtitle"]
    assert rows["nas"]["status"] == "ok" and rows["nas"]["value"] == "100.84.0.3"
    assert "Debian 13 · v0.80.0" == rows["nas"]["subtitle"]


@respx.mock
async def test_the_list_can_be_sorted_by_name_instead() -> None:
    _netbird()
    data = await get_adapter("netbird").fetch("peers", CONFIG, {"trouble_first": False}, _ctx())
    assert [item["title"] for item in data.items] == ["laptop", "nas", "newphone", "pi"]


@respx.mock
async def test_one_group_or_only_what_is_off() -> None:
    _netbird()
    servers = await get_adapter("netbird").fetch("peers", CONFIG, {"group": "Servers"}, _ctx())
    assert [item["title"] for item in servers.items] == ["nas", "pi"], "by name, and the case does not matter"
    assert servers.primary["value"] == "1 / 2"
    off = await get_adapter("netbird").fetch("peers", CONFIG, {"offline_only": True}, _ctx())
    assert [item["title"] for item in off.items] == ["laptop", "pi"]


@respx.mock
async def test_a_group_with_nothing_in_it_says_which() -> None:
    _netbird()
    data = await get_adapter("netbird").fetch("peers", CONFIG, {"group": "cameras"}, _ctx())
    assert data.items == [] and "cameras" in data.meta["empty"]


@respx.mock
async def test_a_key_about_to_expire_is_worth_a_colour() -> None:
    _netbird()
    data = await get_adapter("netbird").fetch("keys", CONFIG, {}, _ctx())
    titles = [item["title"] for item in data.items]
    assert "withdrawn" not in titles, "a revoked key is hidden unless it is asked for"
    assert titles == ["old demo", "one laptop", "servers"]
    rows = {item["title"]: item for item in data.items}
    assert rows["old demo"]["status"] == "bad" and rows["old demo"]["value"] == "expired"
    assert rows["one laptop"]["status"] == "warn", "six days is not much"
    assert "0 of 1 used" in rows["one laptop"]["subtitle"] and "6 d left" in rows["one laptop"]["subtitle"]
    assert rows["servers"]["status"] == "ok" and "4 used" in rows["servers"]["subtitle"]
    assert data.status == "bad" and data.primary["value"] == 3


@respx.mock
async def test_revoked_keys_can_be_asked_for() -> None:
    _netbird()
    data = await get_adapter("netbird").fetch("keys", CONFIG, {"hide_revoked": False}, _ctx())
    assert "withdrawn" in [item["title"] for item in data.items]


@respx.mock
async def test_an_account_without_keys_is_not_an_error() -> None:
    _netbird(keys=[])
    data = await get_adapter("netbird").fetch("keys", CONFIG, {}, _ctx())
    assert data.items == [] and data.status == "ok" and "no setup keys" in data.meta["empty"]


@respx.mock
async def test_a_peer_that_was_never_seen_does_not_read_as_the_year_one() -> None:
    _netbird(peers=[{"id": "x", "hostname": "fresh", "ip": "100.84.0.44", "connected": False,
                     "last_seen": "0001-01-01T00:00:00Z", "groups": []}])
    data = await get_adapter("netbird").fetch("peers", CONFIG, {}, _ctx())
    assert "seen " not in data.items[0]["subtitle"]


@respx.mock
async def test_a_refused_token_names_where_a_new_one_comes_from() -> None:
    respx.get(f"{NB}/api/peers").mock(return_value=httpx.Response(401, json={"message": "unauthorized"}))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("netbird").fetch("peers", CONFIG, {}, _ctx())
    assert "Access tokens" in failure.value.hint and "nbp_" in failure.value.hint


@respx.mock
async def test_the_dashboard_is_named_as_the_likely_wrong_address() -> None:
    respx.get(f"{NB}/api/peers").mock(return_value=httpx.Response(200, text="<html>NetBird</html>"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("netbird").fetch("peers", CONFIG, {}, _ctx())
    assert failure.value.code == "not_json" and "dashboard" in str(failure.value.hint)


@respx.mock
async def test_a_self_hosted_server_is_the_same_api_at_another_address() -> None:
    own = "https://netbird.lan"
    respx.get(f"{own}/api/peers").mock(return_value=httpx.Response(200, json=PEERS))
    respx.get(f"{own}/api/instance/version").mock(return_value=httpx.Response(200, json=VERSION))
    said = await get_adapter("netbird").test({"url": own, "token": "nbp_x", "insecure": True}, _ctx())
    assert "4 machines, 2 connected" in said and "management 0.80.0" in said


def test_every_card_draws_in_the_demo() -> None:
    for kind in ("mesh", "peers", "keys"):
        assert get_adapter("netbird").demo(kind, {}, 2).primary
