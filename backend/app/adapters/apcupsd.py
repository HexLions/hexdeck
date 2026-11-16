"""apcupsd: an APC UPS straight from the daemon that watches it, without NUT.

Written from apcupsd 3.14.14's source (``src/apcnis.c``, ``src/lib/apclibnis.c``, ``src/lib/apcstatus.c``). Not yet
run against a UPS: apcupsd answers only with a UPS on its cable.

- Its network information server listens on TCP 3551 (``NISPORT``, registered with the IANA) while ``NETSERVER`` is
  on; ``NISIP`` decides on which address, 0.0.0.0 unless it was set to 127.0.0.1.
- Every message in either direction is a two-byte length in network order and that many bytes (``net_send``,
  ``net_recv``). The client sends ``status``; the server answers one record per line, ``KEY      : value``, padded to
  nine characters, and a record of length 0 at the end. Anything but ``status`` and ``events`` answers
  ``Invalid command``. There is no password: the protocol has none.
- ``STATUS`` is a row of words: ``ONLINE``, ``ONBATT``, ``LOWBATT``, ``REPLACEBATT``, ``OVERLOAD``, ``CAL``, ``TRIM``,
  ``BOOST``, ``NOBATT``, ``SLAVE``, ``SLAVEDOWN``; ``COMMLOST`` and ``SHUTTING DOWN`` replace all the others.
- Numbers come with their unit after them: ``BCHARGE  : 100.0 Percent``, ``TIMELEFT : 38.2 Minutes``,
  ``LOADPCT  : 12.0 Percent``, ``LINEV    : 230.0 Volts``, ``NOMPOWER : 865 Watts``. A field the UPS does not report
  is left out, never written as 0.

⚠️ The answer also names the host apcupsd runs on (``HOSTNAME``) and its serial number; neither is shown.
"""

from __future__ import annotations

import asyncio
import re
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
    duration_short,
    guard_outbound,
)

TIMEOUT = 8.0
#: Far more than apcupsd sends (about sixty records), and an end to a server that never says it is done.
MAX_RECORDS = 400

WORDS = {
    "ONLINE": "On line", "ONBATT": "On battery", "LOWBATT": "Battery low", "REPLACEBATT": "Replace battery",
    "OVERLOAD": "Overload", "CAL": "Calibrating", "TRIM": "Lowering a high input", "BOOST": "Raising a low input",
    "NOBATT": "No battery", "COMMLOST": "No contact with the UPS", "SHUTTING": "Shutting down",
}
BAD = {"ONBATT", "LOWBATT", "OVERLOAD", "COMMLOST", "SHUTTING", "NOBATT"}
WARN = {"REPLACEBATT"}


