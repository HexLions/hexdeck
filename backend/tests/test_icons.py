"""The logo proxy: what it fetches, what it caches, and the PNG-only services."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.services import icons

DASHBOARD = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons"
SELFHST = "https://cdn.jsdelivr.net/gh/selfhst/icons"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'


@pytest.fixture(autouse=True)
def _fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cache directory of its own, and no memory of earlier misses."""
    monkeypatch.setattr(icons, "_cache_dir", lambda: tmp_path)
    icons._negative.clear()


@respx.mock
async def test_a_logo_that_exists_as_svg_is_served_as_svg() -> None:
    respx.get(f"{DASHBOARD}/svg/radarr.svg").mock(return_value=httpx.Response(200, content=SVG))
    content, kind = await icons.fetch_icon("radarr", "svg")
    assert content == SVG and kind == "image/svg+xml"


@respx.mock
async def test_a_logo_the_collections_have_only_as_png_is_served_anyway() -> None:
    """⚠️ The interface asks for .svg for every logo, so a PNG-only service
    used to draw the grey box that means "no such logo"."""
    respx.get(f"{DASHBOARD}/svg/urbackup.svg").mock(return_value=httpx.Response(404))
    respx.get(f"{SELFHST}/svg/urbackup.svg").mock(return_value=httpx.Response(404))
    respx.get(f"{DASHBOARD}/png/urbackup.png").mock(return_value=httpx.Response(200, content=PNG))
    content, kind = await icons.fetch_icon("urbackup", "svg")
    assert content == PNG and kind == "image/png", "the answer says what it really is"


@respx.mock
async def test_the_second_collection_is_asked_when_the_first_has_nothing() -> None:
    respx.get(f"{DASHBOARD}/svg/shelly.svg").mock(return_value=httpx.Response(404))
    respx.get(f"{SELFHST}/svg/shelly.svg").mock(return_value=httpx.Response(200, content=SVG))
    content, kind = await icons.fetch_icon("shelly", "svg")
    assert content == SVG and kind == "image/svg+xml"


@respx.mock
async def test_a_name_no_collection_has_is_nothing_in_either_shape() -> None:
    for shape, where in (("svg", DASHBOARD), ("svg", SELFHST), ("png", DASHBOARD), ("png", SELFHST)):
        respx.get(f"{where}/{shape}/not-a-service.{shape}").mock(return_value=httpx.Response(404))
    assert await icons.fetch_icon("not-a-service", "svg") is None


@respx.mock
async def test_a_name_that_was_missing_a_moment_ago_is_not_asked_for_again() -> None:
    svg = respx.get(f"{DASHBOARD}/svg/nothing-here.svg").mock(return_value=httpx.Response(404))
    respx.get(f"{SELFHST}/svg/nothing-here.svg").mock(return_value=httpx.Response(404))
    png = respx.get(f"{DASHBOARD}/png/nothing-here.png").mock(return_value=httpx.Response(200, content=PNG))
    assert (await icons.fetch_icon("nothing-here", "svg"))[1] == "image/png"
    content, _kind = await icons.fetch_icon("nothing-here", "svg")
    assert content == PNG, "the PNG is still found while the SVG miss is remembered"
    assert svg.call_count == 1 and png.call_count == 1, "the second call came out of the cache and the miss list"


async def test_a_made_up_name_is_refused_before_anything_is_fetched() -> None:
    assert await icons.fetch_icon("../../etc/passwd", "svg") is None
    assert await icons.fetch_icon("radarr", "exe") is None
