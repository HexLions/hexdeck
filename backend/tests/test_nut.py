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
                              {"label": "Load", "value": 23, "unit": "%"}, {"label": "Input", "value": 231, "unit": "V"}]
    assert data.metrics == {"charge": 100.0, "load": 23.0, "runtime_min": 41.0}
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
