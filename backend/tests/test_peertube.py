"""PeerTube: the instance and what it published last.

``peertube_api.json`` holds what Framatube (PeerTube 8.3.1) answered on
2026-10-15 without signing in, trimmed to the fields the cards read: the
server stats, the instance's name, the version and its four newest videos.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from app.adapters import get_adapter
from app.adapters.base import Context

BASE = "https://framatube.org"
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "peertube_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _peertube() -> None:
    respx.get(f"{BASE}/api/v1/server/stats").mock(return_value=httpx.Response(200, json=ANSWERS["stats"]))
    respx.get(f"{BASE}/api/v1/config/about").mock(return_value=httpx.Response(200, json=ANSWERS["about"]))
    respx.get(f"{BASE}/api/v1/config").mock(return_value=httpx.Response(200, json=ANSWERS["config"]))
    respx.get(f"{BASE}/api/v1/videos").mock(return_value=httpx.Response(200, json=ANSWERS["videos"]))


@respx.mock
async def test_the_instance_counts_its_own_videos() -> None:
    _peertube()
    data = await get_adapter("peertube").fetch("instance", {"url": BASE}, {}, _ctx())
    stats = ANSWERS["stats"]
    assert data.primary == {"label": "Videos", "value": stats["totalLocalVideos"], "metric": "videos"}
    assert stats["totalVideos"] > stats["totalLocalVideos"], "the federated ones are not the instance's"
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values["Views"] == stats["totalLocalVideoViews"] and values["Followers"] == stats["totalInstanceFollowers"]
    waiting = stats["totalAbuses"] - stats["totalAbusesProcessed"]
    assert values.get("Reports open") == waiting and data.status == ("warn" if waiting else "ok")


@respx.mock
async def test_the_videos_published_last_open_the_video() -> None:
    _peertube()
    data = await get_adapter("peertube").fetch("latest", {"url": BASE}, {"limit": 4}, _ctx())
    first = ANSWERS["videos"]["data"][0]
    assert data.items[0]["title"] == first["name"] and data.items[0]["url"] == first["url"]
    assert data.items[0]["subtitle"].startswith(first["channel"]["displayName"])
    query = respx.calls[-1].request.url.params
    assert (query["isLocal"], query["sort"], query["count"]) == ("true", "-publishedAt", "4")


@respx.mock
async def test_the_connection_test_names_the_instance() -> None:
    _peertube()
    assert await get_adapter("peertube").test({"url": BASE}, _ctx()) == "Framatube answers, PeerTube 8.3.1."
