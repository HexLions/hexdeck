"""Owncast: live or not, since when, and the viewers.

``owncast_status.json`` holds ``/api/status`` and ``/api/admin/status`` as
Owncast 0.3.0 answered them locally on 2026-10-10, live from a test pattern
pushed over RTMP with two viewers pinging, and offline after it stopped.
The broadcaster's address was replaced with a documentation one.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "http://owncast.local:8080"
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "owncast_status.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _owncast(state: str) -> None:
    respx.get(f"{BASE}/api/status").mock(return_value=httpx.Response(200, json=ANSWERS[f"status_{state}"]))
    respx.get(f"{BASE}/api/admin/status").mock(return_value=httpx.Response(200, json=ANSWERS[f"admin_{state}"]))


@respx.mock
async def test_live_with_the_admin_password() -> None:
    _owncast("live")
    data = await get_adapter("owncast").fetch("stream", {"url": BASE, "admin_password": "made-up"}, {}, _ctx())
    assert data.status == "ok" and data.primary == {"label": "Stream", "value": "live"}
    assert {"label": "Viewers", "value": 2, "metric": "viewers"} in data.secondary
    assert {"label": "Peak", "value": 3} in data.secondary
    assert {"label": "Sending", "value": "360p · 781 kbit/s"} in data.secondary
    assert {"label": "Title", "value": "Test pattern"} in data.secondary
    assert respx.calls[-1].request.headers["authorization"].startswith("Basic ")
    assert "192.0.2.10" not in json.dumps(data.secondary)


@respx.mock
async def test_live_without_it_asks_only_the_public_status() -> None:
    _owncast("live")
    data = await get_adapter("owncast").fetch("stream", {"url": BASE}, {}, _ctx())
    assert data.primary["value"] == "live" and not any(item["label"] == "Viewers" for item in data.secondary)
    assert [call.request.url.path for call in respx.calls] == ["/api/status"]
    assert data.metrics == {}


@respx.mock
async def test_offline_shows_when_it_was_last_live() -> None:
    _owncast("offline")
    data = await get_adapter("owncast").fetch("stream", {"url": BASE, "admin_password": "made-up"}, {}, _ctx())
    assert data.status == "unknown" and data.primary["value"] == "offline"
    assert any(item["label"] == "Last live" for item in data.secondary)
    assert {"label": "Peak", "value": 3} in data.secondary
    assert not any(item["label"] == "Sending" for item in data.secondary)


@respx.mock
async def test_a_wrong_admin_password() -> None:
    respx.get(f"{BASE}/api/status").mock(return_value=httpx.Response(200, json=ANSWERS["status_offline"]))
    respx.get(f"{BASE}/api/admin/status").mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(AuthFailed):
        await get_adapter("owncast").test({"url": BASE, "admin_password": "wrong"}, _ctx())
