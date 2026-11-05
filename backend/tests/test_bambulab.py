"""Bambu Lab, against a small MQTT broker that behaves as a printer is described to.

No printer was at hand. The reports are written anew in the shape of those the
HACS integration records in its ``mock_data``; the broker checks the CONNECT,
the SUBSCRIBE and the pushall the card sends, byte by byte as MQTT 3.1.1 has them.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.adapters import get_adapter
from app.adapters.bambulab import _packet, _read_packet, error_code, hms_code
from app.adapters.base import AdapterError, AuthFailed, Context

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "bambulab_reports.json").read_text())
SERIAL = "01S00C000000000"
CONFIG = {"host": "printer.local", "serial": SERIAL, "access_code": "12345678"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _text(value: str) -> bytes:
    return struct.pack("!H", len(value)) + value.encode()


class Printer:
    """One broker for one test: what it heard, and what it says."""

    def __init__(self, reports: list[str], connack: int = 0, known_serial: str = SERIAL, silent: bool = False) -> None:
        self.reports, self.connack, self.known_serial, self.silent = reports, connack, known_serial, silent
        self.login: tuple[str, str] | None = None
        self.asked: Any = None
        self.server: asyncio.AbstractServer | None = None
        self.said_goodbye = False

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        kind, body = await _read_packet(reader)
        assert kind == 0x10 and body[:6] == _text("MQTT") and body[6] == 4 and body[7] == 0xC2, "MQTT 3.1.1, clean, user and password"
        at = 10
        fields = []
        for _ in range(3):
            size = struct.unpack("!H", body[at:at + 2])[0]
            fields.append(body[at + 2:at + 2 + size].decode())
            at += 2 + size
        self.login = (fields[1], fields[2])
        writer.write(bytes([0x20, 2, 0, self.connack]))
        await writer.drain()
        if self.connack:
            writer.close()
            return
        try:
            while True:
                kind, body = await _read_packet(reader)
                if kind >> 4 == 8:
                    size = struct.unpack("!H", body[2:4])[0]
                    topic = body[4:4 + size].decode()
                    allowed = topic == f"device/{self.known_serial}/report"
                    writer.write(_packet(0x90, body[:2] + (b"\x00" if allowed else b"\x80")))
                elif kind >> 4 == 3:
                    size = struct.unpack("!H", body[:2])[0]
                    assert body[2:2 + size].decode() == f"device/{self.known_serial}/request"
                    self.asked = json.loads(body[2 + size:])
                    if not self.silent:
                        for name in self.reports:
                            writer.write(_packet(0x30, _text(f"device/{self.known_serial}/report") + json.dumps(FIXTURE[name]).encode()))
                elif kind == 0xE0:
                    self.said_goodbye = True
                    break
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        writer.close()

    async def __aenter__(self) -> Printer:
        self.server = await asyncio.start_server(self.serve, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_: Any) -> None:
        assert self.server is not None
        self.server.close()

    async def open(self, host: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        assert self.server is not None
        return await asyncio.open_connection("127.0.0.1", self.server.sockets[0].getsockname()[1])


def test_the_codes_are_written_as_bambu_writes_them() -> None:
    assert hms_code({"attr": 50331904, "code": 65543}) == "0300_0100_0001_0007"
    assert error_code(50348044) == "0300_400C"


def test_a_long_packet_has_its_size_in_several_bytes() -> None:
    assert _packet(0x30, b"x" * 321)[:3] == bytes([0x30, 0xC1, 0x02])


async def test_a_print_with_its_progress_time_and_temperatures(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("bambulab")
    async with Printer(["fragment", "printing"]) as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        data = await adapter.fetch("status", CONFIG, {}, _ctx())
        await asyncio.sleep(0.05)
    assert printer.login == ("bblp", "12345678")
    assert printer.asked == {"pushing": {"sequence_id": "0", "command": "pushall"}}
    assert printer.said_goodbye, "the connection is not held open"
    assert data.primary == {"label": "Progress", "value": 42, "unit": "%"}, "the fragment before it is not the report"
    assert data.secondary == [{"label": "Status", "value": "Printing"}, {"label": "Job", "value": "Rack bracket"},
                              {"label": "Time left", "value": "2 h 14 min"}, {"label": "Layer", "value": "88 / 212"},
                              {"label": "Nozzle", "value": 220, "unit": "°C"}, {"label": "Bed", "value": 55, "unit": "°C"}]
    assert data.status == "ok" and data.metrics == {"progress": 42.0, "nozzle": 219.8125, "bed": 54.75}


async def test_an_idle_printer_says_so_without_a_job(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("bambulab")
    async with Printer(["idle"]) as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        data = await adapter.fetch("status", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Status", "value": "Idle"} and data.status == "ok"
    assert [row["label"] for row in data.secondary] == ["Nozzle", "Bed"]


async def test_a_failed_print_is_red_and_names_its_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("bambulab")
    async with Printer(["failed"]) as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        data = await adapter.fetch("status", CONFIG, {}, _ctx())
    assert data.status == "bad" and data.meta["status_reason"] == "Failed"
    assert {"label": "Errors", "value": "0300_400C, 0300_0100_0001_0007"} in data.secondary


async def test_the_filament_with_what_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("bambulab")
    async with Printer(["printing"]) as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        data = await adapter.fetch("filament", CONFIG, {}, _ctx())
    assert [(item["title"], item["value"], item["status"]) for item in data.items] == [
        ("A1 PLA Matte", "93%", "ok"), ("A2 PETG HF", "7%", "warn"), ("A3 ABS", "", "unknown")], "the empty slot is left out"
    assert data.items[0]["subtitle"] == "18 % humidity" and data.status == "warn"


async def test_two_cards_share_one_report(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("bambulab")
    ctx = _ctx()
    async with Printer(["printing"]) as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        await adapter.fetch("status", CONFIG, {}, ctx)
        printer.asked = None
        await adapter.fetch("filament", CONFIG, {}, ctx)
    assert printer.asked is None


@pytest.mark.parametrize("code", [4, 5])
async def test_a_wrong_access_code_is_said(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    adapter = get_adapter("bambulab")
    async with Printer([], connack=code) as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        with pytest.raises(AuthFailed) as refused:
            await adapter.test(CONFIG, _ctx())
    assert "access code" in str(refused.value)


async def test_a_wrong_serial_refused_at_the_subscribe_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("bambulab")
    async with Printer(["idle"], known_serial="OTHER") as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        with pytest.raises(AdapterError) as refused:
            await adapter.test(CONFIG, _ctx())
    assert refused.value.code == "bad_serial"


async def test_a_printer_that_does_not_report_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = get_adapter("bambulab")
    monkeypatch.setattr("app.adapters.bambulab.TIMEOUT", 0.2)
    async with Printer(["idle"], silent=True) as printer:
        monkeypatch.setattr(adapter, "_open", printer.open)
        with pytest.raises(AdapterError) as refused:
            await adapter.test(CONFIG, _ctx())
    assert refused.value.code == "unreachable"


async def test_an_address_or_serial_that_cannot_be_one_is_refused_first() -> None:
    adapter = get_adapter("bambulab")
    with pytest.raises(AdapterError) as refused:
        await adapter.test({**CONFIG, "host": "a b"}, _ctx())
    assert refused.value.code == "bad_host"
    with pytest.raises(AdapterError) as refused:
        await adapter.test({**CONFIG, "serial": "a/b"}, _ctx())
    assert refused.value.code == "bad_serial"
