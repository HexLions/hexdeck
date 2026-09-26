"""Shelly: what a relay is doing, what it is drawing, and a button to flip it.

The devices are asked directly on the local network. Nothing goes through
Shelly's cloud, and no account exists here: a Shelly answers its own HTTP
interface, and that is all this adapter uses.

Two generations speak differently and both are handled:

* **Gen 1** (Shelly 1, 1PM, 2.5, Plug S, EM, 3EM) answers ``/status`` with
  ``relays``, ``meters`` and a temperature beside them. ⚠️ Its energy counter
  is in **watt-minutes**, not watt-hours, which is the one unit mistake that
  makes a card read sixty times too high.
* **Gen 2 and later** (Plus, Pro, Mini, Gen3, Gen4) answer
  ``/rpc/Shelly.GetStatus`` with a component per key: ``switch:0``, ``light:0``,
  ``pm1:0``, ``em:0``, and ``sys`` beside them. Their counters are in
  watt-hours.

``/shelly`` is the one address both generations answer, so it is what decides
which of the two is in front of us. The answer is kept for an hour; a device
does not change generation.

⚠️ A Gen 2 device with authentication switched on wants digest, which this
adapter cannot speak yet; it says so instead of failing with a bare 401. Gen 1
uses ordinary basic authentication and works with the password field.
"""

from __future__ import annotations

from typing import Any

from . import demo as fake
from .base import (
    Action,
    Adapter,
    AdapterError,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    duration_short,
    measured,
)

#: A relay's state is worth asking for often; the device is on the same network.
STATUS_SECONDS = 5
INFO_SECONDS = 3600


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def kwh_from_gen1(total: Any) -> float | None:
    """Gen 1 counts watt-minutes. Sixty of them are a watt-hour."""
    watt_minutes = _number(total)
    return None if watt_minutes is None else round(watt_minutes / 60.0 / 1000.0, 3)


def kwh_from_wh(total: Any) -> float | None:
    watt_hours = _number(total)
    return None if watt_hours is None else round(watt_hours / 1000.0, 3)


