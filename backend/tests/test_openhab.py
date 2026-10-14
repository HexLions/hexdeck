"""openHAB: Things that are offline, picked Items, and the switch.

``openhab_rest.json`` holds what openHAB 5.2.3 answered locally on
2026-10-13: the root with the version, ten Items of eight types (one never
set) and four Things of the Network binding, one of them disabled. The
OFFLINE Thing in the tests is written in the shape of ``statusInfo``; no
Network Thing went offline locally. The address is ``openhab.local:8080``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://openhab.local:8080"
CONFIG = {"url": BASE, "token": "oh.hexdeck.made-up"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "openhab_rest.json").read_text())
PICKED = {"items": "gLights, Living_Temperature, Living_Humidity, Front_Door, Bedroom_Blind, Never_Set, Not_There"}
OFFLINE = {"statusInfo": {"status": "OFFLINE", "statusDetail": "COMMUNICATION_ERROR", "description": "No response from the device"},
           "editable": True, "label": "Garage door", "UID": "mqtt:topic:garage", "thingTypeUID": "mqtt:topic"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _openhab(things: list | None = None) -> None:
    respx.get(f"{BASE}/rest/").mock(return_value=httpx.Response(200, json=ANSWERS["root"]))
    respx.get(f"{BASE}/rest/items").mock(return_value=httpx.Response(200, json=ANSWERS["items"]))

    def thing_list(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer oh.hexdeck.made-up":
            return httpx.Response(401)
        return httpx.Response(200, json=ANSWERS["things"] if things is None else things)

    respx.get(f"{BASE}/rest/things").mock(side_effect=thing_list)


@respx.mock
async def test_all_things_online_but_one_disabled() -> None:
    _openhab()
    data = await get_adapter("openhab").fetch("things", CONFIG, {}, _ctx())
    assert data.status == "ok" and data.items == []
    assert data.secondary == [{"label": "Online", "value": 3}, {"label": "Offline", "value": 0}]
    everything = await get_adapter("openhab").fetch("things", CONFIG, {"only_problems": False}, _ctx())
    assert everything.items[-1]["title"] == "Printer" and everything.items[-1]["subtitle"] == "disabled"


@respx.mock
async def test_an_offline_thing_comes_first_with_its_reason() -> None:
    _openhab([*ANSWERS["things"], OFFLINE])
    data = await get_adapter("openhab").fetch("things", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.items == [{"id": "mqtt:topic:garage", "title": "Garage door",
                           "subtitle": "offline · communication error · No response from the device", "status": "bad"}]


@respx.mock
async def test_the_picked_items_with_a_group_for_its_members() -> None:
    _openhab()
    data = await get_adapter("openhab").fetch("items", {"url": BASE}, PICKED, _ctx())
    shown = {row["title"]: row["value"] for row in data.items}
    assert shown == {"Porch light": "on", "Kitchen light": "on", "Living room": "21.4 °C", "Living humidity": "48 %",
                     "Front door": "open", "Bedroom blind": "35 %", "Never set": "–"}
    kitchen = next(row for row in data.items if row["title"] == "Kitchen light")
    assert kitchen["actions"][0]["id"] == "off" and kitchen["actions"][0]["params"] == {"item": "Kitchen_Light"}
    assert next(row for row in data.items if row["title"] == "Never set")["actions"] == [], "no switch for a state nobody knows"


@respx.mock
async def test_the_switch_sends_a_command_to_a_listed_switch_only() -> None:
    _openhab()
    command = respx.post(f"{BASE}/rest/items/Porch_Light").mock(return_value=httpx.Response(200))
    assert await get_adapter("openhab").action("items", "on", {"item": "Porch_Light"}, CONFIG, PICKED, _ctx()) == "Porch_Light switched on."
    assert command.calls[0].request.content == b"ON"
    assert command.calls[0].request.headers["content-type"] == "text/plain"
    for name in ("Front_Door", "Washer_State"):
        with pytest.raises(AdapterError):
            await get_adapter("openhab").action("items", "on", {"item": name}, CONFIG, PICKED, _ctx())


@respx.mock
async def test_things_need_a_token_and_a_wrong_one_is_refused() -> None:
    _openhab()
    with pytest.raises(AuthFailed, match="only with an API token"):
        await get_adapter("openhab").fetch("things", {"url": BASE}, {}, _ctx())
    with pytest.raises(AuthFailed, match="refused"):
        await get_adapter("openhab").fetch("things", {**CONFIG, "token": "oh.hexdeck.wrong"}, {}, _ctx())


@respx.mock
async def test_the_connection_test() -> None:
    _openhab()
    assert await get_adapter("openhab").test(CONFIG, _ctx()) == "openHAB 5.2.3 answers with 10 Items and 4 Things."
    assert await get_adapter("openhab").test({"url": BASE}, _ctx()) == "openHAB 5.2.3 answers with 10 Items; the Things need an API token."
