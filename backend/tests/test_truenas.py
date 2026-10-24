"""TrueNAS: the current API over a WebSocket, the REST API behind it.

The answers are the shapes TrueNAS 25.10.7 gave on 18.09.2026. What matters
most here is where the key goes: a key that reaches the current API over
plain http is revoked by TrueNAS for good (issue #4).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
import respx
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context, WidgetData, ago
from app.adapters.truenas import _version

KEY = "1-readonlykey"
INFO = {"version": "25.10.7", "hostname": "truenas", "uptime_seconds": 3600.5, "loadavg": [0.5, 0.3, 0.1], "physmem": 8_333_570_048, "cores": 4}
POOLS = [{"name": "tank", "status": "ONLINE", "healthy": True, "size": 16_642_998_272, "allocated": 2_711_552}]
ALERTS = [
    {"level": "WARNING", "dismissed": False, "datetime": {"$date": 1_789_712_345_000}, "formatted": "Pool tank is 91% full."},
    {"level": "CRITICAL", "dismissed": True, "datetime": {"$date": 1_789_700_000_000}, "formatted": "Dismissed, not shown."},
]
#: The same from before 25.04, where the REST API is the only one there is.
OLD_INFO = {**INFO, "version": "TrueNAS-SCALE-24.10.2"}
#: What a plain GET of /api/current, without a key, said on 25.10.7 on 21.09.2026.
NEEDS_UPGRADE = 'No WebSocket UPGRADE hdr: None\n Can "Upgrade" only to "WebSocket".'
#: The shapes of the middleware's models in TS-25.10.7 (src/middlewared/middlewared/api/v25_10_0).
DISKS = [
    {"name": "sda", "model": "WDC WD40EFZX-68AWUN0 ", "size": 4_000_787_030_016, "type": "HDD", "pool": "tank", "serial": "WD-EXAMPLE1"},
    {"name": "sdb", "model": "WDC WD40EFZX-68AWUN0", "size": 4_000_787_030_016, "type": "HDD", "pool": "tank", "serial": "WD-EXAMPLE2"},
    {"name": "nvme0n1", "model": "Samsung SSD 980 1TB", "size": 1_000_204_886_016, "type": "SSD", "pool": None, "serial": "S64EXAMPLE"},
]
#: ``disk.temperatures([], True)``: current and critical, or None for a disk that tells none.
TEMPERATURES = {"sda": [38.0, 70.0], "sdb": [53.0, 70.0], "nvme0n1": [None, None]}
APPS = [
    {"name": "jellyfin", "id": "jellyfin", "state": "RUNNING", "upgrade_available": True, "image_updates_available": False, "human_version": "10.10.7_1.2.3", "version": "1.2.3"},
    {"name": "immich", "id": "immich", "state": "CRASHED", "upgrade_available": False, "image_updates_available": False, "human_version": "v1.132.3_1.0.0", "version": "1.0.0"},
    {"name": "adguard", "id": "adguard", "state": "STOPPED", "upgrade_available": False, "image_updates_available": False, "human_version": "v0.107_1.1.0", "version": "1.1.0"},
]
SNAPSHOT_TASKS = [
    {"id": 1, "dataset": "tank/photos", "enabled": True, "state": {"state": "FINISHED", "datetime": {"$date": 1_789_712_345_000}}},
    {"id": 2, "dataset": "tank/old", "enabled": False, "state": {"state": "PENDING"}},
]
REPLICATIONS = [
    {"id": 1, "name": "tank/photos - backup/photos", "enabled": True,
     "state": {"state": "ERROR", "datetime": {"$date": 1_789_712_000_000}, "error": "No incremental base on dataset 'tank/photos' and replication from scratch is not allowed."}},
]
UPDATE = {"code": "NORMAL", "status": {"current_version": {"train": "TrueNAS-SCALE-Goldeye", "profile": "GENERAL", "matches_profile": True},
                                       "new_version": {"version": "25.10.8", "manifest": {}, "release_notes": None, "release_notes_url": "https://www.truenas.com/docs/"}},
          "error": None, "update_download_progress": None}
#: The cards taken from nexdeck 0.46.0, in the shapes nexdeck read from the
#: middleware's source (release/25.10.0, api/v25_10_0), not measured.
SUMMARY = {"ips": {"enp1s0": {"IPV4": ["192.168.1.20/24"], "IPV6": ["fe80::1/64"]}}, "default_routes": ["192.168.1.1"], "nameservers": ["192.168.1.1"]}
SERVICES = [
    {"id": 1, "service": "cifs", "enable": True, "state": "RUNNING", "pids": [100]},
    {"id": 2, "service": "nfs", "enable": True, "state": "STOPPED", "pids": []},
    {"id": 3, "service": "ssh", "enable": False, "state": "RUNNING", "pids": [200]},
    {"id": 4, "service": "ups", "enable": False, "state": "STOPPED", "pids": []},
]
VMS = [{"id": 1, "name": "homeassistant", "vcpus": 1, "cores": 2, "threads": 1, "memory": 4096, "status": {"state": "RUNNING", "pid": 42, "domain_state": "RUNNING"}},
       {"id": 2, "name": "windows", "vcpus": 4, "cores": 1, "threads": 1, "memory": 8192, "status": {"state": "STOPPED", "pid": None, "domain_state": "SHUTOFF"}}]
INSTANCES = [{"id": "pihole", "name": "pihole", "type": "CONTAINER", "status": "RUNNING", "cpu": "1", "memory": 536_870_912},
             {"id": "homeassistant", "name": "homeassistant", "type": "VM", "status": "RUNNING", "cpu": "2", "memory": 4_294_967_296}]
ANSWERS = {"system.info": INFO, "pool.query": POOLS, "alert.list": ALERTS, "disk.query": DISKS, "disk.temperatures": TEMPERATURES,
           "app.query": APPS, "pool.snapshottask.query": SNAPSHOT_TASKS, "replication.query": REPLICATIONS, "update.status": UPDATE,
           "network.general.summary": SUMMARY, "service.query": SERVICES, "vm.query": VMS, "virt.instance.query": INSTANCES}
#: One ``reporting.realtime`` event as 25.x sends it: CPU usage in percent, memory in bytes.
LIVE = {"cpu": {"cpu": {"usage": 37.25, "temp": None}, "cpu0": {"usage": 40.0, "temp": None}},
        "memory": {"arc_size": 2_000_000_000, "arc_free_memory": 0, "arc_available_memory": 0,
                   "physical_memory_total": 8_333_570_048, "physical_memory_available": 4_166_785_024},
        "disks": {}, "interfaces": {}, "zfs": {}, "pools": {}}
#: The same event from 24.10: the average across cores, memory in classes.
LIVE_24_10 = {"cpu": {"0": {"usage": 10.0}, "average": {"user": 5, "system": 3, "idle": 90, "iowait": 2, "usage": 8.0}},
              "memory": {"classes": {"page_tables": 100_000_000, "slab_cache": 200_000_000, "cache": 1_000_000_000, "buffers": 100_000_000,
                                     "unused": 2_000_000_000, "arc": 2_000_000_000, "apps": 3_000_000_000}, "extra": {}}}


class FakeTruenas:
    """A WebSocket that speaks JSON-RPC like TrueNAS, one per connection."""

    def __init__(self, key: str = KEY, refuse: dict[str, str] | None = None, live: dict[str, Any] | None = LIVE) -> None:
        self.key = key
        self.refuse = refuse or {}
        self.live = live
        self.sent: list[dict[str, Any]] = []
        self.opened: list[str] = []
        self.closed = 0
        self._pending: list[str] = []
        self._authenticated = False

    async def open(self, url: str, config: dict[str, Any]) -> FakeTruenas:
        self.opened.append(url)
        self._authenticated = False
        return self

    async def send(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        method, number = message["method"], message["id"]
        # An event in between, as TrueNAS sends them, must not be taken for the answer.
        self._pending.append(json.dumps({"jsonrpc": "2.0", "method": "collection_update", "params": {}}))
        if method == "auth.login_with_api_key":
            self._authenticated = message["params"] == [self.key]
            self._pending.append(json.dumps({"jsonrpc": "2.0", "id": number, "result": self._authenticated}))
        elif not self._authenticated:
            self._pending.append(json.dumps({"jsonrpc": "2.0", "id": number, "error": {"code": -32001, "message": "Method call error", "data": {"errname": "ENOTAUTHENTICATED", "reason": "[ENOTAUTHENTICATED] Not authenticated"}}}))
        elif method in self.refuse:
            self._pending.append(json.dumps({"jsonrpc": "2.0", "id": number, "error": {"code": -32001, "message": "Method call error", "data": {"errname": self.refuse[method], "reason": "Not permitted"}}}))
        elif method == "core.subscribe":
            # The event source with its argument, then the first event a moment later, as TrueNAS does it.
            assert message["params"] == ['reporting.realtime:{"interval": 2}'], message["params"]
            self._pending.append(json.dumps({"jsonrpc": "2.0", "id": number, "result": "sub-1"}))
            if self.live is not None:
                # ⚠️ The collection carries the argument, as TrueNAS sends it.
                self._pending.append(json.dumps({"jsonrpc": "2.0", "method": "collection_update",
                                                 "params": {"msg": "added", "collection": 'reporting.realtime:{"interval": 2}', "fields": self.live}}))
        else:
            if method == "disk.temperatures":
                assert message["params"] == [[], True], "every disk, with its critical temperature"
            if method == "disk.query":
                assert message["params"] == [[], {"extra": {"pools": True}}], "with the pool each disk is in"
            self._pending.append(json.dumps({"jsonrpc": "2.0", "id": number, "result": ANSWERS[method]}))

    async def recv(self) -> str:
        return self._pending.pop(0)

    async def close(self) -> None:
        self.closed += 1

    def methods(self) -> list[str]:
        return [message["method"] for message in self.sent]


def turned_down(status: int):
    async def open_socket(url: str, config: dict[str, Any]) -> Any:
        raise InvalidStatus(Response(status, "Not Found", Headers()))

    return open_socket


def current_api(address: str, status: int) -> respx.Route:
    """The keyless look at /api/current: 400 on a TrueNAS that has it, 404 on one that has not."""
    return respx.get(f"{address}/api/current").mock(return_value=httpx.Response(status, text=NEEDS_UPGRADE if status == 400 else "404 page not found"))


async def no_socket(url: str, config: dict[str, Any]) -> Any:
    raise AssertionError(f"a WebSocket was opened to {url}; over http that revokes the key")


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


@pytest.fixture
def truenas(monkeypatch: pytest.MonkeyPatch):
    adapter = get_adapter("truenas")
    fake = FakeTruenas()
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    return adapter, fake


async def test_https_reads_everything_through_the_current_api(ctx: Context, truenas) -> None:
    """A read-only administrator's key gets 403 from the REST API on 25.10 and
    everything from the current API."""
    adapter, fake = truenas
    config = {"url": "https://truenas.example.com/", "api_key": KEY}

    assert await adapter.test(config, ctx) == "TrueNAS 25.10.7 on truenas answers."
    assert fake.opened == ["wss://truenas.example.com/api/current"]

    fake.sent.clear()
    system = await adapter.fetch("system", config, {}, ctx)
    assert fake.methods() == ["auth.login_with_api_key", "system.info", "alert.list", "core.subscribe",
                              "auth.login_with_api_key", "update.status"], "one connection for the card, and the hourly update check on its own"
    assert system.primary == {"label": "CPU", "value": 37.2, "unit": "%"}, "the CPU usage TrueNAS itself shows, not the load average"
    memory = next(entry for entry in system.secondary if entry["label"] == "Memory")
    assert memory["value"] == 50.0 and memory["unit"] == "%" and memory["text"] == "3.9 GB / 7.8 GB", "memory in use, of the total"
    assert system.metrics == {"load": 37.2, "memory": 50.0}
    assert [entry["value"] for entry in system.secondary if entry["label"] == "Alerts"] == [1]
    assert system.status == "warn"

    pools = await adapter.fetch("pools", config, {}, ctx)
    assert [(item["title"], item["status"]) for item in pools.items] == [("tank", "ok")]

    alerts = await adapter.fetch("alerts", config, {}, ctx)
    assert [(item["title"], item["subtitle"]) for item in alerts.items] == [("Pool tank is 91% full.", "2026-09-18")], "a day, not the first ten digits of a timestamp"
    assert fake.closed == len(fake.opened), "every connection is closed again"


async def test_cards_share_what_was_just_read(ctx: Context, truenas) -> None:
    adapter, fake = truenas
    config = {"url": "https://truenas.example.com", "api_key": KEY}
    await adapter.fetch("system", config, {}, ctx)
    opened = len(fake.opened)
    await adapter.fetch("alerts", config, {}, ctx)
    assert len(fake.opened) == opened, "the alerts were read a moment ago by the system card"


async def test_a_refused_key_says_so_and_does_not_try_the_rest_api(ctx: Context, truenas) -> None:
    adapter, fake = truenas
    config = {"url": "https://truenas.example.com", "api_key": "1-wrong"}
    with respx.mock(assert_all_called=False) as router:
        rest = router.get(url__regex=r".*/api/v2\.0/.*").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(AdapterError) as refused:
            await adapter.test(config, ctx)
    assert refused.value.code == "auth_failed"
    assert "revokes" in refused.value.hint
    assert fake.methods() == ["auth.login_with_api_key"]
    assert not rest.called


async def test_a_method_the_role_may_not_call_names_the_role(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    fake = FakeTruenas(refuse={"pool.query": "EACCES"})
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    with pytest.raises(AdapterError) as refused:
        await adapter.fetch("pools", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert refused.value.code == "auth_failed"
    assert "Read-Only Administrator" in refused.value.hint


@respx.mock
async def test_http_never_opens_the_websocket_and_says_why_it_is_refused(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """TrueNAS revoked a key for good after one login over ws:// (measured).
    Over http the REST API is the only safe way, and on 25.10 it refuses a
    read-only key; the message has to say what to change."""
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", no_socket)
    current_api("http://truenas.example.com", 404)
    respx.get("http://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(403))
    with pytest.raises(AdapterError) as refused:
        await adapter.test({"url": "http://truenas.example.com", "api_key": KEY}, ctx)
    assert refused.value.code == "auth_failed"
    assert "https://" in refused.value.hint


@respx.mock
async def test_http_with_a_full_key_keeps_working_over_rest(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a TrueNAS from before 25.04, which has no other API."""
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", no_socket)
    current_api("http://truenas.example.com", 404)
    respx.get("http://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(200, json=OLD_INFO))
    respx.get("http://truenas.example.com/api/v2.0/pool").mock(return_value=httpx.Response(200, json=POOLS))
    pools = await adapter.fetch("pools", {"url": "http://truenas.example.com", "api_key": "2-full"}, {}, ctx)
    assert [item["title"] for item in pools.items] == ["tank"]
    assert respx.calls.last.request.headers["Authorization"] == "Bearer 2-full"


@respx.mock
async def test_rest_is_refused_where_truenas_has_deprecated_it(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ A full administrator's key over http reaches the REST API on 25.10 and
    gets its answers. That it works is the trouble. Measured on 25.10.7: every
    call that signs in is counted, and one alert on the NAS names the count of
    the last 24 hours. It only goes away at no call at all, so not even the
    version may be asked over REST. A plain GET of /api/current, without the
    key, tells a TrueNAS that has the current API, once an hour."""
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", no_socket)
    look = current_api("http://truenas.example.com", 400)
    rest = respx.get(url__startswith="http://truenas.example.com/api/v2.0/").mock(return_value=httpx.Response(200, json=INFO))
    config = {"url": "http://truenas.example.com", "api_key": "2-full"}
    started = time.monotonic()
    for minutes, kind in enumerate(("pools", "system", "alerts")):
        # Five minutes apart: past every short cache, well inside the hour.
        # Asked in the same second, the second call never leaves the request
        # cache, and this passed with nothing remembered at all.
        monkeypatch.setattr(time, "monotonic", lambda minutes=minutes: started + 300 * minutes)
        with pytest.raises(AdapterError) as refused:
            await adapter.fetch(kind, config, {}, ctx)
        assert refused.value.code == "deprecated_api"
        assert "Change the URL to https://" in refused.value.hint
    assert not rest.called, "one call over REST is one too many: it keeps the alert alive"
    assert look.call_count == 1, "asked once an hour, not once a card"
    assert "authorization" not in look.calls.last.request.headers, "the key stays at home for this"


@respx.mock
async def test_a_proxy_that_says_404_is_caught_by_the_version(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second line. Something in front says 404 for /api/current, so the
    REST API is asked, and what it says its version is settles it: once an
    hour, and nothing else goes there."""
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", no_socket)
    current_api("http://truenas.example.com", 404)
    info = respx.get("http://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(200, json={**INFO, "version": "25.10.1"}))
    pools = respx.get("http://truenas.example.com/api/v2.0/pool").mock(return_value=httpx.Response(200, json=POOLS))
    alerts = respx.get("http://truenas.example.com/api/v2.0/alert/list").mock(return_value=httpx.Response(200, json=ALERTS))
    config = {"url": "http://truenas.example.com", "api_key": "2-full"}
    started = time.monotonic()
    for minutes, kind in enumerate(("pools", "system", "alerts")):
        monkeypatch.setattr(time, "monotonic", lambda minutes=minutes: started + 300 * minutes)
        with pytest.raises(AdapterError) as refused:
            await adapter.fetch(kind, config, {}, ctx)
        assert refused.value.code == "deprecated_api"
        assert "25.10.1" in refused.value.message
    assert info.call_count == 1, "the version is asked once an hour, not once a card"
    assert not pools.called and not alerts.called


@respx.mock
async def test_the_system_card_of_an_older_truenas_keeps_moving(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ The version comes from ``system/info``, and so do load and uptime. A
    first take kept that whole answer for the hour and handed it to the card:
    two minutes later the load had quadrupled and the card still said 12.5 %,
    on exactly the versions that were to notice nothing."""
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", no_socket)
    current_api("http://truenas.example.com", 404)
    respx.get("http://truenas.example.com/api/v2.0/alert/list").mock(return_value=httpx.Response(200, json=[]))
    info = respx.get("http://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(200, json=OLD_INFO))
    config = {"url": "http://truenas.example.com", "api_key": "2-full"}
    assert (await adapter.fetch("system", config, {}, ctx)).primary["value"] == 12.5
    started = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: started + 120)
    info.mock(return_value=httpx.Response(200, json={**OLD_INFO, "loadavg": [2.0, 1.0, 0.5]}))
    assert (await adapter.fetch("system", config, {}, ctx)).primary["value"] == 50.0


@respx.mock
async def test_behind_a_proxy_the_refusal_names_the_proxy_not_the_address(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """The address is https:// already. What has to change is the proxy that
    does not pass the WebSocket on, and the handshake's status says so."""
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", turned_down(502))
    current_api("https://truenas.example.com", 400)
    rest = respx.get(url__startswith="https://truenas.example.com/api/v2.0/").mock(return_value=httpx.Response(200, json=INFO))
    with pytest.raises(AdapterError) as refused:
        await adapter.fetch("pools", {"url": "https://truenas.example.com", "api_key": "2-full"}, {}, ctx)
    assert not rest.called, "the look without the key was enough; nothing went over REST"
    assert refused.value.code == "deprecated_api"
    assert "HTTP 502" in refused.value.message
    assert "reverse proxy" in refused.value.hint
    assert "https://" not in refused.value.hint


@respx.mock
async def test_a_404_in_front_of_a_current_truenas_is_not_remembered_as_an_old_one(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """404 stands for a TrueNAS without the current API, for an hour. A proxy
    can say 404 as well; the version then shows it is no old TrueNAS, and the
    WebSocket has to be tried again once the proxy is mended."""
    adapter = get_adapter("truenas")
    attempts: list[str] = []

    async def gone(url: str, config: dict[str, Any]) -> Any:
        attempts.append(url)
        return await turned_down(404)(url, config)

    monkeypatch.setattr(adapter, "_open_socket", gone)
    respx.get("https://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(200, json=INFO))
    config = {"url": "https://truenas.example.com", "api_key": "2-full"}
    for _ in range(2):
        with pytest.raises(AdapterError) as refused:
            await adapter.fetch("pools", config, {}, ctx)
        assert "reverse proxy" in refused.value.hint
    assert len(attempts) == 2


@pytest.mark.parametrize(("label", "expected"), [
    ("25.10.1", (25, 10)),
    ("25.04.0", (25, 4)),
    ("TrueNAS-SCALE-24.10.2", (24, 10)),
    ("TrueNAS-13.0-U6.1", (13, 0)),
    ("", (0, 0)),
    ("MASTER", (0, 0)),
])
def test_the_version_is_read_from_every_way_truenas_writes_it(label: str, expected: tuple[int, int]) -> None:
    assert _version(label) == expected


async def test_the_websocket_path_refuses_http_on_its_own(truenas) -> None:
    """The last line in front of a revoked key does not rely on the caller."""
    adapter, fake = truenas
    with pytest.raises(AdapterError):
        await adapter._rpc({"url": "http://truenas.example.com", "api_key": KEY}, ["system.info"])
    assert fake.opened == []


@respx.mock
async def test_an_older_truenas_without_the_current_api_falls_back_to_rest(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    attempts: list[str] = []

    async def gone(url: str, config: dict[str, Any]) -> Any:
        attempts.append(url)
        return await turned_down(404)(url, config)

    monkeypatch.setattr(adapter, "_open_socket", gone)
    respx.get("https://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(200, json={**INFO, "version": "TrueNAS-SCALE-24.10.2"}))
    config = {"url": "https://truenas.example.com", "api_key": KEY}
    assert await adapter.test(config, ctx) == "TrueNAS TrueNAS-SCALE-24.10.2 on truenas answers."
    assert await adapter.test(config, ctx) == "TrueNAS TrueNAS-SCALE-24.10.2 on truenas answers."
    assert len(attempts) == 1, "a TrueNAS without the current API is not asked again within the hour"


@respx.mock
async def test_a_proxy_without_websockets_is_named_when_rest_refuses_too(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", turned_down(400))
    current_api("https://truenas.example.com", 404)
    respx.get("https://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(403))
    with pytest.raises(AdapterError) as refused:
        await adapter.test({"url": "https://truenas.example.com", "api_key": KEY}, ctx)
    assert "HTTP 400" in refused.value.message
    assert "reverse proxy" in refused.value.hint


async def test_the_live_numbers_of_a_24_10_come_in_their_own_shape(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    fake = FakeTruenas(live=LIVE_24_10)
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    system = await adapter.fetch("system", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert system.primary == {"label": "CPU", "value": 8.0, "unit": "%"}
    memory = next(entry for entry in system.secondary if entry["label"] == "Memory")
    # Everything but the unused part: apps, cache, buffers, ARC, page tables and slab.
    assert memory["text"] == "6.0 GB / 7.8 GB" and memory["value"] == 76.8


async def test_without_a_live_event_the_load_average_stands_in(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    fake = FakeTruenas(live=None)
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    monkeypatch.setattr("app.adapters.truenas.REALTIME_WAIT", 0.05)
    # No event ever comes; recv would block for good on a real socket, so the fake raises what a timeout does.
    original = fake.recv

    async def recv() -> str:
        if not fake._pending:
            await asyncio.sleep(1)
        return await original()

    fake.recv = recv  # type: ignore[method-assign]
    system = await adapter.fetch("system", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert system.primary == {"label": "Load", "value": 12.5, "unit": "%"}
    assert next(entry for entry in system.secondary if entry["label"] == "Memory") == {"label": "Memory", "value": "7.8 GB"}


async def test_a_key_that_may_not_read_the_statistics_does_not_hold_the_card_up(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """TrueNAS says the subscription ended; the card takes the load average at
    once rather than waiting out the timeout on every refresh."""
    adapter = get_adapter("truenas")
    fake = FakeTruenas(live=None)
    original = fake.send

    async def send(text: str) -> None:
        await original(text)
        if json.loads(text)["method"] == "core.subscribe":
            fake._pending.append(json.dumps({"jsonrpc": "2.0", "method": "notify_unsubscribed",
                                             "params": {"collection": 'reporting.realtime:{"interval": 2}', "error": {"error": "EACCES"}}}))

    fake.send = send  # type: ignore[method-assign]
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    monkeypatch.setattr("app.adapters.truenas.REALTIME_WAIT", 30.0)
    started = time.monotonic()
    system = await adapter.fetch("system", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert time.monotonic() - started < 5, "it did not wait for an event that will not come"
    assert system.primary == {"label": "Load", "value": 12.5, "unit": "%"}


async def test_the_system_card_names_the_update_on_offer(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    system = await adapter.fetch("system", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert {"label": "Update available", "value": "25.10.8"} in system.secondary


async def test_an_update_check_that_fails_leaves_the_card_and_waits_an_hour(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """``update.status`` asks the update server; a TrueNAS that cannot reach it
    still shows its load, and is not asked again on every refresh."""
    adapter = get_adapter("truenas")
    fake = FakeTruenas(refuse={"update.status": "ENOPKG"})
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    config = {"url": "https://truenas.example.com", "api_key": KEY}
    system = await adapter.fetch("system", config, {}, ctx)
    assert system.primary["label"] == "CPU"
    assert not [entry for entry in system.secondary if entry["label"] == "Update available"]
    ctx.cache.pop("truenas:system.info")
    fake.sent.clear()
    await adapter.fetch("system", config, {}, ctx)
    assert "update.status" not in fake.methods()


async def test_the_pools_say_when_they_were_scrubbed(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    pools = [
        {**POOLS[0], "scan": {"function": "SCRUB", "state": "FINISHED", "end_time": {"$date": 1_789_712_345_000}, "errors": 0, "percentage": 100.0, "pause": None}},
        {**POOLS[0], "name": "fast", "scan": {"function": "SCRUB", "state": "FINISHED", "end_time": {"$date": 1_789_712_345_000}, "errors": 2, "percentage": 100.0, "pause": None}},
        {**POOLS[0], "name": "cold", "scan": {"function": "RESILVER", "state": "SCANNING", "percentage": 41.6, "pause": None}},
        {**POOLS[0], "name": "new", "scan": None},
    ]
    monkeypatch.setitem(ANSWERS, "pool.query", pools)
    fake = FakeTruenas()
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    card = await adapter.fetch("pools", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    found = {item["title"]: (item["subtitle"].split(" · ", 2)[-1] if item["subtitle"].count(" · ") == 2 else "", item["status"]) for item in card.items}
    since = ago(1_789_712_345)
    assert found == {"tank": (f"scrubbed {since} ago", "ok"), "fast": (f"scrubbed {since} ago, 2 errors", "warn"),
                     "cold": ("resilvering 42%", "warn"), "new": ("", "ok")}


async def test_the_disks_come_hottest_first_with_their_own_limit(ctx: Context, truenas) -> None:
    adapter, fake = truenas
    card = await adapter.fetch("disks", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert [(item["title"], item["value"], item["status"]) for item in card.items] == [
        ("sdb WDC WD40EFZX-68AWUN0", "53 °C", "warn"),
        ("sda WDC WD40EFZX-68AWUN0", "38 °C", "ok"),
        ("nvme0n1 Samsung SSD 980 1TB", "", "unknown"),
    ]
    assert card.items[0]["subtitle"] == "tank · 3.6 TB · HDD"
    assert card.items[2]["subtitle"].startswith("not in a pool · ")
    assert card.status == "warn"
    assert card.secondary == [{"label": "Disks", "value": 3}, {"label": "Hottest", "value": "53 °C"}]
    assert fake.methods() == ["auth.login_with_api_key", "disk.query", "disk.temperatures"]


async def test_a_disk_at_its_own_critical_temperature_is_red(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    monkeypatch.setitem(ANSWERS, "disk.temperatures", {"sda": [56.0, 55.0], "sdb": [61.0, None], "nvme0n1": 45.0})
    fake = FakeTruenas()
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    card = await adapter.fetch("disks", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert {item["title"].split()[0]: item["status"] for item in card.items} == {"sda": "bad", "sdb": "bad", "nvme0n1": "ok"}


async def test_the_apps_put_the_crashed_one_first_and_count_the_updates(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    card = await adapter.fetch("apps", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert [(item["title"], item["value"], item["status"]) for item in card.items] == [
        ("immich", "Crashed", "bad"), ("adguard", "Stopped", "unknown"), ("jellyfin", "Running", "ok"),
    ]
    assert card.items[2]["subtitle"] == "10.10.7_1.2.3 · update available"
    assert card.secondary == [{"label": "Running", "value": "1 / 3"}, {"label": "Updates", "value": 1}]
    assert card.status == "bad"


async def test_the_tasks_show_the_failed_replication_with_its_reason(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    card = await adapter.fetch("tasks", {"url": "https://truenas.example.com", "api_key": KEY}, {"limit": 5}, ctx)
    assert [(item["title"], item["value"], item["status"]) for item in card.items] == [
        ("tank/photos - backup/photos", "Failed", "bad"), ("tank/photos", "Done", "ok"),
    ], "the disabled task is left out"
    assert card.items[0]["subtitle"].startswith("Replication · ")
    assert card.items[0]["subtitle"].endswith("No incremental base on dataset 'tank/photos' and replication from scratch is not allowed.")
    assert " ago" in card.items[1]["subtitle"]
    assert card.secondary == [{"label": "Tasks", "value": 2}, {"label": "Failed", "value": 1}]
    assert card.status == "bad"


async def test_the_new_cards_over_rest_say_what_they_need(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """An older TrueNAS, read over REST, has no such calls; the card says so
    rather than asking REST for something it does not have."""
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", turned_down(404))
    with respx.mock(assert_all_called=False) as router:
        router.get("https://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(200, json=OLD_INFO))
        rest = router.get(url__regex=r".*/api/v2\.0/(disk|app|replication|pool/snapshottask).*").mock(return_value=httpx.Response(200, json=[]))
        for kind in ("disks", "apps", "tasks"):
            with pytest.raises(AdapterError) as refused:
                await adapter.fetch(kind, {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
            assert refused.value.code == "needs_current_api"
    assert not rest.called


async def test_an_update_server_out_of_reach_is_an_answer_not_an_error(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    """``update.status`` catches its own failures and answers ``code`` ERROR
    (plugins/update_/status.py); the card shows no update and stays as it is."""
    adapter = get_adapter("truenas")
    monkeypatch.setitem(ANSWERS, "update.status", {"code": "ERROR", "status": None, "update_download_progress": None,
                                                   "error": {"errname": "ENOPKG", "reason": "No releases match specified update profile."}})
    fake = FakeTruenas()
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    system = await adapter.fetch("system", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert system.primary["label"] == "CPU"
    assert not [entry for entry in system.secondary if entry["label"] == "Update available"]


async def test_the_apps_card_is_amber_while_one_is_starting(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    monkeypatch.setitem(ANSWERS, "app.query", [{**APPS[0], "state": "DEPLOYING"}])
    fake = FakeTruenas()
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    card = await adapter.fetch("apps", {"url": "https://truenas.example.com", "api_key": KEY}, {}, ctx)
    assert card.status == "warn"


HTTPS = {"url": "https://truenas.example.com", "api_key": KEY}


async def test_the_interfaces_come_from_the_event_the_system_card_reads_anyway(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    live = {**LIVE, "interfaces": {
        "enp1s0": {"link_state": "LINK_STATE_UP", "speed": 1000, "received_bytes_rate": 1_250_000.0, "sent_bytes_rate": 250_000.0},
        "enp2s0": {"link_state": "LINK_STATE_DOWN", "speed": 0, "received_bytes": 0, "sent_bytes": 0, "received_bytes_rate": 0, "sent_bytes_rate": 0},
    }}
    fake = FakeTruenas(live=live)
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    card = await adapter.fetch("interfaces", HTTPS, {}, ctx)
    assert "network.general.summary" in fake.methods() and "core.subscribe" in fake.methods()
    up, down = card.items
    assert up == {"title": "enp1s0", "subtitle": "192.168.1.20/24 · 1000 Mbit/s", "value": "↓ 1.2 MB/s ↑ 244.1 KB/s", "status": "ok"}
    assert (down["status"], down["subtitle"], down["value"]) == ("unknown", "No link", ""), "a port without a cable is grey, not red"
    assert card.metrics == {"down": 1_250_000.0, "up": 250_000.0}
    assert [(row["metric"], row["value"]) for row in card.secondary] == [("down", "1.2 MB/s"), ("up", "244.1 KB/s")]

    fake.sent.clear()
    ctx.cache.pop("truenas:reporting.realtime")
    await adapter.fetch("interfaces", HTTPS, {}, ctx)
    assert "network.general.summary" not in fake.methods(), "the addresses are kept for five minutes"
    assert "core.subscribe" in fake.methods(), "the traffic is not"


async def test_an_interface_card_without_interfaces_says_so(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    with pytest.raises(AdapterError) as empty:
        await adapter.fetch("interfaces", HTTPS, {}, ctx)
    assert empty.value.code == "no_interfaces", "an empty card would hide that TrueNAS sent none"


async def test_the_services_card_shows_what_is_meant_to_run(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    card = await adapter.fetch("services", HTTPS, {}, ctx)
    assert [(item["title"], item["value"], item["status"]) for item in card.items] == [
        ("NFS", "Stopped", "bad"), ("SMB", "Running", "ok"), ("SSH", "Running", "ok"),
    ], "set to start and stopped is red; running without being set to start still shows"
    every = await adapter.fetch("services", HTTPS, {"all_services": True}, ctx)
    assert ("UPS", "Stopped", "unknown") in [(item["title"], item["value"], item["status"]) for item in every.items]


async def test_vms_and_containers_are_counted_once(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    card = await adapter.fetch("vms", HTTPS, {}, ctx)
    assert [(item["title"], item["subtitle"], item["value"], item["status"]) for item in card.items] == [
        ("homeassistant", "VM · 2 vCPU · 4.0 GB", "Running", "ok"),
        ("pihole", "Container · 1 vCPU · 512.0 MB", "Running", "ok"),
        ("windows", "VM · 4 vCPU · 8.0 GB", "Stopped", "unknown"),
    ]
    assert card.secondary == [{"label": "Running", "value": 2}]


async def test_one_list_of_machines_missing_leaves_the_other(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    fake = FakeTruenas(refuse={"virt.instance.query": "ENOMETHOD"})
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    card = await adapter.fetch("vms", HTTPS, {}, ctx)
    assert [item["title"] for item in card.items] == ["homeassistant", "windows"]


async def test_without_either_list_of_machines_the_card_says_so(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    fake = FakeTruenas(refuse={"vm.query": "ENOMETHOD", "virt.instance.query": "ENOMETHOD"})
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    with pytest.raises(AdapterError) as missing:
        await adapter.fetch("vms", HTTPS, {}, ctx)
    assert "neither" in missing.value.message


async def test_a_refused_role_on_the_machines_is_not_taken_for_a_missing_method(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    fake = FakeTruenas(refuse={"vm.query": "EACCES"})
    monkeypatch.setattr(adapter, "_open_socket", fake.open)
    with pytest.raises(AdapterError) as refused:
        await adapter.fetch("vms", HTTPS, {}, ctx)
    assert refused.value.code == "auth_failed"


async def test_the_cards_from_nexdeck_over_rest_say_what_they_need(ctx: Context, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("truenas")
    monkeypatch.setattr(adapter, "_open_socket", turned_down(404))
    with respx.mock(assert_all_called=False) as router:
        router.get("https://truenas.example.com/api/v2.0/system/info").mock(return_value=httpx.Response(200, json=OLD_INFO))
        for kind in ("interfaces", "services"):
            with pytest.raises(AdapterError) as refused:
                await adapter.fetch(kind, HTTPS, {}, ctx)
            assert refused.value.code == "needs_current_api"
        with pytest.raises(AdapterError) as neither:
            await adapter.fetch("vms", HTTPS, {}, ctx)
        assert "https://" in neither.value.hint


def _data(*items: dict[str, Any], **kwargs: Any) -> WidgetData:
    return WidgetData(items=list(items), **kwargs)


def test_a_disk_is_told_once_when_it_gets_hot() -> None:
    adapter = get_adapter("truenas")
    cool, hot = {"title": "sda", "value": "40 °C", "status": "ok"}, {"title": "sda", "value": "56 °C", "status": "warn", "subtitle": "tank"}
    told = adapter.detect("disks", _data(cool), _data(hot), {})
    assert [(found.event, found.title, found.key) for found in told] == [("disk_hot", "sda is at 56 °C", "disk_hot:sda:warn")]
    assert adapter.detect("disks", _data(hot), _data({**hot, "value": "57 °C"}), {}) == [], "only on the crossing"
    assert len(adapter.detect("disks", None, _data(hot), {})) == 1, "hot when the card first looks, like a full pool"


def test_a_disk_going_from_yellow_to_red_is_told_again() -> None:
    adapter = get_adapter("truenas")
    warm, hot = {"title": "sda", "value": "52 °C", "status": "warn"}, {"title": "sda", "value": "61 °C", "status": "bad"}
    assert [found.level for found in adapter.detect("disks", _data(warm), _data(hot), {})] == ["bad"]
    assert adapter.detect("disks", _data(hot), _data(warm), {}) == [], "cooling down is no news"


async def test_a_failed_task_is_told_when_it_fails_not_when_the_card_first_sees_it(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    card = await adapter.fetch("tasks", HTTPS, {"limit": 5}, ctx)
    failed = card.items[0]
    assert failed["id"] == "replication:1"
    good = {**failed, "status": "ok", "value": "Done"}
    told = adapter.detect("tasks", _data(good), _data(failed), {})
    assert [(found.event, found.title, found.level, found.key) for found in told] == [
        ("backup_failed", "tank/photos - backup/photos failed", "bad", "backup_failed:replication:1")]
    assert adapter.detect("tasks", None, _data(failed), {}) == []
    assert adapter.detect("tasks", _data(failed), _data(failed), {}) == []
    broken = WidgetData(items=[good], error="TrueNAS could not be reached.")
    assert len(adapter.detect("tasks", broken, _data(failed), {})) == 1, "a task that failed while the card was in error is still told"


async def test_an_app_update_is_told_once_per_version(ctx: Context, truenas) -> None:
    adapter, _fake = truenas
    card = await adapter.fetch("apps", HTTPS, {}, ctx)
    waiting = next(item for item in card.items if item["title"] == "jellyfin")
    assert waiting["update"] == "a newer version", "this answer names no latest_version"
    current = {**waiting, "update": ""}
    told = adapter.detect("apps", _data(current), _data(waiting), {})
    assert [(found.event, found.title) for found in told] == [("software_update", "jellyfin: update to a newer version")]
    assert adapter.detect("apps", _data(waiting), _data(waiting), {}) == []
    assert len(adapter.detect("apps", _data(waiting), _data({**waiting, "update": "1.2.5"}), {})) == 1, "a newer one is news again"
    assert adapter.detect("apps", None, _data(waiting), {}) == [], "twenty old updates are no news when the card is added"
    image = {**current, "update": "image"}
    assert [found.title for found in adapter.detect("apps", _data(current), _data(image), {})] == ["jellyfin: new image"]


def test_a_truenas_update_is_told_once() -> None:
    adapter = get_adapter("truenas")
    before = WidgetData(secondary=[{"label": "Alerts", "value": 0}])
    after = WidgetData(secondary=[{"label": "Alerts", "value": 0}, {"label": "Update available", "value": "25.10.8"}])
    assert [found.title for found in adapter.detect("system", before, after, {})] == ["TrueNAS 25.10.8 is available"]
    assert adapter.detect("system", after, after, {}) == []
    assert adapter.detect("system", None, after, {}) == []


@pytest.mark.parametrize("kind", ["interfaces", "services", "vms"])
def test_every_card_from_nexdeck_has_a_demo(kind: str) -> None:
    data = get_adapter("truenas").demo(kind, {}, 3)
    assert data.items, kind