async def read_status(host: str, port: int) -> dict[str, str]:
    """The ``status`` records of apcupsd, as ``{KEY: value}``."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=TIMEOUT)
    except (OSError, TimeoutError) as failure:
        raise AdapterError(f"No answer from {host}:{port}.", code="unreachable",
                           hint="apcupsd answers on port 3551 while NETSERVER is on, and NISIP must not be 127.0.0.1.") from failure
    try:
        writer.write(struct.pack(">H", 6) + b"status")
        await writer.drain()
        found: dict[str, str] = {}
        for _ in range(MAX_RECORDS):
            size = struct.unpack(">H", await asyncio.wait_for(reader.readexactly(2), timeout=TIMEOUT))[0]
            if size == 0:
                return found
            line = (await asyncio.wait_for(reader.readexactly(size), timeout=TIMEOUT)).decode("utf-8", "replace").rstrip("\r\n")
            key, colon, value = line.partition(":")
            if not colon:
                raise AdapterError(f"apcupsd answered {line[:60]!r}.", code="not_apcupsd",
                                   hint="The port is 3551 of apcupsd, not a web page.")
            found[key.strip()] = value.strip()
        raise AdapterError("apcupsd did not end its answer.", code="not_apcupsd")
    except (OSError, TimeoutError, asyncio.IncompleteReadError) as failure:
        raise AdapterError(f"{host}:{port} stopped answering, or does not speak like apcupsd.", code="not_apcupsd",
                           hint="The port is 3551 of apcupsd.") from failure
    finally:
        writer.close()


def _number(found: dict[str, str], key: str) -> float | None:
    """``38.2 Minutes`` as 38.2; ``None`` when the UPS does not report it."""
    match = re.match(r"-?\d+(?:\.\d+)?", found.get(key, ""))
    return float(match.group(0)) if match else None


def _words(status: str) -> list[str]:
    return status.split()


class ApcupsdAdapter(Adapter):
    kind = "apcupsd"
    label = "UPS (apcupsd)"
    category = "hosts"
    description = "An APC UPS through apcupsd: charge, runtime left, load, and whether it runs on battery."
    icon = "apc"
    beta = True
    docs_url = "http://www.apcupsd.org/"
    fields = (
        Field("host", "Address", required=True, placeholder="nas.lan",
              help="The machine apcupsd runs on, with NETSERVER on in apcupsd.conf."),
        Field("port", "Port", type="number", default=3551),
    )
    widgets = (
        WidgetType(kind="status", label="UPS", description="Charge, runtime left, load and input voltage; red on battery.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("charge", "load", "runtime_min", "power_w")),
        WidgetType(kind="details", label="UPS details",
                   description="Model, firmware, battery voltage and date, the power it can give, transfers to battery and the last self-test.",
                   renderer="stats", default_size=(4, 3), refresh_seconds=300),
    )

    def _target(self, config: dict[str, Any]) -> tuple[str, int]:
        host = str(config.get("host") or "").strip()
        if not host or not re.fullmatch(r"[A-Za-z0-9._:\[\]-]+", host):
            raise AdapterError("That is not an address.", code="bad_host")
        try:
            port = int(float(config.get("port") or 3551))
        except (TypeError, ValueError) as failure:
            raise AdapterError("That is not a port number.", code="bad_port") from failure
        if not 1 <= port <= 65535:
            raise AdapterError("That is not a port number.", code="bad_port")
        guard_outbound(f"http://[{host}]" if ":" in host and not host.startswith("[") else f"http://{host}")
        return host.strip("[]"), port

    async def _read(self, config: dict[str, Any], ctx: Context, cache: float = 15) -> dict[str, str]:
        host, port = self._target(config)
        key = f"apcupsd:{host}:{port}"
        kept = ctx.cache.get(key)
        if cache and kept and time.time() - kept[0] < cache:
            return kept[1]
        found = await read_status(host, port)
        ctx.cache[key] = (time.time(), found)
        return found

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        found = await self._read(config, ctx, cache=0)
        if "STATUS" not in found:
            raise AdapterError("apcupsd answered without a status.", code="not_apcupsd")
        model = found.get("MODEL") or found.get("UPSNAME") or "the UPS"
        return f"apcupsd {found.get('VERSION', '?').split(' ')[0]} answers for {model}: {self._said(found['STATUS'])}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        found = await self._read(config, ctx)
        if widget_kind == "details":
            return self._details(found)
        return self._status(found)

    @staticmethod
    def _said(status: str) -> str:
        return ", ".join(WORDS[word] for word in _words(status) if word in WORDS) or status or "?"

    @staticmethod
    def _status(found: dict[str, str]) -> WidgetData:
        words = set(_words(found.get("STATUS", "")))
        charge, minutes = _number(found, "BCHARGE"), _number(found, "TIMELEFT")
        load, voltage, rated = _number(found, "LOADPCT"), _number(found, "LINEV"), _number(found, "NOMPOWER")
        if words & BAD:
            state = "bad"
        elif words & WARN or (charge is not None and charge < 50):
            state = "warn"
        elif "ONLINE" in words:
            state = "ok"
        else:
            state = "unknown"
        said = ApcupsdAdapter._said(found.get("STATUS", ""))
        secondary: list[dict[str, Any]] = [{"label": "Status", "value": said}]
        if minutes is not None:
            secondary.append({"label": "Runtime left", "value": duration_short(minutes * 60)})
        if load is not None:
            secondary.append({"label": "Load", "value": round(load), "unit": "%"})
        if voltage is not None:
            secondary.append({"label": "Input", "value": round(voltage), "unit": "V"})
        # apcupsd has no measured watts: the load share of the rated watts, as apcupsd's own CGI pages work it out.
        power = load * rated / 100 if load is not None and rated else None
        if power is not None:
            secondary.append({"label": "Power", "value": round(power), "unit": "W"})
        meta: dict[str, Any] = {"flags": sorted(words)}
        if state == "bad":
            meta["status_reason"] = said
        metrics = {name: value for name, value in (("charge", charge), ("load", load), ("runtime_min", minutes),
                                                   ("power_w", round(power) if power is not None else None)) if value is not None}
        return WidgetData(status=state, primary={"label": "Charge", "value": round(charge) if charge is not None else "?", "unit": "%" if charge is not None else ""},
                          secondary=secondary, meta=meta, metrics=metrics)

    @staticmethod
    def _details(found: dict[str, str]) -> WidgetData:
        rows: list[dict[str, Any]] = []

        def add(label: str, value: Any) -> None:
            if value not in (None, ""):
                rows.append({"label": label, "value": value})

        add("Model", found.get("MODEL"))
        add("Firmware", found.get("FIRMWARE"))
        battery, nominal = _number(found, "BATTV"), _number(found, "NOMBATTV")
        if battery is not None:
            add("Battery", f"{battery:g} V" + (f" of {nominal:g} V" if nominal else ""))
        add("Battery date", found.get("BATTDATE"))
        rated = _number(found, "NOMPOWER")
        if rated is not None:
            add("Nominal power", f"{rated:g} W")
        output = _number(found, "OUTPUTV")
        if output is not None:
            add("Output", f"{output:g} V")
        temperature = _number(found, "ITEMP")
        if temperature is not None:
            add("Temperature", f"{temperature:g} °C")
        transfers = _number(found, "NUMXFERS")
        if transfers is not None:
            add("Transfers to battery", int(transfers))
        add("Last transfer", found.get("LASTXFER"))
        add("Self-test", {"OK": "Passed", "NG": "Failed", "BT": "Failed, battery capacity", "WN": "Warning",
                          "IP": "In progress", "NO": "None yet"}.get(found.get("SELFTEST", ""), found.get("SELFTEST")))
        add("Driver", found.get("DRIVER"))
        # The same shape as the details of a UPS through NUT, so the card draws both alike.
        status = ApcupsdAdapter._status(found).status
        if status == "ok" and found.get("SELFTEST") in ("NG", "BT", "WN"):
            status = "warn"
        charge = _number(found, "BCHARGE")
        return WidgetData(status=status,
                          primary={"label": "Charge", "value": round(charge) if charge is not None else "?", "unit": "%" if charge is not None else ""},
                          secondary=rows)

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        found = {
            "STATUS": "ONLINE", "MODEL": "Back-UPS RS 1500MS2", "FIRMWARE": "969.e3 .I USB FW:e3", "DRIVER": "USB UPS Driver",
            "BCHARGE": "100.0 Percent", "TIMELEFT": f"{fake.walk('apcupsd-runtime', tick, 34, 41):.1f} Minutes",
            "LOADPCT": f"{fake.walk('apcupsd-load', tick, 11, 19):.1f} Percent", "LINEV": "231.0 Volts", "NOMPOWER": "900 Watts",
            "BATTV": "27.2 Volts", "NOMBATTV": "24.0 Volts", "BATTDATE": "2024-03-11", "OUTPUTV": "230.0 Volts",
            "NUMXFERS": "2", "LASTXFER": "Low line voltage", "SELFTEST": "OK",
        }
        return self._details(found) if widget_kind == "details" else self._status(found)


ADAPTER = ApcupsdAdapter()
