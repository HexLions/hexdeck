"""Minecraft: who is on the server, what it calls itself, how far away it is.

No HTTP anywhere. A Minecraft server answers the query its own client uses
before you press Join, and both editions do it in a handful of bytes:

* **Java** speaks the Server List Ping over TCP. A handshake packet that says
  "next state: status", an empty status request, and the server sends back one
  JSON document with the players, the version and the message of the day.
  Length-prefixed VarInts all the way down, which is why the framing below is
  written out by hand.
* **Bedrock** answers an unconnected ping over UDP: one packet with RakNet's
  magic sixteen bytes, and one back whose payload is a single semicolon
  separated line.

⚠️ Java servers older than 1.7 speak a different, incompatible ping. They are
not supported, and the card says so rather than timing out.

⚠️ The protocol carries no credentials and this adapter sends none. The server
address is enough, so nothing here can be more privileged than any player's
client already is.
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    Context,
    Field,
    WidgetData,
    WidgetType,
    guard_member_target,
    measured,
)

#: RakNet's OFFLINE_MESSAGE_DATA_ID: the sixteen bytes every Bedrock ping carries.
MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")
#: What the handshake claims to be. -1 means "just tell me the status", which
#: every server since 1.7 answers regardless of its own version.
PROTOCOL = -1
#: A ping is two packets. Anything slower than this is down as far as a card cares.
TIMEOUT = 6.0
#: Java sends at most twelve sampled names, and often fewer or none at all.
SAMPLE = 12


def varint(value: int) -> bytes:
    """A Minecraft VarInt: seven bits per byte, high bit means "more to come"."""
    unsigned = value & 0xFFFFFFFF
    out = bytearray()
    while True:
        byte = unsigned & 0x7F
        unsigned >>= 7
        out.append(byte | (0x80 if unsigned else 0))
        if not unsigned:
            return bytes(out)


def packet(identifier: int, body: bytes) -> bytes:
    """One packet, length-prefixed as the protocol wants it."""
    inner = varint(identifier) + body
    return varint(len(inner)) + inner


def java_handshake(host: str, port: int) -> bytes:
    """"I am a client, I want the status, and I reached you at this name."

    The name matters: a server behind a proxy such as Velocity routes by the
    address the client asked for, so sending the real host is what makes a
    forwarded server answer at all.
    """
    name = host.encode("utf-8")
    body = varint(PROTOCOL) + varint(len(name)) + name + struct.pack(">H", port) + varint(1)
    return packet(0x00, body)


async def _read_varint(reader: asyncio.StreamReader) -> int:
    number, shift = 0, 0
    for _ in range(5):
        chunk = await reader.readexactly(1)
        byte = chunk[0]
        number |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return number
        shift += 7
    raise AdapterError("The server sent a number no Minecraft server sends.", code="bad_answer")


def skip_varint(payload: bytes, at: int) -> int:
    """The offset after the VarInt that starts at ``at``."""
    while at < len(payload):
        byte = payload[at]
        at += 1
        if not byte & 0x80:
            return at
    raise AdapterError("The answer broke off in the middle of a number.", code="bad_answer")


def flatten(description: Any) -> str:
    """The message of the day as one line.

    It arrives as a plain string on some servers and as a tree of chat
    components on others, each with its own colours, and a card has room for
    the words only.
    """
    if isinstance(description, str):
        return description
    if isinstance(description, list):
        return "".join(flatten(part) for part in description)
    if isinstance(description, dict):
        return str(description.get("text") or "") + "".join(flatten(part) for part in description.get("extra") or [])
    return ""


def clean(text: str) -> str:
    """Without the colour codes, and on one line."""
    out, skip = [], False
    for character in text:
        if skip:
            skip = False
            continue
        if character == "§":  # the section sign starts every colour code
            skip = True
            continue
        out.append(" " if character in "\r\n" else character)
    return " ".join("".join(out).split())


async def java_status(host: str, port: int) -> dict[str, Any]:
    """The status document a Java server answers with, and the round trip."""
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=TIMEOUT)
    except (TimeoutError, OSError) as failure:
        raise AdapterError(f"No answer from {host}:{port}.", code="unreachable",
                           hint="Is the server up, and is that the port it listens on?") from failure
    try:
        writer.write(java_handshake(host, port) + packet(0x00, b""))
        await writer.drain()
        length = await asyncio.wait_for(_read_varint(reader), timeout=TIMEOUT)
        payload = await asyncio.wait_for(reader.readexactly(length), timeout=TIMEOUT)
    except (TimeoutError, OSError, asyncio.IncompleteReadError) as failure:
        raise AdapterError(f"{host}:{port} answered, but not the way a Minecraft server does.", code="bad_answer",
                           hint="Java servers before 1.7 speak an older ping this card cannot read. Bedrock servers need the Bedrock setting.") from failure
    finally:
        writer.close()
    latency = (time.monotonic() - started) * 1000.0
    # The payload is the packet id, then the JSON as a length-prefixed string,
    # and both of those are VarInts that have to be stepped over by hand.
    at = skip_varint(payload, skip_varint(payload, 0))
    try:
        answer = json.loads(payload[at:].decode("utf-8", errors="replace"))
    except ValueError as failure:
        raise AdapterError(f"{host}:{port} did not answer with a status document.", code="bad_answer") from failure
    if not isinstance(answer, dict):
        raise AdapterError(f"{host}:{port} did not answer with a status document.", code="bad_answer")
    players = answer.get("players") if isinstance(answer.get("players"), dict) else {}
    version = answer.get("version") if isinstance(answer.get("version"), dict) else {}
    sample = [str(one.get("name") or "") for one in players.get("sample") or [] if isinstance(one, dict)]
    return {
        "online": int(players.get("online") or 0),
        "max": int(players.get("max") or 0),
        "players": [name for name in sample if name][:SAMPLE],
        "version": clean(str(version.get("name") or "")),
        "motd": clean(flatten(answer.get("description"))),
        "latency": round(latency, 1),
        "edition": "java",
    }


class _Ping(asyncio.DatagramProtocol):
    """One packet out, the first packet back. RakNet needs no more than that."""

    def __init__(self) -> None:
        self.answer: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, _address: Any) -> None:
        if not self.answer.done():
            self.answer.set_result(data)

    def error_received(self, error: Exception) -> None:
        if not self.answer.done():
            self.answer.set_exception(error)


async def bedrock_status(host: str, port: int) -> dict[str, Any]:
    """The one semicolon separated line a Bedrock server answers with."""
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(_Ping, remote_addr=(host, port))
    except OSError as failure:
        raise AdapterError(f"No answer from {host}:{port}.", code="unreachable") from failure
    try:
        transport.sendto(b"\x01" + struct.pack(">Q", int(started * 1000) & 0xFFFFFFFF) + MAGIC + struct.pack(">Q", 2))
        data = await asyncio.wait_for(protocol.answer, timeout=TIMEOUT)
    except (TimeoutError, OSError) as failure:
        raise AdapterError(f"No answer from {host}:{port}.", code="unreachable",
                           hint="Bedrock listens on UDP, usually on 19132. A Java server needs the Java setting.") from failure
    finally:
        transport.close()
    latency = (time.monotonic() - started) * 1000.0
    # id, timestamp, server id, magic, then a length-prefixed string.
    if len(data) < 35 or data[0] != 0x1C:
        raise AdapterError(f"{host}:{port} answered, but not the way a Bedrock server does.", code="bad_answer")
    parts = data[35:].decode("utf-8", errors="replace").split(";")
    def part(index: int) -> str:
        return parts[index].strip() if len(parts) > index else ""
    return {
        "online": int(part(4) or 0) if part(4).isdigit() else 0,
        "max": int(part(5) or 0) if part(5).isdigit() else 0,
        "players": [],
        "version": part(3),
        "motd": clean(" ".join(piece for piece in (part(1), part(7)) if piece)),
        "latency": round(latency, 1),
        "edition": "bedrock",
    }


class MinecraftAdapter(Adapter):
    kind = "minecraft"
    label = "Minecraft"
    category = "media"
    description = "Who is on the server, its message of the day, its version and how fast it answers."
    icon = "minecraft"
    beta = True
    docs_url = "https://minecraft.wiki/w/Java_Edition_protocol/Server_List_Ping"
    fields = (
        Field("host", "Address", required=True, placeholder="minecraft.lan",
              help="The name or address players use. A server behind a proxy routes by it, so use the same one."),
        Field("port", "Port", type="number", default=25565,
              help="25565 for Java, 19132 for Bedrock."),
        Field("edition", "Edition", type="select", default="java",
              options=(("java", "Java"), ("bedrock", "Bedrock")),
              help="Java answers over TCP, Bedrock over UDP. They speak different pings, so this has to be right."),
    )
    widgets = (
        WidgetType(kind="server", label="Server", description="Players online, the message of the day, the version and the round trip.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("players", "latency")),
        WidgetType(kind="players", label="Players", description="The names the server hands out, which is a sample of at most twelve.",
                   renderer="list", default_size=(3, 2), refresh_seconds=60, metrics=("players",),
                   options=(Field("limit", "Entries", type="number", default=12),)),
    )

    @staticmethod
    def _target(config: dict[str, Any]) -> tuple[str, int, str]:
        host = str(config.get("host") or "").strip()
        if not host:
            raise AdapterError("This connection has no address.", code="no_host")
        # Somebody may have typed the whole thing, as a server list entry looks.
        if host.count(":") == 1 and not config.get("port"):
            host, _, written = host.partition(":")
            config = {**config, "port": written}
        edition = "bedrock" if str(config.get("edition") or "java") == "bedrock" else "java"
        try:
            port = int(config.get("port") or (19132 if edition == "bedrock" else 25565))
        except (TypeError, ValueError) as failure:
            raise AdapterError("That is not a port number.", code="bad_port") from failure
        if not 1 <= port <= 65535:
            raise AdapterError("That is not a port number.", code="bad_port")
        # The address comes from a member, so the same rule as every other
        # address a member types in applies: no loopback, no link-local.
        guard_member_target(f"http://[{host}]" if ":" in host and not host.startswith("[") else f"http://{host}")
        return host, port, edition

    async def _status(self, config: dict[str, Any], ctx: Context, cache: float = 30) -> dict[str, Any]:
        host, port, edition = self._target(config)
        key = f"minecraft:{edition}:{host}:{port}"
        kept = ctx.cache.get(key)
        if cache and kept and time.time() - kept[0] < cache:
            return dict(kept[1])
        answer = await (bedrock_status(host, port) if edition == "bedrock" else java_status(host, port))
        ctx.cache[key] = (time.time(), answer)
        return answer

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        answer = await self._status(config, ctx, cache=0)
        version = answer["version"] or "an unnamed version"
        return f"{version} answers with {answer['online']} of {answer['max']} players online."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        answer = await self._status(config, ctx)
        if widget_kind == "players":
            return self._players(answer, options)
        return self._server(answer)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _server(answer: dict[str, Any]) -> WidgetData:
        secondary: list[dict[str, Any]] = [{"label": "Slots", "value": answer["max"]}]
        if answer["version"]:
            secondary.append({"label": "Version", "value": answer["version"]})
        if answer["motd"]:
            secondary.append({"label": "Message", "value": answer["motd"][:60]})
        secondary.append({"label": "Ping", "value": answer["latency"], "unit": "ms", "metric": "latency"})
        return WidgetData(
            # A server that answers is up; a full one is worth a colour, because
            # the next player to try gets turned away.
            status="warn" if answer["max"] and answer["online"] >= answer["max"] else "ok",
            primary={"label": "Players", "value": answer["online"]},
            secondary=secondary,
            metrics=measured({"players": float(answer["online"]), "latency": float(answer["latency"])}),
            meta={"motd": answer["motd"], "edition": answer["edition"]},
        )

    @staticmethod
    def _players(answer: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        names = answer["players"][: max(1, int(options.get("limit") or SAMPLE))]
        # ⚠️ Nobody in the sample does not mean nobody on the server: the
        # sample is optional, most proxies strip it, and a server can be told
        # to hide it. Saying "nobody online" there would be a lie.
        hidden = answer["online"] and not names
        return WidgetData(
            status="ok",
            items=[{"title": name, "status": "ok", "icon": "lucide:user"} for name in names],
            primary={"label": "Players", "value": answer["online"]},
            metrics=measured({"players": float(answer["online"])}),
            meta={"empty": "The server hides its player list." if hidden else "Nobody is online."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        online = int(fake.walk("mc-players", tick, 0, 7))
        answer = {
            "online": online,
            "max": 20,
            "players": ["Notch", "jeb_", "Dinnerbone", "grumm"][:online],
            "version": "Paper 1.21.4",
            "motd": "The house server",
            "latency": round(fake.walk("mc-ping", tick, 3, 24), 1),
            "edition": "java",
        }
        if widget_kind == "players":
            return self._players(answer, options)
        return self._server(answer)


ADAPTER = MinecraftAdapter()
