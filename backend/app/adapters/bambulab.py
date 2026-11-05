"""Bambu Lab: what the 3D printer is doing, its temperatures and its filament.

Written from the HACS integration "Bambu Lab" (greghesp, MIT), its pybambu
client (bambu_client.py, models.py, const.py) and the shape of the reports of
real printers it records in ``mock_data``. Not yet run against a printer: none
was at hand. No code taken, and the test's reports are written anew.

⚠️ There is no HTTP API. The printer is an MQTT broker on port 8883 over TLS:
user ``bblp``, the password is the access code on its screen, topics
``device/<serial>/report`` and ``device/<serial>/request``. A wrong code is
CONNACK 4 or 5. The few packets the card needs are written here, as for NUT,
rather than adding a client library for one card.

⚠️ The certificate is signed by Bambu's own authorities, a new one for several
printer lines, and carries no usable host name. The card does not verify it,
as the integration's own option allows, so the code is protected from a
listener but not from someone posing as the printer on the same network.
TLS stays at 1.2: a P2S never answers a 1.3 hello, and the handshake hangs.

⚠️ A printer only reports what changed, so one card reading alone would see
fragments. ``pushing/pushall`` asks for everything once, and the answer is a
``print`` object with ``command`` "push_status". Reading is that one message,
and the connection is closed straight after rather than held open.

⚠️ ``mc_remaining_time`` is in minutes; ``wifi_signal`` is a string with a
unit. Newer printers also pack temperatures in ``device`` (low 16 bits now,
high 16 the target) beside the plain fields, and the plain ones are read.
``print_error`` and each ``hms`` entry are numbers: the card shows them as
Bambu writes them (``0300_0100_0001_0007``): its texts are not carried here,
and the code is what its wiki is searched by.
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import struct
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    guard_outbound,
)

PORT = 8883
TIMEOUT = 15.0
MAX_PACKET = 4 * 1024 * 1024
PUSH_ALL = {"pushing": {"sequence_id": "0", "command": "pushall"}}
WORDS = {"RUNNING": "Printing", "PREPARE": "Preparing", "PAUSE": "Paused", "FINISH": "Finished", "FAILED": "Failed",
         "IDLE": "Idle", "SLICING": "Slicing", "INIT": "Starting", "OFFLINE": "Offline"}
CACHE_KEY = "bambulab:report"


def _text(value: str) -> bytes:
    raw = value.encode()
    return struct.pack("!H", len(raw)) + raw


def _packet(kind: int, body: bytes) -> bytes:
    """A fixed header with its variable-length size, then the body."""
    size, out = len(body), bytearray([kind])
    while True:
        size, digit = size // 128, size % 128
        out.append(digit | 0x80 if size else digit)
        if not size:
            return bytes(out) + body


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first = (await reader.readexactly(1))[0]
    size, shift = 0, 0
    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        size |= (digit & 0x7F) << shift
        shift += 7
        if not digit & 0x80:
            break
    if size > MAX_PACKET:
        raise AdapterError("The printer sent more than a report should be.", code="bad_answer")
    return first, await reader.readexactly(size)


def _context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def hms_code(entry: dict[str, Any]) -> str:
    attr, code = int(entry.get("attr") or 0), int(entry.get("code") or 0)
    return f"{attr >> 16:04X}_{attr & 0xFFFF:04X}_{code >> 16:04X}_{code & 0xFFFF:04X}"


def error_code(value: int) -> str:
    return f"{value >> 16:04X}_{value & 0xFFFF:04X}"


class BambuLabAdapter(Adapter):
    kind = "bambulab"
    label = "Bambu Lab"
    category = "home"
    description = "A Bambu Lab 3D printer on your network: what it prints, how far along, its temperatures, errors and filament."
    icon = "bambu-lab"
    beta = True
    docs_url = "https://github.com/greghesp/ha-bambulab"
    fields = (
        Field("host", "Address", required=True, placeholder="192.168.1.20", help="The printer's address on your network."),
        Field("serial", "Serial number", required=True, secret=True, help="On the printer's screen under Settings, Device."),
        Field("access_code", "Access code", type="password", required=True, secret=True, help="On the printer's screen under Settings, WLAN or Network."),
    )
    widgets = (
        WidgetType(kind="status", label="Printer", description="How far the print is, the time left, the layer, the temperatures and any error.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("progress", "nozzle", "bed")),
        WidgetType(kind="filament", label="Filament", description="What is loaded in each AMS slot: material, how much is left, and the AMS's humidity.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300),
    )

    def _target(self, config: dict[str, Any]) -> tuple[str, str, str]:
        host = str(config.get("host") or "").strip()
        serial = str(config.get("serial") or "").strip()
        if not host or not re.fullmatch(r"[A-Za-z0-9._:\[\]-]+", host):
            raise AdapterError("That is not an address.", code="bad_host")
        if not re.fullmatch(r"[A-Za-z0-9]+", serial):
            raise AdapterError("That is not a serial number.", code="bad_serial", hint="Letters and digits, from the printer's screen.")
        guard_outbound(f"http://[{host}]" if ":" in host and not host.startswith("[") else f"http://{host}")
        return host.strip("[]"), serial, str(config.get("access_code") or "")

    async def _open(self, host: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """The connection, as its own method so a test can hand in a plain one."""
        return await asyncio.open_connection(host, PORT, ssl=_context())

    async def _ask(self, host: str, serial: str, code: str) -> dict[str, Any]:
        reader, writer = await self._open(host)
        try:
            # Clean session, user and password, thirty seconds of keepalive.
            connect = _text("MQTT") + bytes([4, 0xC2]) + struct.pack("!H", 30) + _text(f"hexdeck-{serial[-6:]}") + _text("bblp") + _text(code)
            writer.write(_packet(0x10, connect))
            await writer.drain()
            kind, body = await _read_packet(reader)
            if kind >> 4 != 2 or len(body) < 2:
                raise AdapterError("This address answers, but not the way a Bambu printer does.", code="not_bambu")
            if body[1] in (4, 5):
                raise AuthFailed("The printer refused the access code.", hint="It is on the printer's screen, and changes when LAN mode is switched on again.")
            if body[1] != 0:
                raise AdapterError(f"The printer refused the connection (code {body[1]}).", code="refused",
                                   hint="Another client, such as Bambu Studio, may be holding the printer.")
            report = f"device/{serial}/report"
            writer.write(_packet(0x82, struct.pack("!H", 1) + _text(report) + b"\x00"))
            writer.write(_packet(0x30, _text(f"device/{serial}/request") + json.dumps(PUSH_ALL).encode()))
            await writer.drain()
            while True:
                kind, body = await _read_packet(reader)
                if kind >> 4 == 9 and body[2:] == b"\x80":
                    raise AdapterError("The printer refused to report under this serial number.", code="bad_serial",
                                       hint="Check the serial number on the printer's screen.")
                if kind >> 4 != 3:
                    continue
                length = struct.unpack("!H", body[:2])[0]
                skip = 2 + length + (2 if kind & 0x06 else 0)
                try:
                    message = json.loads(body[skip:])
                except ValueError:
                    continue
                found = message.get("print") if isinstance(message, dict) else None
                # Everything, not one of the fragments that may arrive first.
                if isinstance(found, dict) and found.get("command") == "push_status" and "gcode_state" in found:
                    return found
        finally:
            try:
                writer.write(b"\xe0\x00")
                writer.close()
                await writer.wait_closed()
            except (OSError, ssl.SSLError):
                pass

    async def _report(self, config: dict[str, Any], ctx: Context, fresh: bool = False) -> dict[str, Any]:
        host, serial, code = self._target(config)
        if not fresh and CACHE_KEY in ctx.cache and asyncio.get_running_loop().time() - ctx.cache[CACHE_KEY][0] < 45:
            return ctx.cache[CACHE_KEY][1]
        try:
            async with asyncio.timeout(TIMEOUT):
                found = await self._ask(host, serial, code)
        except TimeoutError as error:
            raise AdapterError("The printer did not report in time.", code="unreachable", hint="Check the serial number and that the printer is on.") from error
        except (asyncio.IncompleteReadError, ConnectionError) as error:
            raise AdapterError("The printer closed the connection.", code="unreachable", hint="Check the serial number and the access code.") from error
        except (OSError, ssl.SSLError) as error:
            raise AdapterError(f"The printer could not be reached: {error.__class__.__name__}.", code="unreachable", hint="Port 8883 has to be open; LAN mode may be needed.") from error
        ctx.cache[CACHE_KEY] = (asyncio.get_running_loop().time(), found)
        return found

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        found = await self._report(config, ctx, fresh=True)
        state = str(found.get("gcode_state") or "?")
        return f"The printer answers: {WORDS.get(state, state)}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        found = await self._report(config, ctx)
        return self._filament(found) if widget_kind == "filament" else self._status(found)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(found: dict[str, Any]) -> WidgetData:
        state = str(found.get("gcode_state") or "")
        busy = state in ("RUNNING", "PREPARE", "PAUSE", "SLICING")
        percent = int(found.get("mc_percent") or 0)
        error = int(found.get("print_error") or 0)
        hms = [hms_code(one) for one in found.get("hms") or [] if isinstance(one, dict)]
        secondary: list[dict[str, Any]] = [{"label": "Status", "value": WORDS.get(state, state or "?")}]
        job = str(found.get("subtask_name") or "").strip()
        if busy and job:
            secondary.append({"label": "Job", "value": job})
        if busy:
            minutes = int(found.get("mc_remaining_time") or 0)
            secondary.append({"label": "Time left", "value": f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"})
            if found.get("total_layer_num"):
                secondary.append({"label": "Layer", "value": f"{found.get('layer_num') or 0} / {found.get('total_layer_num')}"})
        nozzle, bed = found.get("nozzle_temper"), found.get("bed_temper")
        if isinstance(nozzle, (int, float)):
            secondary.append({"label": "Nozzle", "value": round(nozzle), "unit": "°C"})
        if isinstance(bed, (int, float)):
            secondary.append({"label": "Bed", "value": round(bed), "unit": "°C"})
        problems = ([error_code(error)] if error else []) + hms
        if problems:
            secondary.append({"label": "Errors", "value": ", ".join(problems[:3])})
        status = "bad" if state == "FAILED" or error else "warn" if hms or state == "PAUSE" else "ok"
        metrics = {"progress": float(percent) if busy else 0.0}
        if isinstance(nozzle, (int, float)):
            metrics["nozzle"] = float(nozzle)
        if isinstance(bed, (int, float)):
            metrics["bed"] = float(bed)
        meta = {"status_reason": WORDS.get(state, state) if state in ("FAILED", "PAUSE") else ", ".join(problems[:1])} if status != "ok" else {}
        primary = {"label": "Progress", "value": percent, "unit": "%"} if busy else {"label": "Status", "value": WORDS.get(state, state or "?")}
        return WidgetData(status=status, primary=primary, secondary=secondary[1:] if not busy else secondary, metrics=metrics, meta=meta)

    @staticmethod
    def _filament(found: dict[str, Any]) -> WidgetData:
        items = []
        for unit in (found.get("ams") or {}).get("ams") or []:
            if not isinstance(unit, dict):
                continue
            letter = chr(ord("A") + (int(unit.get("id") or 0) % 26))
            humidity = str(unit.get("humidity_raw") or "")
            for tray in unit.get("tray") or []:
                if not isinstance(tray, dict) or not tray.get("tray_type"):
                    continue
                left = tray.get("remain")
                known = isinstance(left, (int, float)) and left >= 0
                items.append({
                    "title": f"{letter}{int(tray.get('id') or 0) + 1} {tray.get('tray_sub_brands') or tray.get('tray_type')}",
                    "subtitle": f"{humidity} % humidity" if humidity else "",
                    "status": "unknown" if not known else "warn" if left < 10 else "ok",
                    "value": f"{round(left)}%" if known else "",
                    **({"progress": float(left)} if known else {}),
                })
        return WidgetData(status="warn" if any(item["status"] == "warn" for item in items) else "ok", items=items,
                          meta={"empty": "No filament is loaded in an AMS."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        found = {
            "command": "push_status", "gcode_state": "RUNNING", "mc_percent": 20 + tick % 70, "mc_remaining_time": 134 - tick % 120,
            "subtask_name": "Rack bracket", "layer_num": 41 + tick % 100, "total_layer_num": 212, "print_error": 0, "hms": [],
            "nozzle_temper": fake.walk("bambu-nozzle", tick, 218, 221), "bed_temper": fake.walk("bambu-bed", tick, 54, 56),
            "ams": {"ams": [{"id": "0", "humidity_raw": "18", "tray": [
                {"id": "0", "tray_type": "PLA", "tray_sub_brands": "PLA Matte", "remain": 93},
                {"id": "1", "tray_type": "PETG", "tray_sub_brands": "PETG HF", "remain": 41},
                {"id": "2", "tray_type": "PLA", "tray_sub_brands": "PLA Basic", "remain": 7},
                {"id": "3", "tray_type": ""},
            ]}]},
        }
        return self._filament(found) if widget_kind == "filament" else self._status(found)


ADAPTER = BambuLabAdapter()
