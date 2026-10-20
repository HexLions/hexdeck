"""NUT: a UPS straight from its Network UPS Tools server, such as the one TrueNAS runs.

Speaks the NUT network protocol (TCP, port 3493) with ``upsd`` itself, so no
PeaNUT or other go-between is needed. Checked against ``upsd`` 2.8.1 from
Debian 13 running locally on 20.10.2026 with the ``dummy-ups`` driver
playing a Back-UPS ES 700G: on line, on battery with the battery low, the
driver gone, a UPS name that does not exist and a wrong password.

⚠️ Reading needs no account. ``LIST VAR`` answered without ``USERNAME`` and
``PASSWORD``; those exist for ``upsmon`` to ``LOGIN`` and command a shutdown,
and NUT took a wrong password with ``OK`` and refused only the ``LOGIN``
(``ERR ACCESS-DENIED``). So the card asks for none and sends none.

⚠️ On TrueNAS the UPS service (System > Services > UPS) listens only on the
machine itself unless **Remote Monitor** is ticked; then it listens on every
interface, with the monitor user ``upsmon`` and the password ``fixmepass``
unless changed there. The UPS name is the service's **Identifier**.

⚠️ Errors are one line, ``ERR <WORD>``: ``UNKNOWN-UPS`` for a wrong name,
``DRIVER-NOT-CONNECTED`` when the driver behind ``upsd`` is gone (a USB cable
pulled, for instance), ``DATA-STALE`` when it stopped answering.

⚠️ ``ups.status`` is a list of words: ``OL`` on line, ``OB`` on battery,
``LB`` battery low, ``CHRG``/``DISCHRG``, ``RB`` replace battery, ``OVER``
overload, ``BYPASS``, ``CAL``, ``FSD`` forced shutdown. Values are quoted
strings; ``battery.runtime`` is in seconds.
"""

from __future__ import annotations

import asyncio
import re
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
    duration_short,
    guard_outbound,
)

TIMEOUT = 8.0
#: Variables read in one LIST VAR; a UPS has a few dozen.
MAX_LINES = 500
_VAR = re.compile(r'^VAR (\S+) (\S+) "(.*)"$')
_UPS = re.compile(r'^UPS (\S+) "(.*)"$')

#: The words of ``ups.status`` worth saying, in the order they matter.
WORDS = {
    "FSD": "Forced shutdown", "OB": "On battery", "LB": "Battery low", "RB": "Replace battery",
    "OVER": "Overload", "BYPASS": "On bypass", "CAL": "Calibrating", "OFF": "Output off", "OL": "On line",
}
NEEDS = {
    "UNKNOWN-UPS": ("upsd knows no UPS by that name.", "Use the UPS name, on TrueNAS the Identifier of the UPS service."),
    "DRIVER-NOT-CONNECTED": ("upsd cannot reach the UPS driver.", "Check the USB cable and the UPS driver on the machine the UPS is plugged into."),
    "DATA-STALE": ("The UPS driver stopped answering.", "Check the USB cable; upsd says the data is stale."),
}


def _unquote(value: str) -> str:
    return value.replace('\\"', '"').replace("\\\\", "\\")


