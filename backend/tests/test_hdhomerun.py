"""HDHomeRun: the tuners and the channels.

``hdhomerun_api.json`` is written anew from SiliconDust's HTTP development guide and, for ``status.json``, from the
fields Homepage's widget reads; no unit was at hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.adapters.hdhomerun import HdhomerunAdapter

BASE = "http://hdhomerun.local"
CONFIG = {"url": BASE}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "hdhomerun_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


@respx.mock
async def test_the_connection_test_names_the_unit() -> None:
    respx.get(f"{BASE}/discover.json").mock(return_value=httpx.Response(200, json=ANSWERS["discover"]))
    message = await get_adapter("hdhomerun").test(CONFIG, _ctx())
    assert message == "HDHomeRun FLEX 4K HDFX-4K, firmware 20260326, 4 tuners."
    assert "made-up-device-auth" not in message


@respx.mock
async def test_something_else_at_the_address() -> None:
    respx.get(f"{BASE}/discover.json").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("hdhomerun").test(CONFIG, _ctx())
    assert refused.value.code == "not_json"


@respx.mock
async def test_the_tuners_in_use_and_their_signal() -> None:
    respx.get(f"{BASE}/status.json").mock(return_value=httpx.Response(200, json=ANSWERS["status"]))
    data = await get_adapter("hdhomerun").fetch("tuners", CONFIG, {}, _ctx())
    assert [(row["title"], row["subtitle"], row.get("value"), row["status"]) for row in data.items] == [
        ("Tuner 1", "7.1 WABC-HD", "86%", "ok"),
        ("Tuner 2", "4.1 WNBC-HD", "64%", "warn"),
        ("Tuner 3", "Free", None, "ok"),
        ("Tuner 4", "Free", None, "ok"),
    ]
    assert data.secondary == [{"label": "In use", "value": "2 / 4"}]
    assert data.status == "ok" and data.metrics == {"in_use": 2.0}


def test_every_tuner_busy_is_yellow() -> None:
    busy = [{"VctNumber": "2.1", "VctName": "A"}, {"VctNumber": "4.1", "VctName": "B"}]
    assert HdhomerunAdapter._tuners(busy).status == "warn"


@respx.mock
async def test_a_unit_without_a_status_page() -> None:
    respx.get(f"{BASE}/status.json").mock(return_value=httpx.Response(404))
    with pytest.raises(AdapterError) as missing:
        await get_adapter("hdhomerun").fetch("tuners", CONFIG, {}, _ctx())
    assert missing.value.code == "no_status"


@respx.mock
async def test_the_channels_are_counted_and_none_is_tuned() -> None:
    route = respx.get(f"{BASE}/lineup.json").mock(return_value=httpx.Response(200, json=ANSWERS["lineup"]))
    data = await get_adapter("hdhomerun").fetch("channels", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Channels", "value": 4}
    assert {row["label"]: row["value"] for row in data.secondary} == {"HD": 3, "Favourites": 2, "Protected": 1}
    assert route.call_count == 1 and len(respx.calls) == 1, "a channel URL tunes a tuner and must never be fetched"


@pytest.mark.parametrize("kind", ["tuners", "channels"])
def test_every_card_has_a_demo(kind: str) -> None:
    data = get_adapter("hdhomerun").demo(kind, {}, 3)
    assert data.primary or data.items
