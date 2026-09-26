"""Elasticsearch and OpenSearch: the colour, the shards, the indices."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

URL = "http://elasticsearch:9200"
CONFIG = {"url": URL, "api_key": "abc"}

HEALTH = {
    "cluster_name": "homelab", "status": "yellow", "number_of_nodes": 2, "active_shards": 9,
    "unassigned_shards": 1, "relocating_shards": 0, "initializing_shards": 2,
}
INDICES = [
    {"index": "paperless", "health": "green", "status": "open", "docs.count": "18932", "store.size": "4100000000", "pri": "1", "rep": "1"},
    {"index": "logs-2026.09", "health": "red", "status": "open", "docs.count": "4812003", "store.size": "12400000000", "pri": "3", "rep": "1"},
    {"index": ".kibana_8", "health": "green", "status": "open", "docs.count": "412", "store.size": "2100000", "pri": "1", "rep": "0"},
    {"index": "old-orders", "health": "green", "status": "close", "docs.count": "0", "store.size": "0", "pri": "1", "rep": "1"},
]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _mock(health: dict | None = None, indices: list | None = None) -> None:
    respx.get(f"{URL}/_cluster/health").mock(return_value=httpx.Response(200, json=health or HEALTH))
    respx.get(f"{URL}/_cat/indices").mock(return_value=httpx.Response(200, json=indices if indices is not None else INDICES))


@respx.mock
async def test_the_cluster_card_passes_the_cluster_s_own_colour_through() -> None:
    _mock()
    data = await get_adapter("elasticsearch").fetch("cluster", CONFIG, {}, _ctx())
    assert data.primary["value"] == "yellow" and data.status == "warn"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Nodes"] == 2 and labels["Shards"] == 9 and labels["Unplaced"] == 1
    assert labels["Moving"] == 2, "relocating and initializing shards are both movement"
    assert labels["Documents"] == 18932 + 4812003 + 412
    assert data.metrics["unassigned"] == 1.0


@respx.mock
async def test_a_single_node_is_told_why_it_is_yellow() -> None:
    _mock(health={**HEALTH, "number_of_nodes": 1})
    data = await get_adapter("elasticsearch").fetch("cluster", CONFIG, {}, _ctx())
    assert "single node" in data.meta["notice"]
    _mock(health={**HEALTH, "status": "green", "number_of_nodes": 1})
    data = await get_adapter("elasticsearch").fetch("cluster", CONFIG, {}, _ctx())
    assert data.meta["notice"] == "", "a green cluster needs no excuse"


@respx.mock
async def test_the_index_card_puts_the_unhealthy_ones_first_and_hides_the_dotted_ones() -> None:
    _mock()
    data = await get_adapter("elasticsearch").fetch("indices", CONFIG, {}, _ctx())
    titles = [item["title"] for item in data.items]
    assert titles[0] == "logs-2026.09", "red first, whatever its size"
    assert ".kibana_8" not in titles, "a system index is not what somebody is looking at"
    assert data.status == "bad"
    closed = next(item for item in data.items if item["title"] == "old-orders")
    assert closed["status"] == "unknown" and "closed" in closed["subtitle"]
    assert data.items[0]["text"] == "11.5 GB"


@respx.mock
async def test_system_indices_can_be_asked_for() -> None:
    _mock()
    data = await get_adapter("elasticsearch").fetch("indices", CONFIG, {"hidden": True}, _ctx())
    assert ".kibana_8" in [item["title"] for item in data.items]


@respx.mock
async def test_an_empty_cluster_is_not_an_error() -> None:
    _mock(health={**HEALTH, "status": "green", "unassigned_shards": 0, "initializing_shards": 0, "active_shards": 0}, indices=[])
    cluster = await get_adapter("elasticsearch").fetch("cluster", CONFIG, {}, _ctx())
    assert cluster.status == "ok"
    indices = await get_adapter("elasticsearch").fetch("indices", CONFIG, {}, _ctx())
    assert indices.items == [] and "no indices" in indices.meta["empty"]


@respx.mock
async def test_the_api_key_goes_in_the_header_the_cluster_expects() -> None:
    _mock()
    await get_adapter("elasticsearch").fetch("cluster", CONFIG, {}, _ctx())
    sent = respx.calls[0].request.headers.get("authorization")
    assert sent == "ApiKey abc"


@respx.mock
async def test_two_kinds_of_credential_at_once_is_refused_before_anything_is_sent() -> None:
    with pytest.raises(AdapterError) as failure:
        await get_adapter("elasticsearch").fetch("cluster", {"url": URL, "api_key": "abc", "username": "elastic"}, {}, _ctx())
    assert failure.value.code == "two_credentials"
    assert not respx.calls, "nothing was sent"


@respx.mock
async def test_refused_credentials_are_an_authentication_failure() -> None:
    respx.get(f"{URL}/_cluster/health").mock(return_value=httpx.Response(401, json={"error": "security_exception"}))
    respx.get(f"{URL}/_cat/indices").mock(return_value=httpx.Response(401, json={"error": "security_exception"}))
    with pytest.raises(AuthFailed):
        await get_adapter("elasticsearch").fetch("cluster", CONFIG, {}, _ctx())


@respx.mock
async def test_kibana_s_port_is_named_as_the_likely_mistake() -> None:
    respx.get(f"{URL}/_cluster/health").mock(return_value=httpx.Response(200, text="<html>Kibana</html>"))
    respx.get(f"{URL}/_cat/indices").mock(return_value=httpx.Response(200, text="<html>Kibana</html>"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("elasticsearch").fetch("cluster", CONFIG, {}, _ctx())
    assert failure.value.code == "not_json" and "9200" in str(failure.value.hint)


@respx.mock
async def test_opensearch_is_named_by_its_own_name() -> None:
    respx.get(f"{URL}/").mock(return_value=httpx.Response(200, json={
        "cluster_name": "homelab", "version": {"number": "2.19.1", "distribution": "opensearch"}}))
    _mock()
    said = await get_adapter("elasticsearch").test(CONFIG, _ctx())
    assert said.startswith("OpenSearch 2.19.1") and "is yellow" in said


def test_the_demo_draws() -> None:
    assert get_adapter("elasticsearch").demo("cluster", {}, 0).primary
    assert get_adapter("elasticsearch").demo("indices", {}, 0).items
