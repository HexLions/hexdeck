"""DexFrame, against the /api/info answer of DexFrame 2.0 (README "API")."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.adapters.dexframe import DexFrameAdapter, lipo_percent

FIXTURES = Path(__file__).parent / "fixtures"
DF = "http://dexframe.example.com"
INFO: dict[str, Any] = json.loads((FIXTURES / "dexframe_info.json").read_text(encoding="utf-8"))


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def test_it_is_registered_and_in_beta() -> None:
    adapter = get_adapter("dexframe")
    assert isinstance(adapter, DexFrameAdapter) and adapter.beta
    assert [w.kind for w in adapter.widgets] == ["frame", "shiny"]
    assert next(f for f in adapter.fields if f.name == "key").secret


def test_the_frame_card_shows_the_page_the_battery_and_buttons() -> None:
    data = DexFrameAdapter._frame(INFO)
    assert data.status == "ok" and data.primary == {"label": "Page", "value": "3 / 12"}
    assert data.secondary[0] == {"label": "Battery", "value": 59, "unit": "%"}
    assert {"label": "Firmware", "value": "2.0.0"} in data.secondary
    assert [a.id for a in data.actions] == ["prev", "next", "refresh"]
    assert data.metrics == {"battery_percent": 59.0}


def test_a_low_battery_a_usb_board_and_a_busy_screen() -> None:
    assert DexFrameAdapter._frame({**INFO, "batteryMv": 3450}).status == "warn"
    usb = DexFrameAdapter._frame({**INFO, "batteryMv": 12})
    assert usb.secondary[0] == {"label": "Power", "value": "USB"} and usb.metrics == {}
    nosense = DexFrameAdapter._frame({**INFO, "batteryMv": -1})
    assert all(row["label"] not in ("Battery", "Power") for row in nosense.secondary)
    busy = DexFrameAdapter._frame({**INFO, "pending": True})
    assert busy.primary["label"] == "Drawing" and busy.status == "warn"


def test_the_charge_follows_the_lipo_curve() -> None:
    assert lipo_percent(4250) == 100 and lipo_percent(3300) == 0 and lipo_percent(3200) == 0
    assert lipo_percent(3750) == 38 and lipo_percent(4000) == 78


def test_the_shiny_card_needs_a_board_with_a_counter() -> None:
    data = DexFrameAdapter._shiny(INFO)
    assert data.primary == {"label": "Encounters", "value": 1234}
    assert [a.id for a in data.actions] == ["remove", "add"]
    with pytest.raises(AdapterError) as error:
        DexFrameAdapter._shiny({**INFO, "features": "pages"})
    assert error.value.code == "no_counter"


@respx.mock
async def test_fetch_and_test_read_the_info(ctx: Context) -> None:
    respx.get(f"{DF}/api/info").mock(return_value=httpx.Response(200, json=INFO))
    adapter = DexFrameAdapter()
    assert "page 3 of 12" in await adapter.test({"url": DF}, ctx)
    data = await adapter.fetch("shiny", {"url": DF}, {}, ctx)
    assert data.primary["value"] == 1234


@respx.mock
async def test_something_else_at_the_address_is_said_plainly(ctx: Context) -> None:
    respx.get(f"{DF}/api/info").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    with pytest.raises(AdapterError) as error:
        await DexFrameAdapter().fetch("frame", {"url": DF}, {}, ctx)
    assert error.value.code == "not_dexframe"


@respx.mock
async def test_actions_post_with_the_key(ctx: Context) -> None:
    nxt = respx.post(f"{DF}/api/next", params={"key": "s3cret"}).mock(return_value=httpx.Response(202))
    add = respx.post(f"{DF}/api/counter", params={"add": "1", "key": "s3cret"}).mock(return_value=httpx.Response(200, json={"counter": 1235}))
    adapter, config = DexFrameAdapter(), {"url": DF, "key": "s3cret"}
    assert await adapter.action("frame", "next", {}, config, {}, ctx) == "Next page on its way."
    assert await adapter.action("shiny", "add", {}, config, {}, ctx) == "Encounter counted."
    assert nxt.called and add.called


@respx.mock
async def test_a_wrong_key_and_an_unknown_action(ctx: Context) -> None:
    respx.post(f"{DF}/api/refresh").mock(return_value=httpx.Response(403, text="key"))
    adapter = DexFrameAdapter()
    with pytest.raises(AdapterError) as error:
        await adapter.action("frame", "refresh", {}, {"url": DF}, {}, ctx)
    assert error.value.code == "auth_failed"
    with pytest.raises(AdapterError):
        await adapter.action("frame", "reboot", {}, {"url": DF}, {}, ctx)


def test_the_demo_moves() -> None:
    adapter = DexFrameAdapter()
    a, b = adapter.demo("frame", {}, 1), adapter.demo("frame", {}, 40)
    assert a.primary != b.primary
    assert adapter.demo("shiny", {}, 1).primary["value"] != adapter.demo("shiny", {}, 30).primary["value"]
