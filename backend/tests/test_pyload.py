"""pyLoad: speed, the downloads running, and the links that failed.

``pyload_status.json`` holds the answers of pyload-ng 0.5.0b3.dev101 running
locally on 2026-10-10 with an API key: one download running at a 2 MiB/s
limit and three links that failed because pyLoad refuses its own network.
Basic auth was checked the same day against 0.5.0b3.dev95, which has no API
keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://pyload.local:8000"
CONFIG = {"url": BASE, "api_key": "pl_made-up-key"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "pyload_status.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _pyload(status: dict | None = None) -> None:
    for method, answer in (("status_server", status or ANSWERS["status_server"]), ("status_downloads", ANSWERS["status_downloads"]),
                           ("get_queue_data", ANSWERS["queue_data"]), ("get_server_version", ANSWERS["version"]),
                           ("free_space", ANSWERS["free_space"])):
        respx.get(f"{BASE}/api/{method}").mock(return_value=httpx.Response(200, json=answer))


@respx.mock
async def test_the_key_goes_in_as_x_api_key() -> None:
    _pyload()
    await get_adapter("pyload").test(CONFIG, _ctx())
    request = respx.calls[0].request
    assert request.headers["x-api-key"] == "pl_made-up-key" and "authorization" not in request.headers


@respx.mock
async def test_older_versions_get_basic_auth() -> None:
    _pyload()
    await get_adapter("pyload").test({"url": BASE, "username": "pyload", "password": "secret"}, _ctx())
    request = respx.calls[0].request
    assert request.headers["authorization"].startswith("Basic ") and "x-api-key" not in request.headers


@respx.mock
async def test_the_status() -> None:
    _pyload()
    data = await get_adapter("pyload").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "ok"
    assert data.primary["label"] == "Speed" and data.primary["value"].endswith("/s")
    assert {"label": "Active", "value": 1} in data.secondary
    assert {"label": "Queue", "value": 4} in data.secondary
    assert data.metrics["speed"] == float(ANSWERS["status_server"]["speed"])


@respx.mock
async def test_paused_with_a_captcha_waiting() -> None:
    _pyload({**ANSWERS["status_server"], "pause": True, "captcha": True})
    data = await get_adapter("pyload").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "warn" and data.primary["value"] == "paused"
    assert data.meta["notice"] == "A captcha is waiting."


@respx.mock
async def test_the_running_download_then_the_failed_links() -> None:
    _pyload()
    data = await get_adapter("pyload").fetch("downloads", CONFIG, {}, _ctx())
    running, *failed = data.items
    assert running["title"] == "100Mb.dat" and running["progress"] == float(ANSWERS["status_downloads"][0]["percent"]) and running["status"] == "ok"
    assert "Test file" in running["subtitle"] and "/s" in running["subtitle"]
    assert [row["title"] for row in failed] == ["big-file.bin", "other-file.bin", "missing-file.bin"]
    assert all(row["status"] == "bad" and "Refusing to download" in row["subtitle"] for row in failed)
    assert data.status == "warn"


@respx.mock
async def test_without_the_failed_links_the_queue_is_not_asked() -> None:
    _pyload()
    data = await get_adapter("pyload").fetch("downloads", CONFIG, {"failed": False}, _ctx())
    assert [row["title"] for row in data.items] == ["100Mb.dat"]
    assert not any(call.request.url.path.endswith("get_queue_data") for call in respx.calls)


@pytest.mark.parametrize(("config", "said"), [(CONFIG, "API key"), ({"url": BASE, "username": "pyload", "password": "x"}, "user name")])
@respx.mock
async def test_a_refusal(config: dict, said: str) -> None:
    respx.get(f"{BASE}/api/status_server").mock(return_value=httpx.Response(401, json={"error": "Invalid API credentials"}))
    with pytest.raises(AuthFailed, match=said):
        await get_adapter("pyload").test(config, _ctx())