async def read_vars(host: str, port: int, ups: str) -> tuple[dict[str, str], str]:
    """``LIST VAR`` of one UPS and the server's ``VER`` line."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=TIMEOUT)
    except (OSError, TimeoutError) as failure:
        raise AdapterError(f"No answer from {host}:{port}.", code="unreachable",
                           hint="On TrueNAS tick Remote Monitor in the UPS service; otherwise upsd listens only on the NAS itself.") from failure
    try:
        async def ask(line: str) -> str:
            writer.write(f"{line}\n".encode())
            await writer.drain()
            return (await asyncio.wait_for(reader.readline(), timeout=TIMEOUT)).decode("utf-8", "replace").rstrip("\r\n")

        version = await ask("VER")
        first = await ask(f"LIST VAR {ups}")
        if first.startswith("ERR "):
            word = first[4:].strip()
            message, hint = NEEDS.get(word, (f"upsd answered {word}.", "Check the UPS name and the NUT server."))
            raise AdapterError(message, code=word.lower().replace("-", "_"), hint=hint)
        if first != f"BEGIN LIST VAR {ups}":
            raise AdapterError(f"{host}:{port} answered, but not the way a NUT server does.", code="not_nut",
                               hint="The NUT server listens on port 3493.")
        found: dict[str, str] = {}
        for _ in range(MAX_LINES):
            line = (await asyncio.wait_for(reader.readline(), timeout=TIMEOUT)).decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("END LIST VAR") or not line:
                break
            match = _VAR.match(line)
            if match:
                found[match.group(2)] = _unquote(match.group(3))
        return found, version
    except (OSError, TimeoutError, asyncio.IncompleteReadError) as failure:
        raise AdapterError(f"{host}:{port} stopped answering.", code="unreachable") from failure
    finally:
        writer.close()


def _number(found: dict[str, str], name: str) -> float | None:
    try:
        return float(found[name])
    except (KeyError, ValueError):
        return None


class NutAdapter(Adapter):
    kind = "nut"
    label = "UPS (NUT)"
    category = "hosts"
    description = "A UPS straight from its NUT server, such as TrueNAS's UPS service: charge, runtime left, load, and whether it runs on battery."
    icon = "network-ups-tools"
    beta = True
    docs_url = "https://networkupstools.org/docs/developer-guide.chunked/net-protocol.html"
    fields = (
        Field("host", "Address", required=True, placeholder="truenas.lan",
              help="The machine the UPS is plugged into. On TrueNAS tick Remote Monitor in the UPS service."),
        Field("port", "Port", type="number", default=3493),
        Field("ups", "UPS name", required=True, default="ups", help="On TrueNAS the Identifier of the UPS service."),
    )
    widgets = (
        WidgetType(kind="status", label="UPS", description="Charge, runtime left, load and input voltage; red on battery.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("charge", "load", "runtime_min")),
    )

    def _target(self, config: dict[str, Any]) -> tuple[str, int, str]:
        host = str(config.get("host") or "").strip()
        ups = str(config.get("ups") or "ups").strip()
        if not host or not re.fullmatch(r"[A-Za-z0-9._:\[\]-]+", host):
            raise AdapterError("That is not an address.", code="bad_host")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", ups):
            raise AdapterError("That is not a UPS name.", code="bad_ups",
                               hint="Letters, digits, dot, hyphen and underscore, as TrueNAS allows them.")
        try:
            port = int(config.get("port") or 3493)
        except (TypeError, ValueError) as failure:
            raise AdapterError("That is not a port number.", code="bad_port") from failure
        if not 1 <= port <= 65535:
            raise AdapterError("That is not a port number.", code="bad_port")
        guard_outbound(f"http://[{host}]" if ":" in host and not host.startswith("[") else f"http://{host}")
        return host.strip("[]"), port, ups

    async def _read(self, config: dict[str, Any], ctx: Context, cache: float = 15) -> tuple[dict[str, str], str]:
        host, port, ups = self._target(config)
        key = f"nut:{host}:{port}:{ups}"
        kept = ctx.cache.get(key)
        if cache and kept and time.time() - kept[0] < cache:
            return kept[1]
        answer = await read_vars(host, port, ups)
        ctx.cache[key] = (time.time(), answer)
        return answer

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        found, _version = await self._read(config, ctx, cache=0)
        model = " ".join(part for part in (found.get("device.mfr") or found.get("ups.mfr"), found.get("device.model") or found.get("ups.model")) if part)
        return f"upsd answers for {model or 'the UPS'}: {self._words(found.get('ups.status', ''))}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        found, _version = await self._read(config, ctx)
        return self._status(found)

    @staticmethod
    def _words(status: str) -> str:
        flags = status.split()
        said = [text for word, text in WORDS.items() if word in flags]
        if "OB" in flags and "OL" in said:
            said.remove("On line")
        return ", ".join(said) or status or "?"

    @staticmethod
    def _status(found: dict[str, str]) -> WidgetData:
        flags = found.get("ups.status", "").split()
        charge = _number(found, "battery.charge")
        runtime = _number(found, "battery.runtime")
        load = _number(found, "ups.load")
        voltage = _number(found, "input.voltage")
        if {"OB", "LB", "FSD", "OVER"} & set(flags):
            state = "bad"
        elif {"RB", "BYPASS"} & set(flags) or (charge is not None and charge < 50):
            state = "warn"
        elif "OL" in flags:
            state = "ok"
        else:
            state = "unknown"
        secondary: list[dict[str, Any]] = [{"label": "Status", "value": NutAdapter._words(found.get("ups.status", ""))}]
        if runtime is not None:
            secondary.append({"label": "Runtime", "value": duration_short(runtime)})
        if load is not None:
            secondary.append({"label": "Load", "value": round(load), "unit": "%"})
        if voltage is not None:
            secondary.append({"label": "Input", "value": round(voltage), "unit": "V"})
        meta: dict[str, Any] = {}
        if state == "bad":
            meta["status_reason"] = NutAdapter._words(found.get("ups.status", ""))
        metrics = {name: value for name, value in (("charge", charge), ("load", load),
                                                   ("runtime_min", round(runtime / 60, 1) if runtime is not None else None)) if value is not None}
        return WidgetData(status=state, primary={"label": "Charge", "value": round(charge) if charge is not None else "?", "unit": "%" if charge is not None else ""},
                          secondary=secondary, meta=meta, metrics=metrics)

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        on_battery = tick % 12 >= 10
        found = {
            "ups.status": "OB DISCHRG" if on_battery else "OL",
            "battery.charge": str(round(fake.walk("nut-charge", tick, 86, 100) - (12 if on_battery else 0))),
            "battery.runtime": str(int(fake.walk("nut-runtime", tick, 2100, 2600))),
            "ups.load": str(round(fake.walk("nut-load", tick, 18, 31))),
            "input.voltage": "0.0" if on_battery else str(round(fake.walk("nut-volt", tick, 228, 234), 1)),
            "device.model": "Back-UPS ES 700G",
        }
        return self._status(found)


ADAPTER = NutAdapter()
