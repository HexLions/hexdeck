"""Garage: cluster health, nodes and buckets through the admin API v2.

``garage_cluster.json`` holds what Garage 2.4.1 answered locally on
2026-10-10, one node with two buckets and three objects: the health, the
status (with the host name made neutral), the statistics, the bucket list
and each bucket's info. The refusal of a wrong token was measured the same
day.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://garage.local:3903"
CONFIG = {"url": BASE, "token": "made-up-token"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "garage_cluster.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _garage(health: dict | None = None, statistics: dict | None = None, status: dict | None = None) -> None:
    respx.get(f"{BASE}/v2/GetClusterHealth").mock(return_value=httpx.Response(200, json=health or ANSWERS["health"]))
    respx.get(f"{BASE}/v2/GetClusterStatistics").mock(return_value=httpx.Response(200, json=statistics or ANSWERS["statistics"]))
    respx.get(f"{BASE}/v2/GetClusterStatus").mock(return_value=httpx.Response(200, json=status or ANSWERS["status"]))
    respx.get(f"{BASE}/v2/ListBuckets").mock(return_value=httpx.Response(200, json=ANSWERS["buckets"]))
    ids = {bucket["id"]: bucket for bucket in (ANSWERS["bucket_photos"], ANSWERS["bucket_backups"])}
    respx.get(f"{BASE}/v2/GetBucketInfo").mock(side_effect=lambda request: httpx.Response(200, json=ids[request.url.params["id"]]))


@respx.mock
async def test_the_token_goes_in_as_bearer() -> None:
    _garage()
    assert await get_adapter("garage").test(CONFIG, _ctx()) == "Garage answers; the cluster is healthy."
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-token"


@respx.mock
async def test_a_wrong_token() -> None:
    respx.get(f"{BASE}/v2/GetClusterHealth").mock(return_value=httpx.Response(403, json={"code": "AccessDenied"}))
    with pytest.raises(AuthFailed):
        await get_adapter("garage").test(CONFIG, _ctx())


@respx.mock
async def test_the_cluster() -> None:
    _garage()
    data = await get_adapter("garage").fetch("cluster", CONFIG, {}, _ctx())
    assert data.status == "ok" and data.primary == {"label": "Cluster", "value": "healthy"}
    assert {"label": "Nodes up", "value": "1 / 1"} in data.secondary
    assert {"label": "Objects", "value": 3} in data.secondary
    assert data.metrics == {"nodes_down": 0.0, "bytes": 9000000.0}


@respx.mock
async def test_a_degraded_cluster_before_2_4_has_only_its_health() -> None:
    health = {**ANSWERS["health"], "status": "degraded", "storageNodes": 3, "storageNodesUp": 2, "partitionsQuorum": 250}
    _garage(health=health, statistics={"freeform": "Storage nodes: ..."})
    data = await get_adapter("garage").fetch("cluster", CONFIG, {}, _ctx())
    assert data.status == "warn" and data.secondary == [{"label": "Nodes up", "value": "2 / 3"}]
    assert data.meta["notice"] == "6 partitions without quorum."
    assert data.metrics == {"nodes_down": 1.0}


@respx.mock
async def test_the_nodes_the_ones_down_first() -> None:
    node = ANSWERS["status"]["nodes"][0]
    status = {"layoutVersion": 1, "nodes": [node, {**node, "id": "ff" * 32, "hostname": "garage-2", "isUp": False, "lastSeenSecsAgo": 600}]}
    _garage(status=status)
    data = await get_adapter("garage").fetch("nodes", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"], row["status"]) for row in data.items][0] == ("garage-2", "down", "bad")
    assert data.items[1]["title"] == "garage-1" and data.items[1]["value"].endswith("free") and 0 < data.items[1]["progress"] < 100


@respx.mock
async def test_the_buckets_the_biggest_first_without_their_keys() -> None:
    _garage()
    data = await get_adapter("garage").fetch("buckets", CONFIG, {}, _ctx())
    assert [(row["title"], row["subtitle"]) for row in data.items] == [("photos", "3 objects"), ("backups", "0 objects")]
    assert ANSWERS["bucket_photos"]["keys"][0]["accessKeyId"] not in json.dumps(data.items)
