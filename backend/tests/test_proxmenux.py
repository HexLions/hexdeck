"""ProxMenux Monitor: its verdicts, its node, its guests and its disks.

The answers below follow the monitor's own source (MacRimi/ProxMenux, the Flask
server under AppImage/scripts): the health status and details, /api/system,
/api/vms and /api/storage.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

PMX = "https://proxmox.lan:8008"
CONFIG = {"url": PMX, "token": "made-up-token", "insecure": True}

STATUS = {"status": "WARNING", "summary": "Storage nearly full on local-zfs",
          "critical_count": 0, "warning_count": 2, "ok_count": 8, "timestamp": "2026-10-05T10:00:00"}
DETAILS = {"overall": "WARNING", "summary": "Storage nearly full on local-zfs", "details": {
    "cpu": {"status": "OK"},
    "memory": {"status": "OK"},
    "storage": {"status": "WARNING", "reason": "local-zfs at 88%"},
    "log_errors": {"status": "CRITICAL", "reason": "12 errors in the last hour"},
    "updates": {"status": "OK"},
}}
SYSTEM = {"cpu_usage": 12.4, "memory_usage": 46.2, "memory_total": 64.0, "memory_used": 29.6,
          "temperature": 47, "uptime": 1_140_000, "load_average": [0.42, 0.51, 0.6],
          "hostname": "pve", "proxmox_node": "pve", "cpu_cores": 8, "cpu_threads": 16,
          "proxmox_version": "9.0.3", "kernel_version": "6.14.11-1-pve", "available_updates": 3}
VMS = [
    {"vmid": 100, "name": "truenas", "status": "running", "type": "qemu", "cpu": 0.12,
     "mem": 17_179_869_184, "maxmem": 34_359_738_368},
    {"vmid": 110, "name": "pihole", "status": "running", "type": "lxc", "cpu": 0.02,
     "mem": 268_435_456, "maxmem": 536_870_912},
    {"vmid": 120, "name": "old-test", "status": "stopped", "type": "lxc", "cpu": 0, "mem": 0, "maxmem": 536_870_912},
]
STORAGE = {"total": 0, "used": 0, "disks": [
    {"name": "sdb", "model": "WDC WD40EFPX", "size": 4_000_787_030, "health": "warning",
     "temperature": 46, "smart_status": "passed"},
    {"name": "nvme0n1", "model": "Samsung 990 PRO", "size": 2_000_398_934, "health": "passed",
     "temperature": 41, "smart_status": "passed"},
    {"name": "sdc", "model": "ST4000VN006", "size": 4_000_787_030, "health": "critical",
     "temperature": 0, "smart_status": "failed"},
], "disk_count": 3, "healthy_disks": 1, "warning_disks": 1, "critical_disks": 1}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _monitor() -> None:
    respx.get(f"{PMX}/api/health/status").mock(return_value=httpx.Response(200, json=STATUS))
    respx.get(f"{PMX}/api/health/details").mock(return_value=httpx.Response(200, json=DETAILS))
    respx.get(f"{PMX}/api/system").mock(return_value=httpx.Response(200, json=SYSTEM))
    respx.get(f"{PMX}/api/vms").mock(return_value=httpx.Response(200, json=VMS))
    respx.get(f"{PMX}/api/storage").mock(return_value=httpx.Response(200, json=STORAGE))


@respx.mock
async def test_the_verdict_is_the_monitors_own_word() -> None:
    _monitor()
    data = await get_adapter("proxmenux").fetch("health", CONFIG, {}, _ctx())
    assert data.primary["value"] == "Warning" and data.status == "warn"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Warnings"] == 2 and labels["Well"] == 8 and "Critical" not in labels
    assert data.meta["notice"] == "Storage nearly full on local-zfs", "its sentence is the useful part"
    assert data.metrics == {"warnings": 2.0, "criticals": 0.0}


@respx.mock
async def test_a_critical_node_is_red() -> None:
    _monitor()
    respx.get(f"{PMX}/api/health/status").mock(return_value=httpx.Response(200, json={
        **STATUS, "status": "CRITICAL", "critical_count": 1}))
    data = await get_adapter("proxmenux").fetch("health", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.secondary[0]["label"] == "Critical"


@respx.mock
async def test_the_checks_card_shows_what_is_wrong_first_and_why() -> None:
    _monitor()
    data = await get_adapter("proxmenux").fetch("checks", CONFIG, {}, _ctx())
    assert [item["title"] for item in data.items] == ["Log errors", "Storage"], "only what is not well"
    assert data.items[0]["status"] == "bad" and data.items[0]["subtitle"] == "12 errors in the last hour"
    assert data.items[1]["value"] == "Warning"
    assert data.status == "bad" and data.primary["value"] == 2


@respx.mock
async def test_every_category_can_be_asked_for() -> None:
    _monitor()
    data = await get_adapter("proxmenux").fetch("checks", CONFIG, {"troubled_only": False}, _ctx())
    assert len(data.items) == 5
    assert [item["title"] for item in data.items][:2] == ["Log errors", "Storage"], "worst first all the same"


@respx.mock
async def test_a_node_in_good_order_says_so_rather_than_showing_an_empty_list() -> None:
    _monitor()
    respx.get(f"{PMX}/api/health/details").mock(return_value=httpx.Response(200, json={
        "overall": "OK", "summary": "All systems operational", "details": {"cpu": {"status": "OK"}}}))
    data = await get_adapter("proxmenux").fetch("checks", CONFIG, {}, _ctx())
    assert data.items == [] and data.status == "ok"
    assert data.meta["empty"] == "Every check is happy."


@respx.mock
async def test_the_node_card_reads_the_monitors_numbers() -> None:
    _monitor()
    data = await get_adapter("proxmenux").fetch("node", CONFIG, {}, _ctx())
    assert data.primary["value"] == 12.4
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Memory"] == 46.2 and labels["Temp"] == 47.0
    assert labels["Load"] == "0.42 0.51 0.60" and labels["Updates"] == 3
    assert data.meta["node"] == "pve" and data.meta["version"] == "9.0.3"
    assert data.metrics == {"cpu": 12.4, "memory": 46.2, "temperature": 47.0}


@respx.mock
async def test_a_node_without_a_sensor_draws_without_a_temperature() -> None:
    _monitor()
    respx.get(f"{PMX}/api/system").mock(return_value=httpx.Response(200, json={**SYSTEM, "temperature": 0}))
    data = await get_adapter("proxmenux").fetch("node", CONFIG, {}, _ctx())
    assert "Temp" not in [row["label"] for row in data.secondary]
    assert "temperature" not in data.metrics, "nothing measured is nothing written down"


@respx.mock
async def test_guests_are_running_first_and_a_stopped_one_is_not_a_fault() -> None:
    _monitor()
    data = await get_adapter("proxmenux").fetch("guests", CONFIG, {}, _ctx())
    assert [item["title"] for item in data.items] == ["pihole", "truenas", "old-test"]
    stopped = data.items[-1]
    assert stopped["status"] == "unknown" and stopped["value"] == "stopped"
    machine = next(item for item in data.items if item["title"] == "truenas")
    assert "100 · machine" in machine["subtitle"] and "memory 50%" in machine["subtitle"]
    assert data.primary["value"] == "2 / 3" and data.metrics["running"] == 2.0


@respx.mock
async def test_only_machines_or_only_containers_can_be_asked_for() -> None:
    _monitor()
    machines = await get_adapter("proxmenux").fetch("guests", CONFIG, {"kind": "qemu"}, _ctx())
    assert [item["title"] for item in machines.items] == ["truenas"]
    containers = await get_adapter("proxmenux").fetch("guests", CONFIG, {"kind": "lxc", "running_only": True}, _ctx())
    assert [item["title"] for item in containers.items] == ["pihole"]
    assert containers.primary["value"] == "1 / 2", "the count is of the containers, stopped one included"


@respx.mock
async def test_the_disks_card_puts_a_failing_disk_first() -> None:
    _monitor()
    data = await get_adapter("proxmenux").fetch("disks", CONFIG, {}, _ctx())
    assert [item["title"] for item in data.items] == ["sdc", "sdb", "nvme0n1"]
    assert data.items[0]["status"] == "bad" and data.items[0]["value"] == "Failed"
    assert data.items[1]["value"] == "46 °C"
    assert "3.7 TB" in data.items[1]["subtitle"], "the size arrives in kilobytes"
    assert data.status == "bad"
    assert data.secondary[0] == {"label": "Warmest", "value": 46.0, "unit": "°C"}


@respx.mock
async def test_a_monitor_without_a_token_is_told_what_to_make() -> None:
    respx.get(f"{PMX}/api/health/status").mock(return_value=httpx.Response(401, json={
        "error": "Authentication required", "message": "No authorization header provided"}))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("proxmenux").fetch("health", {"url": PMX, "insecure": True}, {}, _ctx())
    assert "wants a token" in str(failure.value) and "API token" in failure.value.hint


@respx.mock
async def test_a_token_that_no_longer_verifies_says_why() -> None:
    respx.get(f"{PMX}/api/health/status").mock(return_value=httpx.Response(401, json={
        "error": "Invalid or expired token"}))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("proxmenux").fetch("health", CONFIG, {}, _ctx())
    assert "rejected the token" in str(failure.value) and "JWT secret" in failure.value.hint


@respx.mock
async def test_an_older_monitor_without_the_health_checks_is_named() -> None:
    _monitor()
    respx.get(f"{PMX}/api/health/details").mock(return_value=httpx.Response(404, text="Not Found"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("proxmenux").fetch("checks", CONFIG, {}, _ctx())
    assert failure.value.code == "no_such_path"


@respx.mock
async def test_the_proxmox_interface_is_named_as_the_likely_mistake() -> None:
    respx.get(f"{PMX}/api/system").mock(return_value=httpx.Response(200, text="<html>Proxmox</html>"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("proxmenux").fetch("node", CONFIG, {}, _ctx())
    assert failure.value.code == "not_json" and "8006" in str(failure.value.hint)


@respx.mock
async def test_the_token_goes_in_the_header_as_a_bearer() -> None:
    _monitor()
    await get_adapter("proxmenux").fetch("node", CONFIG, {}, _ctx())
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-token"


@respx.mock
async def test_the_test_button_names_the_node_and_the_verdict() -> None:
    _monitor()
    said = await get_adapter("proxmenux").test(CONFIG, _ctx())
    assert "pve" in said and "9.0.3" in said and "WARNING" in said


@respx.mock
async def test_something_that_is_not_the_monitor_at_all_is_refused() -> None:
    respx.get(f"{PMX}/api/system").mock(return_value=httpx.Response(200, json={"hello": "world"}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("proxmenux").test(CONFIG, _ctx())
    assert failure.value.code == "not_proxmenux"


def test_every_card_draws_in_the_demo() -> None:
    for kind in ("health", "checks", "node", "guests", "disks"):
        assert get_adapter("proxmenux").demo(kind, {}, 3).primary
