"""apcupsd: the network information server, spoken to as apcupsd's own source writes it.

``apcupsd_status.json`` is written anew from the format ``src/lib/apcstatus.c`` of apcupsd 3.14.14 uses, as an APC
Back-UPS on USB would fill it; no UPS was at hand. The fake server below frames every record the way ``net_send`` does.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

import httpx
import pytest

from app.adapters import get_adapter
from app.adapters.apcupsd import ApcupsdAdapter, read_status
from app.adapters.base import AdapterError, Context

LINES: list[str] = json.loads((Path(__file__).parent / "fixtures" / "apcupsd_status.json").read_text())["status"]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _framed(text: str) -> bytes:
    data = text.encode()
    return struct.pack(">H", len(data)) + data


async def _apcupsd(lines: list[str], asked: list[bytes]) -> tuple[asyncio.AbstractServer, int]:
    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        size = struct.unpack(">H", await reader.readexactly(2))[0]
        command = await reader.readexactly(size)
        asked.append(command)
        if command == b"status":
            writer.write(b"".join(_framed(line + "\n") for line in lines))
        else:
            writer.write(_framed("Invalid command\n"))
        writer.write(struct.pack(">H", 0))
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_the_status_records_are_read_to_the_empty_one() -> None:
    asked: list[bytes] = []
    server, port = await _apcupsd(LINES, asked)
    async with server:
        found = await read_status("127.0.0.1", port)
    assert asked == [b"status"]
    assert found["STATUS"] == "ONLINE"
    assert found["TIMELEFT"] == "38.2 Minutes"
    assert found["END APC"].startswith("2026-11-16")


async def test_the_card_and_the_connection_test() -> None:
    server, port = await _apcupsd(LINES, [])
    async with server:
        config = {"host": "127.0.0.1", "port": port}
        assert await get_adapter("apcupsd").test(config, _ctx()) == "apcupsd 3.14.14 answers for Back-UPS RS 1500MS2: On line."
        data = await get_adapter("apcupsd").fetch("status", config, {}, _ctx())
    assert data.status == "ok"
    assert data.primary == {"label": "Charge", "value": 100, "unit": "%"}
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values == {"Status": "On line", "Runtime left": "38m 12s", "Load": 12, "Input": 231, "Power": 104}
    assert data.metrics == {"charge": 100.0, "load": 12.0, "runtime_min": 38.2, "power_w": 104}


def test_on_battery_is_red_and_a_missing_field_is_not_zero() -> None:
    data = ApcupsdAdapter._status({"STATUS": "ONBATT LOWBATT", "TIMELEFT": "4.0 Minutes"})
    assert data.status == "bad" and data.meta["status_reason"] == "On battery, Battery low"
    assert data.primary["value"] == "?" and "charge" not in data.metrics
    assert "Power" not in {row["label"] for row in data.secondary}, "no rated watts, no watts"


def test_lost_contact_and_an_old_battery() -> None:
    assert ApcupsdAdapter._status({"STATUS": "COMMLOST"}).status == "bad"
    assert ApcupsdAdapter._status({"STATUS": "ONLINE REPLACEBATT", "BCHARGE": "100.0 Percent"}).status == "warn"


def test_the_details() -> None:
    found = dict(line.split(":", 1) for line in LINES)
    found = {key.strip(): value.strip() for key, value in found.items()}
    data = ApcupsdAdapter._details(found)
    rows = {row["label"]: row["value"] for row in data.secondary}
    assert rows["Battery"] == "27.2 V of 24 V" and rows["Self-test"] == "Passed" and rows["Transfers to battery"] == 2
    assert "HOSTNAME" not in json.dumps(data.secondary) and "nas.local" not in json.dumps(data.secondary)


async def test_something_else_on_the_port() -> None:
    async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(8)
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(http, "127.0.0.1", 0)
    async with server:
        with pytest.raises(AdapterError) as refused:
            await read_status("127.0.0.1", server.sockets[0].getsockname()[1])
    assert refused.value.code == "not_apcupsd"


@pytest.mark.parametrize("config", [{"host": "nas.lan; rm"}, {"host": "nas.lan", "port": 70000}, {"host": ""}])
async def test_nothing_odd_reaches_the_wire(config: dict) -> None:
    with pytest.raises(AdapterError):
        await get_adapter("apcupsd").test(config, _ctx())


@pytest.mark.parametrize("kind", ["status", "details"])
def test_every_card_has_a_demo(kind: str) -> None:
    data = get_adapter("apcupsd").demo(kind, {}, 3)
    assert data.primary["value"] == 100
