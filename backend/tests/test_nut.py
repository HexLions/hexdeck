"""NUT, against the lines upsd 2.8.1 answered locally on 20.10.2026.

A small server replays them: what ``LIST VAR`` gave for a dummy-ups playing a
Back-UPS ES 700G on line and later on battery with the battery low, and the
one-line errors for an unknown UPS and a driver that is gone.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "nut_upsd.json").read_text())


async def _upsd(listing: list[str] | str) -> tuple[asyncio.AbstractServer, int, list[str]]:
    """A fake upsd: VER, then the given answer to LIST VAR. Returns the lines it was sent."""
    heard: list[str] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while line := (await reader.readline()).decode().strip():
            heard.append(line)
            if line == "VER":
                writer.write(f"{FIXTURE['VER']}\n".encode())
            elif line.startswith("LIST VAR"):
                rows = [listing] if isinstance(listing, str) else listing
                writer.write(("\n".join(rows) + "\n").encode())
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], heard


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


async def test_on_line() -> None:
    server, port, heard = await _upsd(FIXTURE["online"])
    async with server:
        data = await get_adapter("nut").fetch("status", {"host": "127.0.0.1", "port": port, "ups": "ups"}, {}, _ctx())
    assert data.status == "ok"
    assert data.primary == {"label": "Charge", "value": 100, "unit": "%"}
    assert data.secondary == [{"label": "Status", "value": "On line"}, {"label": "Runtime", "value": "41m"},
                              {"label": "Load", "value": 23, "unit": "%"}, {"label": "Input", "value": 231, "unit": "V"},
                              {"label": "Power", "value": 93, "unit": "W"}]
    assert data.metrics == {"charge": 100.0, "load": 23.0, "runtime_min": 41.0, "power_w": 93.0}, "23 % of 405 W nominal"
    assert heard == ["VER", "LIST VAR ups"], "no USERNAME, PASSWORD or LOGIN: reading needs none"


async def test_on_battery_and_low_is_red_with_the_reason() -> None:
    server, port, _heard = await _upsd(FIXTURE["on_battery"])
    async with server:
        data = await get_adapter("nut").fetch("status", {"host": "127.0.0.1", "port": port, "ups": "ups"}, {}, _ctx())
    assert data.status == "bad" and data.meta["status_reason"] == "On battery, Battery low"
    assert data.primary["value"] == 8 and data.secondary[1] == {"label": "Runtime", "value": "3m 10s"}


@pytest.mark.parametrize(("answer", "code"), [("unknown_ups", "unknown_ups"), ("driver_not_connected", "driver_not_connected")])
async def test_the_errors_upsd_names(answer: str, code: str) -> None:
    server, port, _heard = await _upsd(FIXTURE[answer])
    async with server:
        with pytest.raises(AdapterError) as failure:
            await get_adapter("nut").fetch("status", {"host": "127.0.0.1", "port": port, "ups": "ups"}, {}, _ctx())
    assert failure.value.code == code and failure.value.hint


async def test_something_else_and_nobody_at_all() -> None:
    server, port, _heard = await _upsd("HTTP/1.1 400 Bad Request")
    async with server:
        with pytest.raises(AdapterError) as failure:
            await get_adapter("nut").fetch("status", {"host": "127.0.0.1", "port": port, "ups": "ups"}, {}, _ctx())
    assert failure.value.code == "not_nut"
    with pytest.raises(AdapterError) as failure:
        await get_adapter("nut").fetch("status", {"host": "127.0.0.1", "port": port, "ups": "ups"}, {}, _ctx())
    assert failure.value.code == "unreachable" and "Remote Monitor" in failure.value.hint


@pytest.mark.parametrize("config", [{"host": "nas; rm -rf", "ups": "ups"}, {"host": "nas", "ups": "ups\nLOGIN x"}, {"host": "nas", "ups": "ups", "port": 70000}])
async def test_nothing_odd_reaches_the_wire(config: dict) -> None:
    with pytest.raises(AdapterError):
        await get_adapter("nut").fetch("status", config, {}, _ctx())


async def test_the_connection_test() -> None:
    server, port, _heard = await _upsd(FIXTURE["online"])
    async with server:
        said = await get_adapter("nut").test({"host": "127.0.0.1", "port": port, "ups": "ups"}, _ctx())
    assert said == "upsd answers for American Power Conversion Back-UPS ES 700G: On line."


def test_the_demo() -> None:
    adapter = get_adapter("nut")
    states = {adapter.demo("status", {}, tick).status for tick in range(24)}
    assert states == {"ok", "bad"}, "the demo shows a power cut now and then"


async def test_a_cyberpower_that_pads_its_names() -> None:
    """usbhid-ups on TrueNAS: device.mfr "1 ", ups.mfr "1 ", device.model "1000".

    ``cyberpower_truenas`` is what upsd 2.8.0 on a TrueNAS answered for a
    CyberPower 1000 VA on USB on 21.10.2026, read through this adapter.
    """
    server, port, _heard = await _upsd(FIXTURE["cyberpower_truenas"])
    async with server:
        said = await get_adapter("nut").test({"host": "127.0.0.1", "port": port, "ups": "ups"}, _ctx())
    assert said == "upsd answers for UPS 1000: On line."


async def test_the_details_of_that_cyberpower() -> None:
    server, port, _heard = await _upsd(FIXTURE["cyberpower_truenas"])
    async with server:
        status = await get_adapter("nut").fetch("status", {"host": "127.0.0.1", "port": port, "ups": "ups"}, {}, _ctx())
        details = await get_adapter("nut").fetch("details", {"host": "127.0.0.1", "port": port, "ups": "ups"}, {}, _ctx())
    assert {"label": "Power", "value": 138, "unit": "W"} in status.secondary, "23 % of 600 W"
    rows = {row["label"]: row["value"] for row in details.secondary}
    assert rows["Model"] == "1000", "the maker is padding, so only the model"
    assert rows["Battery"] == "13.6 V of 12 V" and rows["Battery type"] == "PbAcid"
    assert rows["Low battery at"] == 10 and rows["Low runtime at"] == "5m"
    assert rows["Input frequency"] == "49.8" and rows["Nominal power"] == 600
    assert "Output" not in rows, "25.4 V out with 230 V in is not what comes out"
    assert rows["Driver"] == "usbhid-ups 2.8.0" and rows["Beeper"] == "enabled"
