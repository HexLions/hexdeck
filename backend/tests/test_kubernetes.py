"""Kubernetes: quantities, the states the cluster reports, and a cluster with no metrics."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.kubernetes import bytes_of, cpu_cores

URL = "https://10.0.0.10:6443"
CONFIG = {"url": URL, "token": "t"}

NODES = {"items": [
    {"metadata": {"name": "k3s-1"}, "spec": {}, "status": {
        "conditions": [{"type": "Ready", "status": "True"}, {"type": "MemoryPressure", "status": "False"}],
        "allocatable": {"cpu": "4", "memory": "8Gi"}, "nodeInfo": {"kubeletVersion": "v1.31.4+k3s1"}}},
    {"metadata": {"name": "k3s-2"}, "spec": {"unschedulable": True}, "status": {
        "conditions": [{"type": "Ready", "status": "True"}],
        "allocatable": {"cpu": "4", "memory": "8Gi"}, "nodeInfo": {"kubeletVersion": "v1.31.4+k3s1"}}},
    {"metadata": {"name": "k3s-3"}, "spec": {}, "status": {
        "conditions": [{"type": "Ready", "status": "False", "reason": "KubeletNotReady"}],
        "allocatable": {"cpu": "2", "memory": "4Gi"}, "nodeInfo": {"kubeletVersion": "v1.31.4+k3s1"}}},
]}
METRICS = {"items": [
    {"metadata": {"name": "k3s-1"}, "usage": {"cpu": "2000m", "memory": "4Gi"}},
    {"metadata": {"name": "k3s-2"}, "usage": {"cpu": "1", "memory": "2Gi"}},
]}
PODS = {"items": [
    {"metadata": {"name": "immich", "namespace": "media"}, "status": {
        "phase": "Running", "containerStatuses": [{"ready": True, "restartCount": 0}]}},
    {"metadata": {"name": "paperless", "namespace": "docs"}, "status": {
        "phase": "Running", "containerStatuses": [{"ready": False, "restartCount": 3, "state": {}}]}},
    {"metadata": {"name": "backup-cron", "namespace": "ops"}, "status": {
        "phase": "Succeeded", "containerStatuses": [{"ready": False, "restartCount": 0}]}},
    {"metadata": {"name": "grafana", "namespace": "monitoring"}, "status": {
        "phase": "Pending", "containerStatuses": [{"ready": False, "restartCount": 7,
                                                   "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}},
]}
DEPLOYMENTS = {"items": [
    {"metadata": {"name": "immich", "namespace": "media"}, "spec": {"replicas": 2}, "status": {"readyReplicas": 2}},
    {"metadata": {"name": "grafana", "namespace": "monitoring"}, "spec": {"replicas": 1}, "status": {}},
    {"metadata": {"name": "half", "namespace": "ops"}, "spec": {"replicas": 4}, "status": {"readyReplicas": 3}},
    {"metadata": {"name": "parked", "namespace": "ops"}, "spec": {"replicas": 0}, "status": {}},
]}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _cluster(metrics: bool = True) -> None:
    respx.get(f"{URL}/version").mock(return_value=httpx.Response(200, json={"gitVersion": "v1.31.4+k3s1"}))
    respx.get(f"{URL}/api/v1/nodes").mock(return_value=httpx.Response(200, json=NODES))
    respx.get(f"{URL}/apis/metrics.k8s.io/v1beta1/nodes").mock(
        return_value=httpx.Response(200, json=METRICS) if metrics
        else httpx.Response(404, json={"message": "the server could not find the requested resource"}))
    respx.get(f"{URL}/api/v1/pods").mock(return_value=httpx.Response(200, json=PODS))
    respx.get(f"{URL}/apis/apps/v1/deployments").mock(return_value=httpx.Response(200, json=DEPLOYMENTS))


def test_quantities_are_read_as_the_units_they_are() -> None:
    assert cpu_cores("250m") == 0.25
    assert cpu_cores("1") == 1.0
    assert cpu_cores("123456789n") == pytest.approx(0.123456789)
    assert cpu_cores("") is None and cpu_cores(None) is None
    assert bytes_of("2Gi") == 2 * 1024**3
    assert bytes_of("1024Ki") == 1024 * 1024
    assert bytes_of("1000M") == 1e9, "the decimal suffixes are not the binary ones"
    assert bytes_of("nonsense") is None


@respx.mock
async def test_the_cluster_card_counts_nodes_pods_and_usage() -> None:
    _cluster()
    data = await get_adapter("kubernetes").fetch("cluster", CONFIG, {}, _ctx())
    assert data.primary["value"] == "1 / 4", "one of four pods is running and well"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Nodes"] == "1 / 3", "one cordoned, one not ready"
    # 3 of 10 cores, 6 of 20 GiB.
    assert labels["CPU"] == 30.0 and labels["Memory"] == 30.0
    assert labels["Failing"] == 1 and labels["Version"] == "v1.31.4+k3s1"
    assert data.status == "bad" and data.metrics["cpu"] == 30.0


@respx.mock
async def test_a_cluster_without_metrics_server_says_so_instead_of_drawing_zeroes() -> None:
    _cluster(metrics=False)
    data = await get_adapter("kubernetes").fetch("cluster", CONFIG, {}, _ctx())
    labels = {row["label"] for row in data.secondary}
    assert "CPU" not in labels and "Memory" not in labels
    assert "metrics-server" in data.meta["notice"]
    assert "cpu" not in data.metrics, "nothing measured is nothing written down"


@respx.mock
async def test_a_cordoned_node_is_not_a_healthy_node() -> None:
    _cluster()
    data = await get_adapter("kubernetes").fetch("nodes", CONFIG, {}, _ctx())
    rows = {item["title"]: item for item in data.items}
    assert rows["k3s-3"]["status"] == "bad" and rows["k3s-3"]["subtitle"].startswith("KubeletNotReady")
    assert rows["k3s-2"]["status"] == "warn" and "Cordoned" in rows["k3s-2"]["subtitle"]
    assert rows["k3s-1"]["status"] == "ok" and "cpu 50%" in rows["k3s-1"]["subtitle"]
    assert data.items[0]["title"] == "k3s-3", "the broken one comes first"
    assert data.primary["value"] == "1 / 3" and rows["k3s-1"]["value"] == "v1.31.4+k3s1"


@respx.mock
async def test_a_node_under_pressure_is_yellow() -> None:
    _cluster()
    respx.get(f"{URL}/api/v1/nodes").mock(return_value=httpx.Response(200, json={"items": [
        {"metadata": {"name": "k3s-1"}, "spec": {}, "status": {
            "conditions": [{"type": "Ready", "status": "True"}, {"type": "DiskPressure", "status": "True"}],
            "allocatable": {"cpu": "4", "memory": "8Gi"}, "nodeInfo": {"kubeletVersion": "v1.31.4"}}}]}))
    data = await get_adapter("kubernetes").fetch("nodes", CONFIG, {}, _ctx())
    assert data.items[0]["status"] == "warn" and "DiskPressure" in data.items[0]["subtitle"]


@respx.mock
async def test_a_finished_job_is_not_a_broken_pod() -> None:
    _cluster()
    data = await get_adapter("kubernetes").fetch("pods", CONFIG, {}, _ctx())
    titles = [item["title"] for item in data.items]
    assert "backup-cron" not in titles, "Succeeded is how a job ends"
    assert "immich" not in titles, "a running pod is not what this card is for"
    assert titles == ["grafana", "paperless"]
    assert data.items[0]["value"] == "CrashLoopBackOff" and "7 restarts" in data.items[0]["subtitle"]
    assert data.items[1]["value"] == "Not ready"
    assert data.primary["value"] == 2 and data.status == "bad"


@respx.mock
async def test_every_pod_can_be_asked_for() -> None:
    _cluster()
    data = await get_adapter("kubernetes").fetch("pods", CONFIG, {"troubled_only": False}, _ctx())
    assert len(data.items) == 4 and "Every pod is running." == data.meta["empty"]


@respx.mock
async def test_one_namespace_is_asked_of_the_namespaced_address() -> None:
    _cluster()
    route = respx.get(f"{URL}/api/v1/namespaces/media/pods").mock(return_value=httpx.Response(200, json={"items": []}))
    await get_adapter("kubernetes").fetch("pods", CONFIG, {"namespace": "media"}, _ctx())
    assert route.call_count == 1


@respx.mock
async def test_a_deployment_scaled_to_nothing_is_not_degraded() -> None:
    _cluster()
    data = await get_adapter("kubernetes").fetch("workloads", CONFIG, {}, _ctx())
    rows = {item["title"]: item for item in data.items}
    assert rows["parked"]["status"] == "unknown" and rows["parked"]["value"] == "0 / 0"
    assert rows["grafana"]["status"] == "bad" and rows["grafana"]["value"] == "0 / 1"
    assert rows["half"]["status"] == "warn" and rows["half"]["progress"] == 75.0
    assert rows["immich"]["status"] == "ok"
    assert data.primary["value"] == 2 and data.status == "bad"


@respx.mock
async def test_a_token_without_the_rights_names_the_role_that_has_them() -> None:
    _cluster()
    respx.get(f"{URL}/api/v1/nodes").mock(return_value=httpx.Response(403, json={"reason": "Forbidden"}))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("kubernetes").fetch("nodes", CONFIG, {}, _ctx())
    assert "view" in str(failure.value)


@respx.mock
async def test_a_refused_token_is_an_authentication_failure() -> None:
    _cluster()
    respx.get(f"{URL}/api/v1/nodes").mock(return_value=httpx.Response(401, json={"reason": "Unauthorized"}))
    with pytest.raises(AuthFailed):
        await get_adapter("kubernetes").fetch("nodes", CONFIG, {}, _ctx())


@respx.mock
async def test_the_token_goes_in_the_header_as_a_bearer() -> None:
    _cluster()
    await get_adapter("kubernetes").fetch("nodes", CONFIG, {}, _ctx())
    assert respx.calls[0].request.headers["authorization"] == "Bearer t"


@respx.mock
async def test_something_that_is_not_an_api_server_says_so() -> None:
    respx.get(f"{URL}/version").mock(return_value=httpx.Response(200, json={"hello": "world"}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("kubernetes").test(CONFIG, _ctx())
    assert failure.value.code == "not_kubernetes"


@respx.mock
async def test_the_test_button_says_whether_usage_is_measured() -> None:
    _cluster(metrics=False)
    said = await get_adapter("kubernetes").test(CONFIG, _ctx())
    assert "3 nodes" in said and "without metrics-server" in said


def test_the_demo_draws() -> None:
    for kind in ("cluster", "nodes", "pods", "workloads"):
        assert get_adapter("kubernetes").demo(kind, {}, 2).primary
