"""DexFrame: the e-paper Pokémon frame on an ESP32. Battery, the page on screen and the shiny
counter, with buttons to change page, refresh the screen and count encounters.

Written against the DexFrame 2.0 HTTP API (github.com/HexLions/dexframe, README "API").

⚠️ The board answers ``GET /api/info`` with no credentials. Changes (POST) need the admin
key when one is set on the board, passed as ``?key=``; without a key anyone on the network
may change it, which is the board's default.

⚠️ With power saving on, the board is offline between wakes: the card then shows it as
unreachable until the next button press. Keep power saving off for a live card.

⚠️ A refresh takes up to ~16 s on the colour panels and the board serves nothing while it
draws: ``pending`` in ``/api/info`` stays true until it is done.

⚠️ ``batteryMv`` is -1 when the board reads no battery (not wired) and below 1000 when it
runs from USB alone; the charge is an estimate from the voltage of a 1S LiPo.
"""

from __future__ import annotations

from typing import Any

from .base import (
    Action,
    Adapter,
    AdapterError,
    Context,
    Deed,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    duration_short,
)

# Resting voltage of a 1S LiPo -> charge, the same table as the DexFrame web UI.
LIPO = ((4200, 100), (4100, 90), (4000, 78), (3900, 63), (3800, 47), (3700, 30), (3600, 15), (3500, 6), (3400, 2), (3300, 0))


def lipo_percent(mv: int) -> int:
    if mv >= LIPO[0][0]:
        return 100
    for (v1, p1), (v0, p0) in zip(LIPO, LIPO[1:], strict=False):
        if mv >= v0:
            return round(p0 + (p1 - p0) * (mv - v0) / (v1 - v0))
    return 0


