"""Shelly: both generations, the unit trap in Gen 1, and the buttons.

The two status documents below are the shapes the devices really answer with,
trimmed to what the cards read.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

URL = "http://192.168.1.50"
CONFIG = {"url": URL}

#: A Shelly 2.5: two relays, two meters, watt-minutes in "total".
GEN1 = {
    "relays": [{"ison": True, "overpower": False}, {"ison": False, "overpower": False}],
    "meters": [{"power": 62.4, "total": 1_200_000, "is_valid": True}, {"power": 0.0, "total": 60_000, "is_valid": True}],
    "temperature": 44.6,
    "overtemperature": False,
    "uptime": 640000,
    "wifi_sta": {"rssi": -58, "ip": "192.168.1.50"},
    "update": {"has_update": True, "new_version": "20260901-1",  "old_version": "20260501-1"},
}
#: A Shelly Plus 1PM: one switch component, watt-hours in "aenergy".
GEN2 = {
    "switch:0": {"id": 0, "output": True, "apower": 62.4, "voltage": 231.1,
                 "aenergy": {"total": 20000.0}, "temperature": {"tC": 44.6}, "errors": []},
    "sys": {"uptime": 640000, "restart_required": False, "available_updates": {"stable": {"version": "1.4.4"}}},
    "wifi": {"rssi": -58, "sta_ip": "192.168.1.50"},
}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _gen1() -> None:
    respx.get(f"{URL}/shelly").mock(return_value=httpx.Response(200, json={"type": "SHSW-25", "fw": "20260501-1", "auth": False}))
    respx.get(f"{URL}/status").mock(return_value=httpx.Response(200, json=GEN1))


def _gen2(status: dict | None = None) -> None:
    respx.get(f"{URL}/shelly").mock(return_value=httpx.Response(200, json={"name": "Boiler", "gen": 3, "model": "S3SW-001P8EU", "auth_en": False}))
    respx.get(f"{URL}/rpc/Shelly.GetStatus").mock(return_value=httpx.Response(200, json=status or GEN2))


@respx.mock
async def test_gen1_watt_minutes_become_kilowatt_hours() -> None:
    _gen1()
    data = await get_adapter("shelly").fetch("device", CONFIG, {}, _ctx())
    assert data.primary["value"] == 62.4 and data.primary["unit"] == "W"
    labels = {row["label"]: row["value"] for row in data.secondary}
    # 1,200,000 watt-minutes are 20,000 watt-hours are 20 kWh, not 1200.
    assert labels["Energy"] == 20.0
    assert labels["Relay"] == "on" and labels["Temp"] == 44.6
    assert labels["Firmware"] == "20260901-1"
    assert data.metrics["power"] == 62.4 and data.metrics["energy"] == 20.0


@respx.mock
async def test_gen2_watt_hours_become_the_same_number() -> None:
    _gen2()
    data = await get_adapter("shelly").fetch("device", CONFIG, {}, _ctx())
    assert data.primary["value"] == 62.4
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Energy"] == 20.0, "the two generations must read the same on the same device"
    assert labels["Firmware"] == "1.4.4"
    assert data.meta["generation"] == 3


@respx.mock
async def test_the_button_does_the_opposite_of_what_the_relay_is_doing() -> None:
    _gen1()
    on = await get_adapter("shelly").fetch("device", CONFIG, {}, _ctx())
    assert [action.id for action in on.actions] == ["off"]
    second = await get_adapter("shelly").fetch("device", CONFIG, {"channel": 1}, _ctx())
    assert [action.id for action in second.actions] == ["on"]
    assert second.primary["value"] == 0.0, "the second channel has its own meter"


@respx.mock
async def test_a_card_can_be_read_only() -> None:
    _gen1()
    data = await get_adapter("shelly").fetch("device", CONFIG, {"switching": False}, _ctx())
    assert data.actions == []


@respx.mock
async def test_switching_speaks_the_right_dialect_for_the_generation() -> None:
    _gen1()
    turn = respx.get(f"{URL}/relay/0").mock(return_value=httpx.Response(200, json={"ison": False}))
    said = await get_adapter("shelly").action("device", "off", {"channel": 0}, CONFIG, {}, _ctx())
    assert turn.call_count == 1 and turn.calls[0].request.url.params["turn"] == "off"
    assert "switched off" in said

    respx.reset()
    _gen2()
    rpc = respx.get(f"{URL}/rpc/Switch.Set").mock(return_value=httpx.Response(200, json={"was_on": False}))
    await get_adapter("shelly").action("device", "on", {"channel": 0}, CONFIG, {}, _ctx())
    assert rpc.call_count == 1 and rpc.calls[0].request.url.params["on"] == "true"

    respx.reset()
    _gen2()
    toggle = respx.get(f"{URL}/rpc/Switch.Toggle").mock(return_value=httpx.Response(200, json={"was_on": True}))
    await get_adapter("shelly").action("device", "toggle", {"channel": 1}, CONFIG, {}, _ctx())
    assert toggle.call_count == 1 and toggle.calls[0].request.url.params["id"] == "1"


@respx.mock
async def test_an_unknown_action_is_refused() -> None:
    _gen1()
    with pytest.raises(AdapterError):
        await get_adapter("shelly").action("device", "reboot", {}, CONFIG, {}, _ctx())


@respx.mock
async def test_overpower_is_red_and_says_so() -> None:
    _gen1()
    respx.get(f"{URL}/status").mock(return_value=httpx.Response(200, json={
        **GEN1, "relays": [{"ison": True, "overpower": True}], "meters": [{"power": 3100.0, "total": 0}]}))
    data = await get_adapter("shelly").fetch("device", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.meta["alarm"] == "Overpower"


@respx.mock
async def test_a_meter_without_a_relay_offers_no_button() -> None:
    _gen1()
    respx.get(f"{URL}/status").mock(return_value=httpx.Response(200, json={
        "relays": [], "emeters": [{"power": 412.0, "total": 154_000.0}, {"power": 38.0, "total": 9_000.0}]}))
    data = await get_adapter("shelly").fetch("channels", CONFIG, {}, _ctx())
    assert [item["title"] for item in data.items] == ["Clamp 0", "Clamp 1"]
    assert all(item["actions"] == [] for item in data.items), "there is nothing to switch on a clamp"
    assert data.primary["value"] == 450.0, "the card adds the clamps up"


@respx.mock
async def test_every_output_gets_a_row_and_a_button() -> None:
    _gen2({**GEN2, "switch:1": {"id": 1, "output": False, "apower": 0.0, "aenergy": {"total": 3114.0}, "errors": []}})
    data = await get_adapter("shelly").fetch("channels", CONFIG, {}, _ctx())
    assert [item["title"] for item in data.items] == ["Output 0", "Output 1"]
    assert [item["actions"][0]["id"] for item in data.items] == ["off", "on"]


@respx.mock
async def test_the_three_phase_total_is_a_row_of_its_own() -> None:
    _gen2({"em:0": {"a_act_power": 120.0, "b_act_power": 80.0, "c_act_power": 40.0, "total_act_power": 240.0},
           "sys": {"uptime": 10, "restart_required": False}})
    data = await get_adapter("shelly").fetch("channels", CONFIG, {}, _ctx())
    assert any(item["title"] == "Total" and item["value"] == "240.0 W" for item in data.items)


@respx.mock
async def test_a_password_protected_gen2_device_is_told_what_is_missing() -> None:
    respx.get(f"{URL}/shelly").mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("shelly").fetch("device", CONFIG, {}, _ctx())
    assert failure.value.code == "auth_failed" and "digest" in str(failure.value.hint).lower()


@respx.mock
async def test_something_that_is_not_a_shelly_says_so() -> None:
    respx.get(f"{URL}/shelly").mock(return_value=httpx.Response(200, text="<html>router</html>"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("shelly").fetch("device", CONFIG, {}, _ctx())
    assert failure.value.code in ("not_json", "not_shelly")


@respx.mock
async def test_the_test_button_names_the_device() -> None:
    _gen2()
    said = await get_adapter("shelly").test(CONFIG, _ctx())
    assert "Boiler" in said and "generation 3" in said


def test_the_demo_draws() -> None:
    assert get_adapter("shelly").demo("device", {}, 0).primary
    assert get_adapter("shelly").demo("channels", {}, 4).items
