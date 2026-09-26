"""OpenWrt: the ubus session, the fixed-point load, and the three answers it gives.

The fake below is a bus: it routes by object and method the way uhttpd's ubus
plugin does, answers ``[6]`` for a call outside the session's rights, and hands
out a token that can be made to expire.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

URL = "http://192.168.1.1"
BUS = f"{URL}/ubus"
CONFIG = {"url": URL, "username": "root", "password": "secret"}
TOKEN = "c1ed6c7b025d0caca723a816fa61b668"

BOARD = {"kernel": "6.6.73", "hostname": "router", "model": "GL.iNet GL-MT6000",
         "release": {"distribution": "OpenWrt", "version": "24.10.1", "description": "OpenWrt 24.10.1 r28597"}}
INFO = {"uptime": 812_344, "load": [7864, 5242, 3932],  # 0.12, 0.08, 0.06 scaled by 65536
        "memory": {"total": 536_870_912, "free": 210_000_000, "available": 268_435_456}, "swap": {"total": 0, "free": 0}}
WAN = {"up": True, "pending": False, "autostart": True, "uptime": 812_344, "l3_device": "eth1", "proto": "dhcp",
       "ipv4-address": [{"address": "84.112.7.19", "mask": 22}]}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _bus(answers: dict[tuple[str, str], Any] | None = None, token: str = TOKEN,
         accepts: list[str] | None = None, password: str = "secret") -> dict[str, int]:
    """A bus that routes calls, with a session it may be told to forget."""
    counted: dict[str, int] = {}
    allowed = accepts if accepts is not None else [token]
    routed: dict[tuple[str, str], Any] = {
        ("system", "board"): BOARD,
        ("system", "info"): INFO,
        ("network.interface.wan", "status"): WAN,
        ("iwinfo", "devices"): {"devices": ["phy0-ap0", "phy1-ap0"]},
        **(answers or {}),
    }

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        session, obj, method, arguments = body["params"]
        counted[f"{obj}.{method}"] = counted.get(f"{obj}.{method}", 0) + 1
        if (obj, method) == ("session", "login"):
            if arguments.get("password") != password:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": [0, {}]})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": [
                0, {"ubus_rpc_session": token, "timeout": 300, "expires": 299}]})
        if session not in allowed:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": [6]})
        if (obj, method) == ("iwinfo", "assoclist"):
            stations = {"phy0-ap0": [{"signal": -47}, {"signal": -72}], "phy1-ap0": [{"signal": -55}]}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": [0, {"results": stations.get(arguments.get("device"), [])}]})
        if (obj, method) in routed:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": [0, routed[(obj, method)]]})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": [4]})

    respx.post(BUS).mock(side_effect=answer)
    return counted


@respx.mock
async def test_the_load_is_divided_by_the_scale_the_kernel_uses() -> None:
    _bus()
    data = await get_adapter("openwrt").fetch("system", CONFIG, {}, _ctx())
    assert data.primary["value"] == 0.12, "7864 is not a load of 7864"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Load"] == "0.12 0.08 0.06"
    assert data.metrics["load"] == 0.12


@respx.mock
async def test_memory_counts_what_a_program_could_still_get() -> None:
    _bus()
    data = await get_adapter("openwrt").fetch("system", CONFIG, {}, _ctx())
    labels = {row["label"]: row["value"] for row in data.secondary}
    # 512 MB total, 256 MB available: half, not the 61 per cent that "free" suggests.
    assert labels["Memory"] == 50.0
    assert data.metrics["memory"] == 50.0
    assert data.meta["model"] == "GL.iNet GL-MT6000" and labels["Release"] == "24.10.1"


@respx.mock
async def test_the_wan_card_shows_the_address_and_the_device() -> None:
    _bus()
    data = await get_adapter("openwrt").fetch("wan", CONFIG, {}, _ctx())
    assert data.primary["value"] == "up" and data.status == "ok"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Address"] == "84.112.7.19" and labels["Device"] == "eth1" and labels["Protocol"] == "dhcp"


@respx.mock
async def test_an_uplink_that_is_down_is_red_and_one_that_is_switched_off_is_not() -> None:
    _bus({("network.interface.wan", "status"): {"up": False, "pending": False, "autostart": True}})
    assert (await get_adapter("openwrt").fetch("wan", CONFIG, {}, _ctx())).status == "bad"
    _bus({("network.interface.wan", "status"): {"up": False, "pending": False, "autostart": False}})
    assert (await get_adapter("openwrt").fetch("wan", CONFIG, {}, _ctx())).status == "warn"
    _bus({("network.interface.wan", "status"): {"up": False, "pending": True, "autostart": True}})
    dialling = await get_adapter("openwrt").fetch("wan", CONFIG, {}, _ctx())
    assert dialling.status == "warn" and dialling.primary["value"] == "pending"


@respx.mock
async def test_another_interface_can_be_asked_for_by_name() -> None:
    counted = _bus({("network.interface.wan6", "status"): {"up": True, "ipv6-address": [{"address": "2001:db8::1"}]}})
    data = await get_adapter("openwrt").fetch("wan", CONFIG, {"interface": "wan6"}, _ctx())
    assert counted["network.interface.wan6.status"] == 1
    assert data.secondary[0]["value"] == "2001:db8::1"


@respx.mock
async def test_an_interface_that_does_not_exist_is_named() -> None:
    _bus()
    with pytest.raises(AdapterError) as failure:
        await get_adapter("openwrt").fetch("wan", CONFIG, {"interface": "iot"}, _ctx())
    assert failure.value.code == "no_such_object"


@respx.mock
async def test_the_radios_are_asked_one_by_one() -> None:
    counted = _bus()
    data = await get_adapter("openwrt").fetch("wireless", CONFIG, {}, _ctx())
    assert [item["title"] for item in data.items] == ["phy0-ap0", "phy1-ap0"]
    assert [item["value"] for item in data.items] == ["2", "1"]
    assert "weakest -72 dBm" == data.items[0]["subtitle"]
    assert data.primary["value"] == 3 and counted["iwinfo.assoclist"] == 2


@respx.mock
async def test_a_router_without_radios_says_so_rather_than_nothing() -> None:
    _bus({("iwinfo", "devices"): {"devices": []}})
    data = await get_adapter("openwrt").fetch("wireless", CONFIG, {}, _ctx())
    assert data.items == [] and "wired" in data.meta["empty"]


@respx.mock
async def test_the_session_is_asked_for_once_and_taken_up_again_when_it_expires() -> None:
    counted = _bus()
    ctx = _ctx()
    await get_adapter("openwrt").fetch("system", CONFIG, {}, ctx)
    await get_adapter("openwrt").fetch("wan", CONFIG, {}, ctx)
    assert counted["session.login"] == 1, "one login for both cards"

    counted = _bus(accepts=["a-newer-token"], token="a-newer-token")
    data = await get_adapter("openwrt").fetch("wan", CONFIG, {}, ctx)
    assert counted["session.login"] == 1, "the denied call logged in again"
    assert data.primary["value"] == "up"


@respx.mock
async def test_a_wrong_password_is_named_as_such() -> None:
    _bus(password="something else")
    with pytest.raises(AdapterError) as failure:
        await get_adapter("openwrt").fetch("system", CONFIG, {}, _ctx())
    assert failure.value.code == "auth_failed"


@respx.mock
async def test_a_user_without_the_rights_is_told_who_decides() -> None:
    _bus(accepts=[])
    with pytest.raises(AdapterError) as failure:
        await get_adapter("openwrt").fetch("system", CONFIG, {}, _ctx())
    assert failure.value.code == "denied" and "rpcd" in str(failure.value.hint)


@respx.mock
async def test_a_router_without_the_bus_plugin_is_told_what_is_missing() -> None:
    respx.post(BUS).mock(return_value=httpx.Response(404, text="Not Found"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("openwrt").fetch("system", CONFIG, {}, _ctx())
    assert failure.value.code == "no_ubus" and "uhttpd-mod-ubus" in str(failure.value.hint)


@respx.mock
async def test_the_bus_path_is_added_once() -> None:
    _bus()
    await get_adapter("openwrt").fetch("system", {**CONFIG, "url": BUS}, {}, _ctx())
    assert all("/ubus/ubus" not in str(call.request.url) for call in respx.calls)


@respx.mock
async def test_the_test_button_names_the_board_and_the_release() -> None:
    _bus()
    said = await get_adapter("openwrt").test(CONFIG, _ctx())
    assert "GL.iNet GL-MT6000" in said and "24.10.1" in said


def test_the_demo_draws() -> None:
    for kind in ("system", "wan", "wireless"):
        assert get_adapter("openwrt").demo(kind, {}, 2).primary
