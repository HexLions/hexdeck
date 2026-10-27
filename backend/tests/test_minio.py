"""MinIO, against metrics pages in the shape its source writes them.

Not recorded from a running MinIO: the project is archived and its binaries
are no longer served. The metric names are the ones cmd/metrics-v2.go of
RELEASE.2025-10-15T17-29-55Z builds; two nodes report the cluster figures
each, as a cluster does.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.minio import parse_metrics

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "minio_metrics.json").read_text())
URL = "https://minio.example.com:9000"
CONFIG = {"url": URL, "token": "eyJhbGciOiJIUzUxMiJ9.example.signature"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def test_labels_and_exponents_are_read() -> None:
    samples = parse_metrics('minio_bucket_usage_total_bytes{bucket="a b",server="x:9000"} 4.4e+11\n# HELP x\nbroken line\n')
    assert samples == [("minio_bucket_usage_total_bytes", {"bucket": "a b", "server": "x:9000"}, 4.4e11)]


@respx.mock
async def test_the_cluster_card_takes_each_figure_once_not_per_node() -> None:
    route = respx.get(f"{URL}/minio/v2/metrics/cluster").mock(return_value=httpx.Response(200, text=FIXTURE["cluster"]))
    data = await get_adapter("minio").fetch("cluster", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Used", "value": "45%"}
    assert data.secondary == [{"label": "Used", "value": "930.4 GB of 2.0 TB"}, {"label": "Nodes", "value": "2 / 2"},
                              {"label": "Drives", "value": "8 / 8"}, {"label": "Buckets", "value": 3}, {"label": "Objects", "value": 138943}]
    assert data.status == "ok"
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {CONFIG['token']}"


@respx.mock
async def test_a_drive_offline_is_red_and_says_so() -> None:
    respx.get(f"{URL}/minio/v2/metrics/cluster").mock(return_value=httpx.Response(200, text=FIXTURE["cluster_drive_down"]))
    data = await get_adapter("minio").fetch("cluster", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.meta["status_reason"] == "0 node(s), 1 drive(s) offline"
    assert data.metrics["drives_offline"] == 1.0


@respx.mock
async def test_buckets_largest_first_with_their_objects() -> None:
    respx.get(f"{URL}/minio/v2/metrics/bucket").mock(return_value=httpx.Response(200, text=FIXTURE["bucket"]))
    data = await get_adapter("minio").fetch("buckets", CONFIG, {}, _ctx())
    assert [(item["title"], item["subtitle"], item["value"]) for item in data.items] == [
        ("photos", "48211 objects", "411.1 GB"), ("backups", "312 objects", "260.0 GB")]


@respx.mock
async def test_before_the_first_scan_the_buckets_say_why_they_are_empty() -> None:
    respx.get(f"{URL}/minio/v2/metrics/bucket").mock(return_value=httpx.Response(200, text=FIXTURE["bucket_before_scan"]))
    data = await get_adapter("minio").fetch("buckets", CONFIG, {}, _ctx())
    assert data.items == [] and "scanner" in data.meta["empty"]


@respx.mock
async def test_without_a_token_no_header_is_sent_and_a_refusal_says_how_to_make_one() -> None:
    route = respx.get(f"{URL}/minio/v2/metrics/cluster").mock(return_value=httpx.Response(403, json=FIXTURE["access_denied"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("minio").test({"url": URL}, _ctx())
    assert "Authorization" not in route.calls[0].request.headers
    assert "mc admin prometheus generate" in refused.value.hint


@respx.mock
async def test_the_console_address_is_named() -> None:
    respx.get(f"{URL}/minio/v2/metrics/cluster").mock(return_value=httpx.Response(200, text="<!doctype html><html></html>"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("minio").test(CONFIG, _ctx())
    assert refused.value.code == "not_minio" and "console" in refused.value.hint


@respx.mock
async def test_the_connection_test_counts_the_nodes() -> None:
    respx.get(f"{URL}/minio/v2/metrics/cluster").mock(return_value=httpx.Response(200, text=FIXTURE["cluster"]))
    assert await get_adapter("minio").test(CONFIG, _ctx()) == "MinIO answers with 2 nodes online."