class DexFrameAdapter(Adapter):
    kind = "dexframe"
    label = "DexFrame"
    category = "home"
    description = "The DexFrame e-paper Pokémon frame: battery, the page on screen and the shiny counter, with buttons to change page and count encounters."
    icon = "lucide:monitor"
    #: Not yet confirmed against a board on a real network.
    beta = True
    docs_url = "https://github.com/HexLions/dexframe#api"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://dexframe.local",
              help="The address of the board, as you open its web page."),
        Field("key", "Admin key", type="password", secret=True,
              help="Only if you set one on the board (System section); every change needs it then."),
    )
    deeds = (
        Deed(widget_kind="frame", id="next", label="Next page", icon="play"),
        Deed(widget_kind="frame", id="prev", label="Previous page"),
        Deed(widget_kind="frame", id="refresh", label="Refresh the screen", icon="refresh-cw"),
        Deed(widget_kind="shiny", id="add", label="Count an encounter", icon="plus"),
    )
    widgets = (
        WidgetType(kind="frame", label="Frame", description="The page on screen, the battery and the firmware, with buttons for the previous and next page and a full refresh.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("battery_percent",)),
        WidgetType(kind="shiny", label="Shiny counter", description="The encounters on the board's shiny hunt counter, with buttons to add and take away one.",
                   renderer="value", default_size=(2, 2), refresh_seconds=30, metrics=("encounters",)),
    )

    def _params(self, config: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(extra or {})
        if config.get("key"):
            params["key"] = config["key"]
        return params

    async def _info(self, config: dict[str, Any], ctx: Context, cache: float = 10) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/api/info", cache_seconds=cache, auth_errors=False)
        if response.status_code >= 400:
            raise AdapterError(f"The board answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is the address of the board's web page.")
        try:
            info = response.json()
        except ValueError as error:
            raise AdapterError("The board did not answer with JSON.", code="not_json",
                               hint="The URL probably points at something else than a DexFrame.") from error
        if not isinstance(info, dict) or "entries" not in info or "w" not in info:
            raise AdapterError("This address answers, but not the way a DexFrame does.", code="not_dexframe")
        return info

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        info = await self._info(config, ctx, cache=0)
        return f"DexFrame {info.get('version') or ''} answers; page {int(info.get('current') or 0) + 1} of {int(info.get('entries') or 0)} on screen.".replace("  ", " ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        info = await self._info(config, ctx)
        return self._shiny(info) if widget_kind == "shiny" else self._frame(info)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any],
                     config: dict[str, Any], options: dict[str, Any], ctx: Context) -> str:
        paths = {"next": ("/api/next", {}), "prev": ("/api/prev", {}), "refresh": ("/api/refresh", {}),
                 "add": ("/api/counter", {"add": 1}), "remove": ("/api/counter", {"add": -1})}
        if action_id not in paths:
            raise AdapterError("Unknown DexFrame action.", code="no_such_action")
        path, extra = paths[action_id]
        response = await ctx.request("POST", f"{base_url(config)}{path}", params=self._params(config, extra), auth_errors=False)
        if response.status_code in (401, 403):
            raise AdapterError("The board wants its admin key.", code="auth_failed",
                               hint="Put the key set in the board's System section in the integration.")
        if response.status_code >= 400:
            raise AdapterError(f"The board refused: HTTP {response.status_code}.", code="action_failed")
        ctx.forget_answers()
        return {"next": "Next page on its way.", "prev": "Previous page on its way.", "refresh": "The screen is refreshing.",
                "add": "Encounter counted.", "remove": "One encounter taken away."}[action_id]

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _frame(info: dict[str, Any]) -> WidgetData:
        entries, current = int(info.get("entries") or 0), int(info.get("current") or 0)
        mv = int(info.get("batteryMv") if info.get("batteryMv") is not None else -1)
        low = int(info.get("batLowMv") or 3500)
        secondary: list[dict[str, Any]] = []
        metrics: dict[str, float] = {}
        status = "ok"
        if mv >= 1000:
            charge = lipo_percent(mv)
            secondary.append({"label": "Battery", "value": charge, "unit": "%"})
            metrics["battery_percent"] = float(charge)
            if mv <= low:
                status = "warn"
        elif mv >= 0:
            secondary.append({"label": "Power", "value": "USB"})
        if info.get("push"):
            secondary.append({"label": "Showing", "value": "Pushed page"})
        if info.get("version"):
            secondary.append({"label": "Firmware", "value": str(info["version"])})
        if info.get("uptime"):
            secondary.append({"label": "Up", "value": duration_short(float(info["uptime"]))})
        actions = [Action(id="prev", label="Previous"), Action(id="next", label="Next", icon="play"),
                   Action(id="refresh", label="Refresh", icon="refresh-cw")]
        if info.get("pending"):
            status = "warn" if status == "ok" else status
        return WidgetData(
            status=status,
            primary={"label": "Drawing" if info.get("pending") else "Page", "value": f"{current + 1} / {entries}" if entries else "-"},
            secondary=secondary, actions=actions, metrics=metrics,
        )

    @staticmethod
    def _shiny(info: dict[str, Any]) -> WidgetData:
        features = str(info.get("features") or "").split(",")
        if "counter" not in features:
            raise AdapterError("This board has no shiny counter.", code="no_counter",
                               hint="Update the board to DexFrame 2.0 or later.")
        count = int(info.get("counter") or 0)
        return WidgetData(
            status="ok",
            primary={"label": "Encounters", "value": count},
            actions=[Action(id="remove", label="Minus one", icon="minus"), Action(id="add", label="Plus one", icon="plus")],
            metrics={"encounters": float(count)},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        from . import demo as fake

        info = {"w": 400, "h": 300, "version": "2.0.0", "entries": 12, "current": tick % 12,
                "batteryMv": int(fake.walk("dexframe-battery", tick, 3720, 4080)), "batLowMv": 3500,
                "uptime": 3600 + tick * 30, "pending": fake.flicker("dexframe-pending", tick, 0.1),
                "features": "counter", "counter": fake.counter("dexframe-counter", tick, 1200, 0.5)}
        return self._shiny(info) if widget_kind == "shiny" else self._frame(info)


ADAPTER = DexFrameAdapter()
