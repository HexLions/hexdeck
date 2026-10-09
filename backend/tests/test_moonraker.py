"""Moonraker: the job on the printer, the heaters, and the jobs before.

The answers below follow the examples and object specifications in
Moonraker's documentation (external_api/printer, server, history and
printer_objects) as of 2026-10-09, wrapped in {"result": ...} as every
Moonraker HTTP answer is.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

URL = "http://printer.local:7125"
CONFIG = {"url": URL}
INFO = {"state": "ready", "state_message": "Printer is ready", "hostname": "voron", "software_version": "v0.12.0-85-gd785b396"}
PRINTING = {
    "print_stats": {"filename": "parts/rack_bracket.gcode", "total_duration": 3700.0, "print_duration": 3600.0,
                    "filament_used": 5210.0, "state": "printing", "message": "", "info": {"total_layer": 180, "current_layer": 90}},
    "display_status": {"message": "", "progress": 0.5},
    "virtual_sdcard": {"progress": 0.61, "is_active": True},
    "extruder": {"temperature": 240.4, "target": 240.0},
    "heater_bed": {"temperature": 79.8, "target": 80.0},
}
JOBS = {"count": 3, "jobs": [
    {"job_id": "000003", "filename": "parts/clip.gcode", "status": "error", "total_duration": 300.0, "filament_used": 210.0, "end_time": 1791551000.0},
    {"job_id": "000002", "filename": "benchy.gcode", "status": "completed", "total_duration": 5400.0, "filament_used": 4810.0, "end_time": 1791500000.0},
    {"job_id": "000001", "filename": "vase.gcode", "status": "klippy_disconnect", "total_duration": 9300.0, "filament_used": 12200.0, "end_time": 1791400000.0},
]}
TOTALS = {"job_totals": {"total_jobs": 3, "total_time": 15100.0, "total_print_time": 15000.0, "total_filament_used": 17220.0}}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _moonraker(info: dict | None = None, status: dict | None = None) -> None:
    respx.get(f"{URL}/printer/info").mock(return_value=httpx.Response(200, json={"result": info or INFO}))
    respx.get(f"{URL}/printer/objects/query").mock(return_value=httpx.Response(200, json={"result": {"eventtime": 1.0, "status": status if status is not None else PRINTING}}))
    respx.get(f"{URL}/server/history/list").mock(return_value=httpx.Response(200, json={"result": JOBS}))
    respx.get(f"{URL}/server/history/totals").mock(return_value=httpx.Response(200, json={"result": TOTALS}))


@respx.mock
async def test_a_job_shows_its_progress_layer_time_left_and_heaters() -> None:
    """Progress comes from display_status, which follows the slicer's M73, not the file position."""
    _moonraker()
    data = await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Printing", "value": "50%"}
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels == {"File": "rack_bracket.gcode", "Layer": "90 / 180", "Left": "1 h 00 min", "Nozzle": "240 / 240", "Bed": "80 / 80"}
    assert data.status == "ok"
    assert data.metrics == {"progress": 50.0, "extruder": 240.4, "bed": 79.8}


@respx.mock
async def test_the_query_names_its_objects_and_the_heaters_attributes() -> None:
    _moonraker()
    await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    query = next(call for call in respx.calls if call.request.url.path == "/printer/objects/query").request.url.query.decode()
    assert "print_stats" in query and "display_status" in query and "extruder=temperature,target" in query


@respx.mock
async def test_too_little_progress_gives_no_estimate_and_no_layers_without_the_slicer() -> None:
    early = {**PRINTING, "display_status": {"progress": 0.01}, "print_stats": {**PRINTING["print_stats"], "info": {"total_layer": None, "current_layer": None}}}
    _moonraker(status=early)
    data = await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    labels = {row["label"] for row in data.secondary}
    assert "Left" not in labels and "Layer" not in labels


@respx.mock
async def test_a_printer_without_a_bed_simply_has_no_bed_temperature() -> None:
    """A missing printer object is omitted from the answer, not an error."""
    _moonraker(status={key: value for key, value in PRINTING.items() if key != "heater_bed"})
    data = await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    assert "Bed" not in {row["label"] for row in data.secondary} and "bed" not in data.metrics


@respx.mock
async def test_klippy_in_shutdown_outranks_the_job() -> None:
    _moonraker(info={"state": "shutdown", "state_message": "MCU 'mcu' shutdown: Timer too close"})
    data = await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.primary["value"] == "shutdown"
    assert data.meta["status_reason"].startswith("MCU")


@respx.mock
async def test_an_idle_printer_says_standby() -> None:
    _moonraker(status={"print_stats": {"state": "standby", "filename": ""}, "extruder": {"temperature": 24.0, "target": 0.0}})
    data = await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Printer", "value": "standby"}
    assert {row["label"]: row["value"] for row in data.secondary} == {"Nozzle": "24"}


@respx.mock
async def test_the_history_names_how_each_job_ended() -> None:
    _moonraker()
    data = await get_adapter("moonraker").fetch("history", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"], row["status"]) for row in data.items] == [
        ("clip.gcode", "error", "bad"), ("benchy.gcode", "completed", "ok"), ("vase.gcode", "klippy disconnect", "bad")]
    assert data.items[1]["subtitle"].startswith("1 h 30 min · 4.8 m")
    assert data.status == "bad", "the newest job failed"
    assert data.primary == {"label": "Jobs", "value": 3}


@respx.mock
async def test_an_api_key_goes_in_as_x_api_key_and_only_when_given() -> None:
    _moonraker()
    await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    assert "x-api-key" not in respx.calls[0].request.headers
    await get_adapter("moonraker").fetch("printer", {**CONFIG, "api_key": "abc"}, {}, _ctx())
    assert respx.calls[-1].request.headers["x-api-key"] == "abc"


@respx.mock
async def test_a_refused_key_points_at_trusted_clients() -> None:
    respx.get(f"{URL}/printer/info").mock(return_value=httpx.Response(401, json={"error": {"code": 401, "message": "Unauthorized"}}))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    assert "trusted_clients" in failure.value.hint


@respx.mock
async def test_moonrakers_own_error_message_is_passed_on() -> None:
    respx.get(f"{URL}/printer/info").mock(return_value=httpx.Response(503, json={"error": {"code": 503, "message": "Klippy Disconnected"}}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("moonraker").fetch("printer", CONFIG, {}, _ctx())
    assert "Klippy Disconnected" in failure.value.message


@respx.mock
async def test_the_connection_test_names_klipper_and_the_host() -> None:
    _moonraker()
    assert await get_adapter("moonraker").test(CONFIG, _ctx()) == "Moonraker answers: Klipper v0.12.0-85-gd785b396 on voron, ready."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("moonraker")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
