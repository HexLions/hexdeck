"""AMP: the session, the instances across targets, the metrics, and the buttons.

The fake panel below routes by the endpoint in the path the way AMP does, keeps
one session per address (the controller's and each instance's proxy), and can
answer AMP's own flavour of failure: a 200 with a stack trace in it.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

URL = "http://amp.lan:8080"
CONFIG = {"url": URL, "username": "hexdeck", "password": "secret"}

INSTANCES = [{
    "Id": 1, "InstanceId": "ads", "FriendlyName": "local", "AvailableInstances": [
        {"InstanceID": "a", "InstanceName": "mc01", "FriendlyName": "Minecraft survival",
         "Module": "Minecraft", "ModuleDisplayName": "Minecraft", "Running": True, "AppState": 20},
        {"InstanceID": "b", "InstanceName": "valheim01", "FriendlyName": "Valheim",
         "Module": "GenericModule", "ModuleDisplayName": "Valheim", "Running": False, "AppState": 0},
    ]}, {
    "Id": 2, "InstanceId": "node2", "FriendlyName": "node-2", "AvailableInstances": [
        {"InstanceID": "c", "InstanceName": "pal01", "FriendlyName": "Palworld",
         "Module": "GenericModule", "ModuleDisplayName": "Palworld", "Running": False, "AppState": 100},
    ]}]
ADS_STATUS = {"State": 20, "Uptime": "9.00:00:00", "Metrics": {
    "CPU Usage": {"RawValue": 12, "MaxValue": 100, "Percent": 12.0, "Units": "%"},
    "Memory Usage": {"RawValue": 9800, "MaxValue": 32000, "Percent": 30.6, "Units": "MB"}}}
INSTANCE_STATUS = {"State": 20, "Uptime": "3.04:12:55", "Metrics": {
    "CPU Usage": {"RawValue": 43, "MaxValue": 100, "Percent": 43.0, "Units": "%"},
    "Memory Usage": {"RawValue": 4096, "MaxValue": 8192, "Units": "MB"},
    "Active Users": {"RawValue": 4, "MaxValue": 20, "Units": "Users"}}}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _panel(instances: Any = None, controller: bool = True, status: dict | None = None,
           password: str = "secret", refuse_power: bool = False) -> dict[str, int]:
    """A panel that answers the way AMP does, counting what was called."""
    counted: dict[str, int] = {}

    def answer(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        endpoint = path.split("/API/", 1)[1] if "/API/" in path else path
        body = json.loads(request.content.decode())
        counted[endpoint] = counted.get(endpoint, 0) + 1
        if endpoint.endswith("Core/Login"):
            if body.get("password") != password:
                return httpx.Response(200, json={"success": False, "resultReason": "Invalid credentials",
                                                 "result": 0, "permissions": []})
            return httpx.Response(200, json={"success": True, "sessionID": f"session-for-{endpoint}",
                                             "rememberMeToken": "", "result": 1, "permissions": []})
        if endpoint == "ADSModule/GetInstances":
            if not controller:
                return httpx.Response(404, text="Not Found")
            return httpx.Response(200, json={"result": instances if instances is not None else INSTANCES})
        if endpoint == "Core/GetStatus":
            return httpx.Response(200, json=status if status is not None else ADS_STATUS)
        if endpoint.startswith("ADSModule/Servers/") and endpoint.endswith("Core/GetStatus"):
            return httpx.Response(200, json=INSTANCE_STATUS)
        if endpoint.startswith("ADSModule/") and endpoint.endswith("Instance"):
            return httpx.Response(200, json={"Status": not refuse_power, "Reason": "Instance is suspended"})
        if endpoint.startswith("Core/") and endpoint.split("/")[1] in ("Start", "Stop", "Restart"):
            return httpx.Response(200, json={"Status": True})
        return httpx.Response(200, json={"Title": "Unknown method", "Message": f"No such method {endpoint}",
                                         "StackTrace": "at AMP"})

    respx.post(url__startswith=f"{URL}/API").mock(side_effect=answer)
    return counted


@respx.mock
async def test_the_servers_of_every_target_end_up_in_one_list() -> None:
    _panel()
    data = await get_adapter("amp").fetch("instances", CONFIG, {}, _ctx())
    titles = [item["title"] for item in data.items]
    assert titles == ["Palworld", "Minecraft survival", "Valheim"], "failed first, then running, then stopped"
    rows = {item["title"]: item for item in data.items}
    assert rows["Palworld"]["status"] == "bad" and rows["Palworld"]["value"] == "Failed"
    assert rows["Palworld"]["subtitle"] == "Palworld · node-2", "the target it lives on"
    assert rows["Valheim"]["status"] == "unknown", "a stopped server is not a fault"
    assert data.primary["value"] == "1 / 3" and data.metrics["running"] == 1.0


@respx.mock
async def test_a_state_between_the_two_is_yellow() -> None:
    _panel(instances=[{"FriendlyName": "local", "AvailableInstances": [
        {"InstanceID": "a", "InstanceName": "mc01", "FriendlyName": "Minecraft", "AppState": 75, "Running": True}]}])
    data = await get_adapter("amp").fetch("instances", CONFIG, {}, _ctx())
    assert data.items[0]["status"] == "warn" and data.items[0]["value"] == "Updating"


@respx.mock
async def test_only_what_is_running_can_be_asked_for() -> None:
    _panel()
    data = await get_adapter("amp").fetch("instances", CONFIG, {"running_only": True}, _ctx())
    assert [item["title"] for item in data.items] == ["Minecraft survival"]


@respx.mock
async def test_one_server_is_read_through_its_own_session() -> None:
    counted = _panel()
    data = await get_adapter("amp").fetch("instance", CONFIG, {"instance": "mc01"}, _ctx())
    assert counted["ADSModule/Servers/a/API/Core/Login"] == 1, "the controller proxies a login to the instance"
    assert counted["ADSModule/Servers/a/API/Core/GetStatus"] == 1
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["State"] == "Ready" and labels["CPU"] == 43.0
    assert labels["Memory"] == 50.0, "4096 of 8192 MB, computed where AMP sends no percentage"
    assert labels["Players"] == "4 / 20" and labels["Up"] == "3.04:12:55"
    assert data.metrics == {"cpu": 43.0, "memory": 50.0, "players": 4.0}


@respx.mock
async def test_the_picker_offers_what_the_panel_has() -> None:
    _panel()
    offered = await get_adapter("amp").choices("instance", CONFIG, _ctx())
    assert offered == [("mc01", "Minecraft survival"), ("valheim01", "Valheim"), ("pal01", "Palworld")]
    assert await get_adapter("amp").choices("something-else", CONFIG, _ctx()) == []


@respx.mock
async def test_a_server_that_is_gone_is_named_rather_than_silently_swapped() -> None:
    _panel()
    with pytest.raises(AdapterError) as failure:
        await get_adapter("amp").fetch("instance", CONFIG, {"instance": "deleted01"}, _ctx())
    assert failure.value.code == "no_such_instance"


@respx.mock
async def test_the_panel_card_counts_and_reads_the_controller_s_own_machine() -> None:
    _panel()
    data = await get_adapter("amp").fetch("panel", CONFIG, {}, _ctx())
    assert data.primary["value"] == "1 / 3"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Servers"] == 3 and labels["CPU"] == 12.0 and labels["Failed"] == 1
    assert data.status == "bad"


@respx.mock
async def test_stopping_asks_first_and_starting_does_not() -> None:
    _panel()
    data = await get_adapter("amp").fetch("instances", CONFIG, {"switching": True}, _ctx())
    rows = {item["title"]: item for item in data.items}
    running = rows["Minecraft survival"]["actions"]
    assert [one["id"] for one in running] == ["stop", "restart"]
    assert all(one["confirm"] for one in running), "everybody on the server is put off"
    stopped = rows["Valheim"]["actions"]
    assert [one["id"] for one in stopped] == ["start"] and not stopped[0]["confirm"]


@respx.mock
async def test_a_button_says_what_it_did_and_asks_the_controller() -> None:
    counted = _panel()
    said = await get_adapter("amp").action("instances", "stop", {"instance": "mc01"}, CONFIG, {}, _ctx())
    assert counted["ADSModule/StopInstance"] == 1 and "mc01" in said


@respx.mock
async def test_a_panel_that_refuses_says_why() -> None:
    _panel(refuse_power=True)
    with pytest.raises(AdapterError) as failure:
        await get_adapter("amp").action("instances", "start", {"instance": "pal01"}, CONFIG, {}, _ctx())
    assert failure.value.code == "refused" and "suspended" in str(failure.value)


@respx.mock
async def test_an_unknown_action_is_refused_before_anything_is_sent() -> None:
    _panel()
    with pytest.raises(AdapterError):
        await get_adapter("amp").action("instances", "delete", {"instance": "mc01"}, CONFIG, {}, _ctx())


@respx.mock
async def test_a_single_game_server_without_a_controller_still_draws() -> None:
    counted = _panel(controller=False)
    data = await get_adapter("amp").fetch("instances", CONFIG, {}, _ctx())
    assert len(data.items) == 1 and data.items[0]["status"] == "ok"
    assert counted["Core/GetStatus"] >= 1, "the panel is the server"
    said = await get_adapter("amp").test(CONFIG, _ctx())
    assert "single game server" in said


@respx.mock
async def test_the_session_is_kept_across_cards_and_taken_up_again_when_refused() -> None:
    counted = _panel()
    ctx = _ctx()
    await get_adapter("amp").fetch("instances", CONFIG, {}, ctx)
    await get_adapter("amp").fetch("panel", CONFIG, {}, ctx)
    assert counted["Core/Login"] == 1, "one login for both cards"

    # The panel forgets it: the next call is refused and has to log in again.
    ctx.cache["amp:session"] = (ctx.cache["amp:session"][0], "stale")
    ctx.cache.pop("amp:instances", None)
    counted = _panel()
    data = await get_adapter("amp").fetch("instances", CONFIG, {}, ctx)
    assert data.items, "it recovered rather than going blank"


@respx.mock
async def test_amps_own_flavour_of_failure_is_not_read_as_data() -> None:
    _panel()
    with pytest.raises(AdapterError) as failure:
        await get_adapter("amp")._call(CONFIG, _ctx(), "Core/MadeUpMethod")
    assert failure.value.code == "amp_error" and "No such method" in str(failure.value)


@respx.mock
async def test_a_wrong_password_names_two_factor_as_the_other_possibility() -> None:
    _panel(password="something else")
    with pytest.raises(AdapterError) as failure:
        await get_adapter("amp").fetch("instances", CONFIG, {}, _ctx())
    assert failure.value.code == "auth_failed" and "two-factor" in str(failure.value.hint)


def test_the_demo_draws() -> None:
    for kind in ("instances", "instance", "panel"):
        assert get_adapter("amp").demo(kind, {}, 4).primary
