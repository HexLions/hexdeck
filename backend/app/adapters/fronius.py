"""Fronius: what the roof makes, what the house takes, the grid and the battery.

Every Fronius inverter with a Datamanager or a Gen24 answers the Solar API v1
on the local network, without a password: ``GetPowerFlowRealtimeData.fcgi``
gives the whole site at once, ``GetInverterInfo.cgi`` each inverter with its
state.

⚠️ The signs are Fronius's: ``P_Grid`` is positive while the house takes
from the grid, ``P_Akku`` positive while the battery gives, and ``P_Load``
negative for what the house uses. The flow card wants the house positive.

⚠️ A Gen24 answers ``null`` for the energy of the day and the year; only
the total is there. The card leaves out what is null instead of showing 0.

⚠️ A Symo writes its inverter name as HTML entities (``&#83;&#121;...``).

⚠️ ``StatusCode`` 7 is running; 0 to 6 are starting up, 8 standby, 9 boot
loading, 10 error. A Gen24 also says it in words, in ``InverterState``.

Written from the answers of a Symo, a Primo and a Gen24 with a battery as
pyfronius and Home Assistant record them in their tests; the fixture here
is written anew in that shape. Not yet run against an inverter.
"""

from __future__ import annotations

import html
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    measured,
)

READ_SECONDS = 10
STATES = {7: "running", 8: "standby", 9: "boot loading", 10: "error"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kw(watts: float | None) -> float | None:
    return None if watts is None else round(watts / 1000.0, 2)


def _energy(watt_hours: float | None) -> str:
    if watt_hours is None:
        return "–"
    if watt_hours >= 1_000_000:
        return f"{watt_hours / 1_000_000:.2f} MWh"
    return f"{watt_hours / 1000:.1f} kWh"


def site_of(flow: dict[str, Any]) -> dict[str, Any]:
    """The site in the card's signs and units: kilowatts, the house positive."""
    site = flow.get("Site") or {}
    inverters = [one for one in (flow.get("Inverters") or {}).values() if isinstance(one, dict)]
    socs = [value for value in (_number(one.get("SOC")) for one in inverters) if value is not None]
    load = _number(site.get("P_Load"))
    return {
        "solar": _kw(_number(site.get("P_PV")) or 0.0),
        "home": _kw(-load) if load is not None else None,
        "grid": _kw(_number(site.get("P_Grid"))),
        "battery": _kw(_number(site.get("P_Akku"))) if site.get("P_Akku") is not None or socs else None,
        "battery_soc": round(sum(socs) / len(socs), 1) if socs else None,
        "day": _number(site.get("E_Day")),
        "total": _number(site.get("E_Total")),
        "autonomy": _number(site.get("rel_Autonomy")),
    }


class FroniusAdapter(Adapter):
    kind = "fronius"
    label = "Fronius"
    category = "home"
    description = "A Fronius inverter over its local Solar API: the solar power, the house, the grid and the battery, and each inverter's state."
    icon = "fronius"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://192.168.1.80",
              help="The inverter or its Datamanager on your own network. The Solar API needs no password; on a Gen24 it has to be switched on under Communication."),
    )
    widgets = (
        WidgetType(kind="power", label="Solar", description="The solar power now, the house, the grid, the battery and the energy of the day.",
                   renderer="value", default_size=(3, 2), refresh_seconds=15, metrics=("pv", "home", "grid")),
        WidgetType(kind="flow", label="Energy flow", description="Solar, house, battery and grid as one picture, with the power moving between them.",
                   renderer="flow", default_size=(4, 3), refresh_seconds=15, metrics=("home", "pv", "grid")),
        WidgetType(kind="inverters", label="Inverters", description="Each inverter with its state, its power and an error code when it has one.",
                   renderer="list", default_size=(3, 2), refresh_seconds=60),
    )

    async def _api(self, config: dict[str, Any], ctx: Context, call: str, cache: float = READ_SECONDS) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/solar_api/v1/{call}", cache_seconds=cache)
        if response.status_code >= 400:
            raise AdapterError(f"The inverter answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of the inverter or its Datamanager; on a Gen24 the Solar API has to be switched on.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("The inverter did not answer with JSON.", code="not_json",
                               hint="The address of the inverter or its Datamanager.") from failure
        status = ((answer.get("Head") or {}).get("Status") or {}) if isinstance(answer, dict) else {}
        if status.get("Code") not in (0, None):
            raise AdapterError(f"The inverter answered: {status.get('Reason') or status.get('UserMessage') or 'an error'}.", code="api_error")
        return ((answer.get("Body") or {}).get("Data") or {}) if isinstance(answer, dict) else {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        inverters = await self._api(config, ctx, "GetInverterInfo.cgi", cache=0)
        count = len([one for one in inverters.values() if isinstance(one, dict)])
        return f"The Solar API answers with {count} inverter{'s' if count != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "inverters":
            info = await self._api(config, ctx, "GetInverterInfo.cgi", cache=60)
            flow = await self._api(config, ctx, "GetPowerFlowRealtimeData.fcgi")
            return self._inverters(info, flow)
        site = site_of(await self._api(config, ctx, "GetPowerFlowRealtimeData.fcgi"))
        return self._flow(site) if widget_kind == "flow" else self._power(site)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _power(site: dict[str, Any]) -> WidgetData:
        secondary: list[dict[str, Any]] = []
        if site["home"] is not None:
            secondary.append({"label": "House", "value": site["home"], "unit": "kW", "metric": "home"})
        if site["grid"] is not None:
            secondary.append({"label": "From grid" if site["grid"] >= 0 else "To grid", "value": abs(site["grid"]), "unit": "kW", "metric": "grid"})
        if site["battery_soc"] is not None:
            secondary.append({"label": "Battery", "value": site["battery_soc"], "unit": "%"})
        if site["day"] is not None:
            secondary.append({"label": "Today", "value": _energy(site["day"])})
        elif site["total"] is not None:
            secondary.append({"label": "Total", "value": _energy(site["total"])})
        return WidgetData(
            status="ok",
            primary={"label": "Solar", "value": site["solar"], "unit": "kW", "metric": "pv"},
            secondary=secondary,
            metrics=measured({"pv": site["solar"], "home": site["home"], "grid": site["grid"]}),
            meta={"autonomy": site["autonomy"]},
        )

    @staticmethod
    def _flow(site: dict[str, Any]) -> WidgetData:
        flow = {key: site[key] for key in ("solar", "home", "grid", "battery", "battery_soc")}
        flow["home"] = flow["home"] or 0.0
        flow["grid"] = flow["grid"] or 0.0
        flow["car"] = None
        return WidgetData(
            primary={"label": "House", "value": flow["home"], "unit": "kW"},
            metrics=measured({"home": flow["home"], "pv": flow["solar"], "grid": flow["grid"]}),
            meta={"flow": flow},
        )

    @staticmethod
    def _inverters(info: dict[str, Any], flow: dict[str, Any]) -> WidgetData:
        live = flow.get("Inverters") or {}
        rows = []
        for number, inverter in sorted(info.items(), key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 0):
            if not isinstance(inverter, dict):
                continue
            code = inverter.get("StatusCode")
            state = str(inverter.get("InverterState") or (STATES.get(code, "starting up" if isinstance(code, int) and code < 7 else str(code or "")))).lower()
            error = int(_number(inverter.get("ErrorCode")) or 0)
            power = _number((live.get(str(number)) or {}).get("P"))
            rows.append({
                "id": str(number),
                "title": html.unescape(str(inverter.get("CustomName") or f"Inverter {number}")),
                "subtitle": " · ".join(part for part in (state, f"code {error}" if error else "") if part),
                "value": f"{power:.0f} W" if power is not None else "",
                "status": "bad" if error or state == "error" else "ok" if state == "running" else "unknown",
            })
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "ok", items=rows,
                          meta={"empty": "No inverter"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        pv = fake.walk("fronius-pv", tick, 2100, 5400)
        load = fake.walk("fronius-load", tick, 600, 2600)
        battery = fake.walk("fronius-akku", tick, -900, 400)
        flow = {"Site": {"P_PV": pv, "P_Load": -load, "P_Akku": battery, "P_Grid": load - pv - battery, "E_Day": 14_200 + tick * 4,
                         "E_Total": 18_402_300, "rel_Autonomy": 100.0},
                "Inverters": {"1": {"DT": 1, "P": pv * 0.97, "SOC": round(fake.walk("fronius-soc", tick, 40, 90), 1)}}}
        if widget_kind == "inverters":
            return self._inverters({"1": {"CustomName": "Symo GEN24 10.0 Plus", "StatusCode": 7, "ErrorCode": 0, "InverterState": "Running"}}, flow)
        site = site_of(flow)
        return self._flow(site) if widget_kind == "flow" else self._power(site)


ADAPTER = FroniusAdapter()
