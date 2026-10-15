"""Lemmy: the instance and what is hot in its communities.

``lemmy_api.json`` holds what lemmy.ml (Lemmy 0.19.20) answered on
2026-10-15 without signing in, trimmed to the fields the cards read: the
site with its counts, and four hot local posts, one of them a text post
without a ``url``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

BASE = "https://lemmy.ml"
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "lemmy_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _lemmy(posts: dict | None = None) -> None:
    respx.get(f"{BASE}/api/v3/site").mock(return_value=httpx.Response(200, json=ANSWERS["site"]))
    respx.get(f"{BASE}/api/v3/post/list").mock(return_value=httpx.Response(200, json=posts or ANSWERS["posts"]))


@respx.mock
async def test_the_instance() -> None:
    _lemmy()
    data = await get_adapter("lemmy").fetch("site", {"url": BASE}, {}, _ctx())
    counts = ANSWERS["site"]["site_view"]["counts"]
    assert data.primary == {"label": "Active today", "value": counts["users_active_day"], "metric": "active_day"}
    assert {"label": "Communities", "value": counts["communities"]} in data.secondary


@respx.mock
async def test_the_hot_posts_open_here() -> None:
    _lemmy()
    data = await get_adapter("lemmy").fetch("hot", {"url": BASE}, {"limit": 4}, _ctx())
    first = ANSWERS["posts"]["posts"][0]
    assert data.items[0]["title"] == first["post"]["name"]
    assert data.items[0]["url"] == f"{BASE}/post/{first['post']['id']}", "here, not at the post's home instance"
    assert len(data.items) == 4, "a text post without a link is a post all the same"
    query = respx.calls[-1].request.url.params
    assert (query["type_"], query["sort"]) == ("Local", "Hot")


@respx.mock
async def test_an_nsfw_post_is_left_out() -> None:
    posts = json.loads(json.dumps(ANSWERS["posts"]))
    posts["posts"][0]["post"]["nsfw"] = True
    _lemmy(posts)
    data = await get_adapter("lemmy").fetch("hot", {"url": BASE}, {}, _ctx())
    assert posts["posts"][0]["post"]["name"] not in [row["title"] for row in data.items]


@respx.mock
async def test_a_version_without_v3_is_said() -> None:
    respx.get(f"{BASE}/api/v3/site").mock(return_value=httpx.Response(404))
    with pytest.raises(AdapterError, match="no Lemmy API v3"):
        await get_adapter("lemmy").test({"url": BASE}, _ctx())
