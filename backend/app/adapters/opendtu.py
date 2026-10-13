"""OpenDTU: what the balcony panels make right now, today and in total.

OpenDTU is the firmware of an ESP32 that talks to Hoymiles micro inverters
over the air. One address answers everything: ``/api/livedata/status`` hands
out the totals of all inverters and, for each, whether it is reachable and
producing; with ``?inv=<serial>`` it adds that inverter's channels (``AC``,
``DC`` per panel string, ``INV``), each value as ``{"v", "u", "d"}``.

⚠️ Reading needs no password unless "Allow read-only access" was switched
off under Security. Then it is HTTP authentication as ``admin`` with the
password of the web interface; OpenDTU accepts Basic.

⚠️ An inverter that is not reachable at night is asleep, not broken: micro
inverters switch off with the light. The card stays green for it; only a
radio that is not connected, which OpenDTU itself reports as a hint, turns
it red.

⚠️ The serial in the address is hexadecimal, exactly as the inverter list
writes it; OpenDTU reads it with base 16.

Written from OpenDTU's own source (``WebApi_ws_live.cpp`` and the
statistics parser, release v26.9.28). Not yet run against a device.
"""

from __future__ import annotations

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
    base_url,
    measured,
)

#: The totals change every few seconds; one answer serves every card on the board.
LIVE_SECONDS = 10


def _value(field: Any) -> float | None:
    """The number of one ``{"v", "u", "d"}`` field."""
    if not isinstance(field, dict):
        return None
    try:
        return float(field.get("v"))
    except (TypeError, ValueError):
        return None


def _channel(inverter: dict[str, Any], kind: str, name: str) -> float | None:
    """One value of an inverter's first channel of a kind: ``AC``, ``DC`` or ``INV``."""
    channels = inverter.get(kind)
    if not isinstance(channels, dict):
        return None
    return _value((channels.get("0") or {}).get(name))


def _watts(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value / 1000:.2f} kW" if abs(value) >= 1000 else f"{value:.0f} W"


def _energy(watt_hours: float | None) -> str:
    if watt_hours is None:
        return "–"
    return f"{watt_hours / 1000:.2f} kWh" if watt_hours >= 1000 else f"{watt_hours:.0f} Wh"


def _hint(hints: dict[str, Any]) -> tuple[str, str]:
    """The status and sentence OpenDTU's own hints call for."""
    if hints.get("radio_problem"):
        return "bad", "The radio module is not connected, so no inverter can be read."
    if hints.get("pin_mapping_issue"):
        return "bad", "OpenDTU has no pin mapping selected for this board."
    if hints.get("time_sync"):
        return "warn", "OpenDTU has no time yet, so it does not poll the inverters."
    if hints.get("default_password"):
        return "warn", "OpenDTU still uses its default password."
    return "ok", ""


