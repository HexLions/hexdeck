"""Tasmota: a device's relays, what it draws, and every sensor it reads.

Every Tasmota device answers commands over HTTP at ``/cm?cmnd=...``. One
command, ``Status 0``, hands out everything in one JSON object: ``Status``
(the name), ``StatusFWR`` (the firmware), ``StatusSNS`` (every sensor,
``ENERGY`` among them on a plug that measures) and ``StatusSTS`` (the relays
as ``POWER`` or ``POWER1`` to ``POWERn``, uptime, Wi-Fi).

⚠️ A web password, when one is set, goes as HTTP Basic for the user
``admin``; Tasmota tries the header first and the ``user`` and ``password``
parameters only after it, so the password never has to be in the address.
A wrong one is answered with 401 and ``{"WARNING": "Need user=..."}``.

⚠️ A plug with two measuring channels reports ``Power`` as a list. The card
shows the sum, the list shows each.

⚠️ Relays count from 1 in Tasmota's commands. A device with one relay calls
it ``POWER`` in its state, and ``Power1`` switches it all the same.

Written from Tasmota's own source (``CmndStatus``, ``MqttShowState`` and
``HandleHttpCommand``, release v15.6.0). Not yet run against a device.
"""

from __future__ import annotations

import re
from typing import Any

from . import demo as fake
from .base import (
    Action,
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    duration_short,
    measured,
)

STATUS_SECONDS = 10
#: Units for the readings Tasmota names the same way on every sensor.
UNITS = {"Temperature": "°", "DewPoint": "°", "Humidity": "%", "Pressure": "hPa", "Illuminance": "lx",
         "Power": "W", "ApparentPower": "VA", "ReactivePower": "var", "Voltage": "V", "Current": "A",
         "Total": "kWh", "Today": "kWh", "Yesterday": "kWh", "Frequency": "Hz", "CarbonDioxide": "ppm", "Distance": "cm"}
