"""Minecraft: the ping both editions answer, against servers that really speak it.

The fakes below are the protocol, not a mock of this module: a TCP server that
frames its answer with VarInts the way a Java server does, and a UDP socket
that answers with RakNet's semicolon separated line. That is the only way to
know the framing is right.
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

import httpx
import pytest

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.adapters.minecraft import MAGIC, clean, flatten, varint

STATUS = {
    "version": {"name": "Paper 1.21.4", "protocol": 769},
    "players": {"online": 3, "max": 20, "sample": [{"name": "Notch"}, {"name": "jeb_"}, {"name": "Dinnerbone"}]},
    "description": {"text": "§aThe house ", "extra": [{"text": "server"}]},
}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


async def _java_server(answer: dict[str, Any] | bytes = STATUS) -> tuple[str, int, asyncio.AbstractServer]:
    """A server that answers one status request the way Java does."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(256)  # the handshake and the request, which we do not check here
        if isinstance(answer, bytes):
            writer.write(answer)
        else:
            body = json.dumps(answer).encode("utf-8")
            inner = varint(0x00) + varint(len(body)) + body
            writer.write(varint(len(inner)) + inner)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return "127.0.0.1", port, server


class _Bedrock(asyncio.DatagramProtocol):
    def __init__(self, line: str) -> None:
        self.line = line
        self.transport: Any = None

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, _data: bytes, address: Any) -> None:
        payload = self.line.encode("utf-8")
        self.transport.sendto(b"\x1c" + struct.pack(">Q", 1) + struct.pack(">Q", 2) + MAGIC + struct.pack(">H", len(payload)) + payload, address)


#: What a Bedrock server answers with: edition, motd, protocol, version, players, slots, id, second line.
BEDROCK_LINE = "MCPE;The house server;800;1.21.50;2;10;123456;Survival;Survival;1;19132;19133;"


@pytest.fixture(autouse=True)
def _allow_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard against loopback targets is the point elsewhere; here the fake server is loopback."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "allow_loopback_targets", True)


async def test_a_java_server_is_read_down_to_its_message_of_the_day() -> None:
    host, port, server = await _java_server()
    try:
        data = await get_adapter("minecraft").fetch("server", {"host": host, "port": port}, {}, _ctx())
    finally:
        server.close()
    assert data.primary["value"] == 3
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Slots"] == 20 and labels["Version"] == "Paper 1.21.4"
    assert labels["Message"] == "The house server", "the colour code and the chat components are gone"
    assert data.metrics["players"] == 3.0 and data.metrics["latency"] >= 0


async def test_the_player_card_lists_the_sample_and_says_when_it_is_hidden() -> None:
    host, port, server = await _java_server()
    try:
        data = await get_adapter("minecraft").fetch("players", {"host": host, "port": port}, {}, _ctx())
    finally:
        server.close()
    assert [item["title"] for item in data.items] == ["Notch", "jeb_", "Dinnerbone"]

    hidden = {**STATUS, "players": {"online": 4, "max": 20}}
    host, port, server = await _java_server(hidden)
    try:
        data = await get_adapter("minecraft").fetch("players", {"host": host, "port": port}, {}, _ctx())
    finally:
        server.close()
    assert data.items == [] and "hides" in data.meta["empty"], "four online and no names is a hidden list, not an empty server"


async def test_a_full_server_is_worth_a_colour() -> None:
    host, port, server = await _java_server({**STATUS, "players": {"online": 20, "max": 20}})
    try:
        data = await get_adapter("minecraft").fetch("server", {"host": host, "port": port}, {}, _ctx())
    finally:
        server.close()
    assert data.status == "warn", "the next player to try gets turned away"


async def test_a_bedrock_server_answers_over_udp() -> None:
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(lambda: _Bedrock(BEDROCK_LINE), local_addr=("127.0.0.1", 0))
    port = transport.get_extra_info("socket").getsockname()[1]
    try:
        data = await get_adapter("minecraft").fetch("server", {"host": "127.0.0.1", "port": port, "edition": "bedrock"}, {}, _ctx())
    finally:
        transport.close()
    assert data.primary["value"] == 2
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Slots"] == 10 and labels["Version"] == "1.21.50"
    assert "The house server" in labels["Message"]


async def test_something_that_is_not_a_minecraft_server_is_said_plainly() -> None:
    host, port, server = await _java_server(b"not a packet at all")
    try:
        with pytest.raises(AdapterError) as failure:
            await get_adapter("minecraft").fetch("server", {"host": host, "port": port}, {}, _ctx())
    finally:
        server.close()
    assert failure.value.code in ("bad_answer", "unreachable")


async def test_nothing_listening_is_a_reason_not_a_stack_trace() -> None:
    with pytest.raises(AdapterError) as failure:
        await get_adapter("minecraft").fetch("server", {"host": "127.0.0.1", "port": 1}, {}, _ctx())
    assert failure.value.code == "unreachable"


async def test_the_answer_is_kept_so_two_cards_are_one_ping() -> None:
    host, port, server = await _java_server()
    ctx = _ctx()
    try:
        await get_adapter("minecraft").fetch("server", {"host": host, "port": port}, {}, ctx)
        server.close()  # the second card gets nothing from the network
        data = await get_adapter("minecraft").fetch("players", {"host": host, "port": port}, {}, ctx)
    finally:
        server.close()
    assert [item["title"] for item in data.items] == ["Notch", "jeb_", "Dinnerbone"]


def test_the_message_of_the_day_survives_every_shape_it_arrives_in() -> None:
    assert clean(flatten("§cHello\n§rworld")) == "Hello world"
    assert clean(flatten({"text": "a", "extra": [{"text": "b", "extra": [{"text": "c"}]}]})) == "abc"
    assert clean(flatten([{"text": "x"}, {"text": "y"}])) == "xy"
    assert clean(flatten(None)) == ""


def test_varints_are_the_ones_the_protocol_defines() -> None:
    assert varint(0) == b"\x00"
    assert varint(1) == b"\x01"
    assert varint(127) == b"\x7f"
    assert varint(128) == b"\x80\x01"
    assert varint(255) == b"\xff\x01"
    assert varint(-1) == b"\xff\xff\xff\xff\x0f", "the handshake sends -1 as the protocol version"


def test_the_demo_draws() -> None:
    assert get_adapter("minecraft").demo("server", {}, 0).primary
    assert get_adapter("minecraft").demo("players", {}, 5).items