class ShellyAdapter(Adapter):
    kind = "shelly"
    label = "Shelly"
    category = "home"
    description = "What a Shelly relay is doing, what it draws right now, and a button to switch it."
    icon = "shelly"
    beta = True
    docs_url = "https://shelly-api-docs.shelly.cloud/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://192.168.1.50",
              help="The device on your own network. Shelly's cloud is not used and no account is needed."),
        Field("password", "Password", type="password", secret=True,
              help="Only for a Gen 1 device with authentication on; its user name is always admin. Gen 2 and later want digest, which is not supported yet."),
    )
    widgets = (
        WidgetType(kind="device", label="Device", description="Power now, the energy counter, the temperature and a button to switch the relay.",
                   renderer="value", default_size=(3, 2), refresh_seconds=15, metrics=("power", "energy"),
                   options=(
                       Field("channel", "Channel", type="number", default=0,
                             help="0 for a single relay. A Shelly 2.5 or Pro 4PM counts its outputs from 0."),
                       Field("switching", "Offer the switch", type="bool", default=True,
                             help="Off makes the card read-only, which is what a card on a wall tablet usually wants."),
                   )),
        WidgetType(kind="channels", label="Outputs", description="Every output of the device with its state and what it draws, each with its own button.",
                   renderer="list", default_size=(3, 2), refresh_seconds=15, metrics=("power",),
                   options=(Field("switching", "Offer the switch", type="bool", default=True),)),
    )

    # -- talking to the device ------------------------------------------------

    @staticmethod
    def _auth(config: dict[str, Any]) -> tuple[str, str] | None:
        password = str(config.get("password") or "")
        return ("admin", password) if password else None

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float,
                   params: dict[str, Any] | None = None) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}{path}", params=params,
                                     auth=self._auth(config), cache_seconds=cache, auth_errors=False)
        if response.status_code == 401:
            # AuthFailed carries a fixed hint, and the hint is the whole point
            # here: the password field cannot help a Gen 2 device.
            raise AdapterError(
                "The device wants a password.",
                code="auth_failed",
                hint="A Gen 1 device takes the password field (its user is admin). Gen 2 and later use digest authentication, "
                     "which HexDeck cannot speak yet; switch authentication off for the device or put it on a network HexDeck may reach.",
            )
        if response.status_code == 404:
            raise AdapterError("That address answers, but not the way a Shelly does.", code="not_shelly",
                               hint="Use the device's own address, not the cloud and not a proxy in front of it.")
        if response.status_code >= 400:
            raise AdapterError(f"The device answered with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("The device did not answer with JSON.", code="not_json") from failure
        if not isinstance(answer, dict):
            raise AdapterError("That address answers, but not the way a Shelly does.", code="not_shelly")
        return answer

    async def _generation(self, config: dict[str, Any], ctx: Context) -> int:
        """1 or 2 and up, from the one address both generations answer."""
        info = await self._get(config, ctx, "/shelly", INFO_SECONDS)
        generation = _number(info.get("gen"))
        return int(generation) if generation else 1

    async def _state(self, config: dict[str, Any], ctx: Context, cache: float = STATUS_SECONDS) -> dict[str, Any]:
        generation = await self._generation(config, ctx)
        if generation >= 2:
            return {**self._from_gen2(await self._get(config, ctx, "/rpc/Shelly.GetStatus", cache)), "generation": generation}
        return self._from_gen1(await self._get(config, ctx, "/status", cache))

    # -- the two shapes, made into one ---------------------------------------

    @staticmethod
    def _from_gen1(status: dict[str, Any]) -> dict[str, Any]:
        relays = [one for one in status.get("relays") or [] if isinstance(one, dict)]
        meters = [one for one in status.get("meters") or [] if isinstance(one, dict)]
        emeters = [one for one in status.get("emeters") or [] if isinstance(one, dict)]
        channels = []
        for index, relay in enumerate(relays):
            meter = meters[index] if index < len(meters) else {}
            channels.append({
                "id": index,
                "name": f"Output {index}",
                "on": bool(relay.get("ison")),
                "power": _number(meter.get("power")),
                "energy": kwh_from_gen1(meter.get("total")),
                "alarm": "Overpower" if relay.get("overpower") else "",
            })
        # An EM or 3EM has no relay to switch, only clamps to read.
        for index, clamp in enumerate(emeters):
            channels.append({
                "id": index, "name": f"Clamp {index}", "on": None,
                "power": _number(clamp.get("power")), "energy": kwh_from_wh(clamp.get("total")), "alarm": "",
            })
        temperature = _number(status.get("temperature"))
        if temperature is None and isinstance(status.get("tmp"), dict):
            temperature = _number(status["tmp"].get("tC"))
        update = status.get("update") if isinstance(status.get("update"), dict) else {}
        wifi = status.get("wifi_sta") if isinstance(status.get("wifi_sta"), dict) else {}
        return {
            "generation": 1,
            "channels": channels,
            "temperature": temperature,
            "uptime": _number(status.get("uptime")),
            "rssi": _number(wifi.get("rssi")),
            "update": str(update.get("new_version") or "") if update.get("has_update") else "",
            "alarm": "Too hot" if status.get("overtemperature") else "",
        }

    @staticmethod
    def _from_gen2(status: dict[str, Any]) -> dict[str, Any]:
        channels: list[dict[str, Any]] = []
        temperature: float | None = None
        for key, value in sorted(status.items()):
            if not isinstance(value, dict) or ":" not in key:
                continue
            component, _, number = key.partition(":")
            if component not in ("switch", "light", "pm1", "em1"):
                continue
            energy = value.get("aenergy") if isinstance(value.get("aenergy"), dict) else {}
            inside = value.get("temperature") if isinstance(value.get("temperature"), dict) else {}
            if temperature is None:
                temperature = _number(inside.get("tC"))
            channels.append({
                "id": int(number) if number.isdigit() else 0,
                "name": f"{component.capitalize()} {number}" if component != "switch" else f"Output {number}",
                # A meter has no output to switch; only a switch and a light do.
                "on": bool(value.get("output")) if component in ("switch", "light") else None,
                # A switch reports apower, an energy meter act_power. Same watt.
                "power": _number(value.get("apower") if value.get("apower") is not None else value.get("act_power")),
                "energy": kwh_from_wh(energy.get("total")),
                "alarm": "Overpower" if "overpower" in (value.get("errors") or []) else "",
                "kind": component,
            })
        system = status.get("sys") if isinstance(status.get("sys"), dict) else {}
        wifi = status.get("wifi") if isinstance(status.get("wifi"), dict) else {}
        available = system.get("available_updates") if isinstance(system.get("available_updates"), dict) else {}
        stable = available.get("stable") if isinstance(available.get("stable"), dict) else {}
        # A three-phase Pro reports the house total in one place; it is worth a row.
        total = status.get("em:0") if isinstance(status.get("em:0"), dict) else {}
        if total:
            channels.append({"id": 0, "name": "Total", "on": None, "power": _number(total.get("total_act_power")),
                             "energy": None, "alarm": "", "kind": "em"})
        return {
            "generation": 2,
            "channels": channels,
            "temperature": temperature,
            "uptime": _number(system.get("uptime")),
            "rssi": _number(wifi.get("rssi")),
            "update": str(stable.get("version") or "") if stable else "",
            "alarm": "Restart required" if system.get("restart_required") else "",
        }

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        info = await self._get(config, ctx, "/shelly", 0)
        state = await self._state(config, ctx, cache=0)
        name = str(info.get("name") or info.get("model") or info.get("type") or "A Shelly")
        return f"{name} answers as generation {state['generation']} with {len(state['channels'])} outputs."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        state = await self._state(config, ctx)
        if widget_kind == "channels":
            return self._channels(state, options)
        return self._device(state, options)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id not in ("on", "off", "toggle"):
            raise AdapterError("Unknown action.", code="no_such_action")
        channel = int(_number(params.get("channel")) or 0)
        generation = await self._generation(config, ctx)
        if generation >= 2:
            if action_id == "toggle":
                path, query = "/rpc/Switch.Toggle", {"id": channel}
            else:
                path, query = "/rpc/Switch.Set", {"id": channel, "on": "true" if action_id == "on" else "false"}
        else:
            path, query = f"/relay/{channel}", {"turn": action_id}
        await self._get(config, ctx, path, 0, query)
        # The card has to show the new state, not the one from four seconds ago.
        ctx.cache.clear()
        return {"on": f"Output {channel} switched on.", "off": f"Output {channel} switched off.",
                "toggle": f"Output {channel} toggled."}[action_id]

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _actions(channel: dict[str, Any], switching: bool) -> list[Action]:
        if not switching or channel.get("on") is None:
            return []
        # One button that does the opposite of the current state reads better on
        # a card than two that are half wrong at any moment.
        if channel["on"]:
            return [Action(id="off", label="Switch off", icon="power", params={"channel": channel["id"]})]
        return [Action(id="on", label="Switch on", icon="power", params={"channel": channel["id"]})]

    @classmethod
    def _device(cls, state: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        wanted = int(_number(options.get("channel")) or 0)
        channels = state["channels"]
        channel = next((one for one in channels if one["id"] == wanted and one.get("on") is not None), None) \
            or next((one for one in channels if one["id"] == wanted), None) \
            or (channels[0] if channels else {"id": wanted, "name": f"Output {wanted}", "on": None, "power": None, "energy": None, "alarm": ""})
        secondary: list[dict[str, Any]] = []
        if channel.get("on") is not None:
            secondary.append({"label": "Relay", "value": "on" if channel["on"] else "off"})
        if channel.get("energy") is not None:
            secondary.append({"label": "Energy", "value": channel["energy"], "unit": "kWh", "metric": "energy"})
        if state.get("temperature") is not None:
            secondary.append({"label": "Temp", "value": round(state["temperature"], 1), "unit": "°C"})
        if state.get("uptime") is not None:
            secondary.append({"label": "Up", "value": duration_short(state["uptime"])})
        if state.get("update"):
            secondary.append({"label": "Firmware", "value": state["update"]})
        alarm = channel.get("alarm") or state.get("alarm") or ""
        power = channel.get("power")
        return WidgetData(
            status="bad" if alarm else "ok",
            primary={"label": "Power", "value": round(power, 1) if power is not None else "-",
                     "unit": "W" if power is not None else ""},
            secondary=secondary,
            actions=cls._actions(channel, bool(options.get("switching", True))),
            metrics=measured({"power": power, "energy": channel.get("energy")}),
            meta={"alarm": alarm, "generation": state["generation"]},
        )

    @classmethod
    def _channels(cls, state: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        switching = bool(options.get("switching", True))
        items = []
        for channel in state["channels"]:
            power = channel.get("power")
            items.append({
                "id": channel["id"],
                "title": channel["name"],
                "subtitle": channel.get("alarm") or ("on" if channel.get("on") else "off" if channel.get("on") is not None else ""),
                "value": f"{round(power, 1)} W" if power is not None else "",
                "status": "bad" if channel.get("alarm") else "ok" if channel.get("on") else "unknown",
                "actions": [action.model_dump() for action in cls._actions(channel, switching)],
            })
        total = sum(one["power"] for one in state["channels"] if one.get("power") is not None)
        return WidgetData(
            status="bad" if state.get("alarm") or any(one.get("alarm") for one in state["channels"]) else "ok",
            items=items,
            primary={"label": "Power", "value": round(total, 1), "unit": "W"},
            metrics=measured({"power": total}),
            meta={"empty": "This device has no outputs to show."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        on = fake.flicker("shelly-on", tick, 0.7)
        state = {
            "generation": 3,
            "channels": [
                {"id": 0, "name": "Output 0", "on": on, "power": round(fake.walk("shelly-w", tick, 4, 82), 1) if on else 0.0,
                 "energy": 41.882, "alarm": "", "kind": "switch"},
                {"id": 1, "name": "Output 1", "on": False, "power": 0.0, "energy": 3.114, "alarm": "", "kind": "switch"},
            ],
            "temperature": round(fake.walk("shelly-t", tick, 38, 46), 1),
            "uptime": 640_000.0,
            "rssi": -58.0,
            "update": "",
            "alarm": "",
        }
        if widget_kind == "channels":
            return self._channels(state, options)
        return self._device(state, options)


ADAPTER = ShellyAdapter()