#: Words in ``StatusSNS`` that are not readings.
NOT_READINGS = {"Time", "TempUnit", "PressureUnit", "SpeedUnit", "TotalStartTime"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum(value: Any) -> float | None:
    """A reading that is one number, or a list of them for several channels."""
    if isinstance(value, list):
        numbers = [number for number in (_number(one) for one in value) if number is not None]
        return sum(numbers) if numbers else None
    return _number(value)


def relays(state: dict[str, Any]) -> list[tuple[int, bool]]:
    """The relays of ``StatusSTS`` as (number from 1, on)."""
    found = []
    for key, value in state.items():
        match = re.fullmatch(r"POWER(\d*)", key)
        if match and isinstance(value, str):
            found.append((int(match.group(1) or 1), value.upper() == "ON"))
    return sorted(found)


def _name(status: dict[str, Any]) -> str:
    device = status.get("Status") or {}
    names = device.get("FriendlyName")
    first = names[0] if isinstance(names, list) and names else names
    return str(device.get("DeviceName") or first or "Tasmota")


def readings(sensors: dict[str, Any]) -> list[tuple[str, float, str]]:
    """Every number of ``StatusSNS`` as (sensor and reading, value, unit)."""
    temperature = "°" + str(sensors.get("TempUnit") or "C")
    rows = []
    for sensor, values in sensors.items():
        if sensor in NOT_READINGS or not isinstance(values, dict):
            continue
        for reading, value in values.items():
            if reading in NOT_READINGS:
                continue
            number = _sum(value)
            if number is None:
                continue
            unit = UNITS.get(reading, "")
            if unit == "°":
                unit = temperature
            elif reading == "Pressure":
                unit = str(sensors.get("PressureUnit") or "hPa")
            rows.append((f"{sensor} {reading}", number, unit))
    return rows


class TasmotaAdapter(Adapter):
    kind = "tasmota"
    label = "Tasmota"
    category = "home"
    description = "A Tasmota device: its relays with a button to switch them, what it draws, and every sensor it reads."
    icon = "tasmota"
    docs_url = "https://tasmota.github.io/docs/Commands/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://192.168.1.60",
              help="The device on your own network."),
        Field("password", "Password", type="password", secret=True,
              help="Only when a web admin password is set on the device; its user name is always admin."),
    )
    widgets = (
        WidgetType(kind="device", label="Device", description="The relay, the power and today's energy on a plug that measures, the Wi-Fi signal and how long it has run.",
                   renderer="value", default_size=(3, 2), refresh_seconds=15, metrics=("power",),
                   options=(
                       Field("relay", "Relay", type="number", default=1, help="1 for a device with one relay; Tasmota counts its relays from 1."),
                       Field("switching", "Offer the switch", type="bool", default=True,
                             help="Off makes the card read-only, which is what a card on a wall tablet usually wants."),
                   )),
        WidgetType(kind="sensors", label="Sensors", description="Every reading of the device's sensors, and each relay with its own button.",
                   renderer="list", default_size=(3, 3), refresh_seconds=30,
                   options=(Field("switching", "Offer the switch", type="bool", default=True),)),
    )

    async def _command(self, config: dict[str, Any], ctx: Context, command: str, cache: float) -> dict[str, Any]:
        auth = ("admin", str(config["password"])) if config.get("password") else None
        response = await ctx.request("GET", f"{base_url(config)}/cm", params={"cmnd": command}, auth=auth,
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code == 401:
            raise AuthFailed("Tasmota refused the password." if auth else "Tasmota asks for its web password.",
                             hint="The web admin password set under Configuration > Other; the user name is admin.")
        if response.status_code >= 400:
            raise AdapterError(f"Tasmota answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of the device itself, as you open its web interface.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Tasmota did not answer with JSON.", code="not_json",
                               hint="The address of the device itself, as you open its web interface.") from failure
        if not isinstance(answer, dict):
            raise AdapterError("Tasmota answered with something unexpected.", code="bad_answer")
        if "Command" in answer and answer.get("Command") == "Error":
            raise AdapterError("Tasmota did not know the command.", code="bad_answer")
        return answer

    async def _status(self, config: dict[str, Any], ctx: Context, cache: float = STATUS_SECONDS) -> dict[str, Any]:
        return await self._command(config, ctx, "Status 0", cache)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        status = await self._status(config, ctx, cache=0)
        version = str((status.get("StatusFWR") or {}).get("Version") or "").split("(")[0]
        count = len(relays(status.get("StatusSTS") or {}))
        return f"{_name(status)} answers{f' with Tasmota {version}' if version else ''} and {count} relay{'s' if count != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        status = await self._status(config, ctx)
        if widget_kind == "sensors":
            return self._sensors(status, options)
        return self._device(status, options)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id not in ("on", "off"):
            raise AdapterError("Unknown action.", code="no_such_action")
        relay = max(1, int(_number(params.get("relay")) or 1))
        await self._command(config, ctx, f"Power{relay} {'On' if action_id == 'on' else 'Off'}", 0)
        # The card has to show the new state, not the one from ten seconds ago.
        ctx.cache.clear()
        return f"Relay {relay} switched {action_id}."

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _button(relay: int, on: bool, switching: bool) -> list[Action]:
        if not switching:
            return []
        # One button that does the opposite of the current state, as on the Shelly card.
        if on:
            return [Action(id="off", label="Switch off", icon="power", params={"relay": relay})]
        return [Action(id="on", label="Switch on", icon="power", params={"relay": relay})]

    @classmethod
    def _device(cls, status: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        state = status.get("StatusSTS") or {}
        energy = (status.get("StatusSNS") or {}).get("ENERGY") or {}
        all_relays = relays(state)
        wanted = max(1, int(_number(options.get("relay")) or 1))
        relay = next((one for one in all_relays if one[0] == wanted), all_relays[0] if all_relays else None)
        power = _sum(energy.get("Power")) if isinstance(energy, dict) else None
        wifi = state.get("Wifi") or {}
        secondary: list[dict[str, Any]] = []
        if relay is not None and power is not None:
            secondary.append({"label": "Relay", "value": "on" if relay[1] else "off"})
        today = _number(energy.get("Today")) if isinstance(energy, dict) else None
        if today is not None:
            secondary.append({"label": "Today", "value": round(today, 3), "unit": "kWh"})
        if _number(wifi.get("Signal")) is not None:
            secondary.append({"label": "Wi-Fi", "value": int(wifi["Signal"]), "unit": "dBm"})
        if _number(state.get("UptimeSec")) is not None:
            secondary.append({"label": "Up", "value": duration_short(_number(state["UptimeSec"]))})
        if power is not None:
            primary: dict[str, Any] = {"label": "Power", "value": round(power, 1), "unit": "W", "metric": "power"}
        elif relay is not None:
            primary = {"label": f"Relay {relay[0]}" if len(all_relays) > 1 else "Relay", "value": "on" if relay[1] else "off"}
        else:
            primary = {"label": _name(status), "value": "–"}
        return WidgetData(
            status="ok",
            primary=primary,
            secondary=secondary,
            actions=cls._button(relay[0], relay[1], bool(options.get("switching", True))) if relay is not None else [],
            metrics=measured({"power": power}),
            meta={"device": _name(status)},
        )

    @classmethod
    def _sensors(cls, status: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        switching = bool(options.get("switching", True))
        rows: list[dict[str, Any]] = []
        all_relays = relays(status.get("StatusSTS") or {})
        for number, on in all_relays:
            rows.append({"id": f"relay-{number}", "title": f"Relay {number}" if len(all_relays) > 1 else "Relay",
                         "value": "on" if on else "off", "status": "ok" if on else "unknown",
                         "actions": [action.model_dump() for action in cls._button(number, on, switching)]})
        for title, value, unit in readings(status.get("StatusSNS") or {}):
            rows.append({"id": title, "title": title, "value": f"{value:g} {unit}".strip(), "status": "ok"})
        return WidgetData(status="ok", items=rows, meta={"device": _name(status), "empty": "No relay and no sensor"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        power = round(fake.walk("tasmota-power", tick, 40, 160), 1)
        status = {
            "Status": {"DeviceName": "Desk plug", "FriendlyName": ["Desk plug"]},
            "StatusFWR": {"Version": "15.0.1(release-tasmota)"},
            "StatusSNS": {"Time": "", "ENERGY": {"Total": 412.318, "Yesterday": 1.204, "Today": round(0.6 + tick % 30 / 100, 3),
                                                 "Power": power, "Voltage": 231, "Current": round(power / 231, 3)},
                          "DS18B20": {"Temperature": 22.4}, "TempUnit": "C"},
            "StatusSTS": {"UptimeSec": 431_200 + tick * 15, "POWER": "ON", "Wifi": {"RSSI": 78, "Signal": -61}},
        }
        if widget_kind == "sensors":
            return self._sensors(status, options)
        return self._device(status, options)


ADAPTER = TasmotaAdapter()