class OpenDTUAdapter(Adapter):
    kind = "opendtu"
    label = "OpenDTU"
    category = "home"
    description = "Hoymiles micro inverters through OpenDTU: the power now, the yield today and in total, and each inverter."
    icon = "lucide:sun"
    docs_url = "https://www.opendtu.solar/firmware/web_api/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://opendtu.local"),
        Field("password", "Password", type="password", secret=True,
              help="Only when read-only access is switched off under Security: the password of OpenDTU's web interface."),
    )
    widgets = (
        WidgetType(kind="solar", label="Solar", description="The power the panels make now, the yield today and in total, and how many inverters are producing.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("power", "yield_day")),
        WidgetType(kind="inverters", label="Inverters", description="Each inverter with its power, its yield today, its temperature and its limit.",
                   renderer="list", default_size=(3, 3), refresh_seconds=30),
    )

    async def _live(self, config: dict[str, Any], ctx: Context, serial: str = "") -> dict[str, Any]:
        auth = ("admin", str(config["password"])) if config.get("password") else None
        response = await ctx.request("GET", f"{base_url(config)}/api/livedata/status", params={"inv": serial} if serial else None,
                                     headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                                     auth=auth, cache_seconds=LIVE_SECONDS, auth_errors=False)
        if response.status_code == 401:
            raise AuthFailed("OpenDTU refused the password." if auth else "OpenDTU asks for its password.",
                             hint="Read-only access is switched off under Security; enter the password of the web interface.")
        if response.status_code >= 400:
            raise AdapterError(f"OpenDTU answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of OpenDTU itself, as you open it in the browser.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("OpenDTU did not answer with JSON.", code="not_json",
                               hint="The address of OpenDTU itself, as you open it in the browser.") from failure
        if not isinstance(answer, dict):
            raise AdapterError("OpenDTU answered with something unexpected.", code="bad_answer")
        return answer

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        live = await self._live(config, ctx)
        count = len([one for one in live.get("inverters") or [] if isinstance(one, dict)])
        return f"OpenDTU answers with {count} inverter{'s' if count != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        live = await self._live(config, ctx)
        if widget_kind == "inverters":
            detailed = []
            for inverter in live.get("inverters") or []:
                if not isinstance(inverter, dict):
                    continue
                serial = str(inverter.get("serial") or "")
                one = (await self._live(config, ctx, serial)).get("inverters") if serial else None
                detailed.append(one[0] if isinstance(one, list) and one and isinstance(one[0], dict) else inverter)
            return self._inverters(detailed, live.get("hints") or {})
        return self._solar(live)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _solar(live: dict[str, Any]) -> WidgetData:
        total = live.get("total") or {}
        inverters = [one for one in live.get("inverters") or [] if isinstance(one, dict)]
        power, today, overall = (_value(total.get(name)) for name in ("Power", "YieldDay", "YieldTotal"))
        producing = sum(1 for one in inverters if one.get("producing"))
        status, notice = _hint(live.get("hints") or {})
        if not inverters:
            status, notice = "unknown", "OpenDTU has no inverter set up."
        elif not any(one.get("reachable") for one in inverters):
            notice = notice or "No inverter answers; at night they sleep."
        return WidgetData(
            status=status,
            primary={"label": "Power", "value": _watts(power), "metric": "power"},
            secondary=[
                {"label": "Today", "value": _energy(today), "metric": "yield_day"},
                {"label": "Total", "value": f"{overall:.1f} kWh" if overall is not None else "–"},
                {"label": "Producing", "value": f"{producing}/{len(inverters)}"},
            ],
            metrics=measured({"power": power, "yield_day": today}),
            meta={"notice": notice},
        )

    @staticmethod
    def _inverters(inverters: list[dict[str, Any]], hints: dict[str, Any]) -> WidgetData:
        rows = []
        for inverter in sorted(inverters, key=lambda one: (int(one.get("order") or 0), str(one.get("name") or ""))):
            power = _channel(inverter, "AC", "Power")
            today = _channel(inverter, "INV", "YieldDay")
            temperature = _channel(inverter, "INV", "Temperature")
            limit = inverter.get("limit_relative")
            parts = [f"today {_energy(today)}" if today is not None else "",
                     f"{temperature:.0f} °C" if temperature is not None else "",
                     f"limit {float(limit):.0f} %" if isinstance(limit, (int, float)) and 0 <= limit < 100 else ""]
            if inverter.get("producing"):
                state = "ok"
            elif inverter.get("reachable"):
                state, parts = "warn", ["not producing", *parts]
            else:
                state, parts = "unknown", ["asleep" if inverter.get("poll_enabled", True) else "not polled"]
            rows.append({
                "id": str(inverter.get("serial") or inverter.get("name") or len(rows)),
                "title": str(inverter.get("name") or inverter.get("serial") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": _watts(power) if inverter.get("reachable") else "",
                "status": state,
            })
        status, notice = _hint(hints)
        return WidgetData(status=status, items=rows, meta={"notice": notice, "empty": "No inverter set up"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        east = round(fake.walk("opendtu-east", tick, 180, 390), 1)
        west = round(fake.walk("opendtu-west", tick, 120, 310), 1)
        inverters = [
            {"serial": "116181234567", "name": "Balcony east", "order": 0, "reachable": True, "producing": True, "poll_enabled": True, "limit_relative": 100,
             "AC": {"0": {"Power": {"v": east}}}, "INV": {"0": {"YieldDay": {"v": 1240 + tick % 50}, "Temperature": {"v": 38.4}}}},
            {"serial": "116189876543", "name": "Balcony west", "order": 1, "reachable": True, "producing": True, "poll_enabled": True, "limit_relative": 80,
             "AC": {"0": {"Power": {"v": west}}}, "INV": {"0": {"YieldDay": {"v": 980 + tick % 40}, "Temperature": {"v": 35.1}}}},
        ]
        if widget_kind == "inverters":
            return self._inverters(inverters, {})
        return self._solar({"inverters": inverters, "hints": {},
                            "total": {"Power": {"v": east + west}, "YieldDay": {"v": 2220 + tick % 90}, "YieldTotal": {"v": 1834.6}}})


ADAPTER = OpenDTUAdapter()
