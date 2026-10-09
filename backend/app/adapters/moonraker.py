"""Moonraker: the Klipper printer, the job on it, and the jobs before.

Moonraker's HTTP API, read-only: ``/printer/info`` for Klippy's own state,
``/printer/objects/query`` for the job and the heaters, and
``/server/history/list`` with ``/server/history/totals`` for what was printed.
Every answer comes wrapped as ``{"result": ...}``.

⚠️ An API key goes in as ``X-Api-Key``, and only when Moonraker asks for one:
a client on a trusted network gets in without it.

⚠️ A printer object that does not exist is left out of the answer, not
reported as an error: a printer without a heated bed has no ``heater_bed``,
and the card simply has no bed temperature.

⚠️ Progress comes from ``display_status``, which follows M73 from the slicer
and falls back on the file position; the file position alone runs ahead of
the print on the first layers. The time left is estimated from the time
spent and the progress, and only once there is enough progress for the
estimate to mean anything.

⚠️ Layers are known only when the slicer sends SET_PRINT_STATS_INFO; without
it they are null and the card leaves them out.

Read from Moonraker's documentation (external_api/printer, server, history
and printer_objects on moonraker.readthedocs.io) on 2026-10-09.
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
    ago,
    base_url,
    measured,
)

#: The job state as a colour and a word. Klippy's own trouble is read separately.
JOB = {"printing": "ok", "paused": "warn", "complete": "ok", "cancelled": "unknown", "error": "bad", "standby": "ok"}
#: How a finished job ended.
ENDED = {"completed": "ok", "in_progress": "ok", "cancelled": "unknown", "error": "bad",
         "klippy_shutdown": "bad", "klippy_disconnect": "bad", "interrupted": "bad"}
QUERY = "print_stats&display_status&virtual_sdcard&extruder=temperature,target&heater_bed=temperature,target"
PRINTER_SECONDS = 10
HISTORY_SECONDS = 300
#: Below this the estimate of the time left swings too far to show.
ESTIMATE_FROM = 0.05


def _duration(seconds: Any) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    hours, rest = divmod(total, 3600)
    return f"{hours} h {rest // 60:02d} min" if hours else f"{rest // 60} min"


class MoonrakerAdapter(Adapter):
    kind = "moonraker"
    label = "Moonraker"
    category = "other"
    description = "The Klipper printer behind Moonraker: the job with its progress, layer and time left, the temperatures, and the jobs before."
    icon = "klipper"
    docs_url = "https://moonraker.readthedocs.io/en/latest/external_api/introduction/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://printer.local:7125",
              help="Moonraker itself, usually on port 7125, not Mainsail or Fluidd."),
        Field("api_key", "API key", type="password", secret=True,
              help="Only when Moonraker asks for one; a client on a trusted network needs none. Mainsail shows it under Settings > Authorization."),
    )
    widgets = (
        WidgetType(kind="printer", label="Printer", description="What the printer is doing: the job with its progress, layer and time left, and the nozzle and bed temperatures.",
                   renderer="value", default_size=(3, 2), refresh_seconds=15, metrics=("progress", "extruder", "bed")),
        WidgetType(kind="history", label="Print history", description="The latest jobs with how they ended, how long they took and the filament they used.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        key = str(config.get("api_key") or "").strip()
        response = await ctx.request("GET", f"{base_url(config)}{path}", headers={"X-Api-Key": key} if key else None,
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Moonraker wants an API key, or refused the one given.",
                             hint="Moonraker's API key, which Mainsail shows under Settings > Authorization, or add HexDeck's address to trusted_clients.")
        if response.status_code >= 400:
            message = ""
            try:
                message = str((response.json().get("error") or {}).get("message") or "")
            except (ValueError, AttributeError):
                pass
            raise AdapterError(f"Moonraker answered with HTTP {response.status_code}{': ' + message if message else ''}.", code="http_error",
                               hint="Moonraker's own address, usually port 7125, not the web interface in front of it.")
        try:
            return (response.json() or {}).get("result")
        except (ValueError, AttributeError) as failure:
            raise AdapterError("Moonraker did not answer with JSON.", code="not_json",
                               hint="Moonraker's own address, usually port 7125, not Mainsail or Fluidd.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        info = await self._get(config, ctx, "/printer/info", 0) or {}
        return f"Moonraker answers: Klipper {info.get('software_version') or '?'} on {info.get('hostname') or '?'}, {info.get('state') or '?'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "history":
            limit = max(1, int(options.get("limit") or 8))
            listed = await self._get(config, ctx, f"/server/history/list?limit={min(50, limit)}&order=desc", HISTORY_SECONDS) or {}
            totals = await self._get(config, ctx, "/server/history/totals", HISTORY_SECONDS) or {}
            return self._history(listed.get("jobs") or [], totals.get("job_totals") or {}, limit)
        info = await self._get(config, ctx, "/printer/info", PRINTER_SECONDS) or {}
        status = (await self._get(config, ctx, f"/printer/objects/query?{QUERY}", PRINTER_SECONDS) or {}).get("status") or {}
        return self._printer(info, status)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _printer(info: dict[str, Any], status: dict[str, Any]) -> WidgetData:
        klippy = str(info.get("state") or "")
        stats = status.get("print_stats") or {}
        state = str(stats.get("state") or "standby")
        progress = (status.get("display_status") or {}).get("progress")
        if not isinstance(progress, (int, float)):
            progress = (status.get("virtual_sdcard") or {}).get("progress")
        fraction = float(progress) if isinstance(progress, (int, float)) else None
        extruder = status.get("extruder") or {}
        bed = status.get("heater_bed") or {}
        secondary: list[dict[str, Any]] = []
        if stats.get("filename") and state in ("printing", "paused", "complete", "error", "cancelled"):
            secondary.append({"label": "File", "value": str(stats["filename"]).rsplit("/", 1)[-1]})
        info_ = stats.get("info") or {}
        if isinstance(info_.get("current_layer"), int) and isinstance(info_.get("total_layer"), int):
            secondary.append({"label": "Layer", "value": f"{info_['current_layer']} / {info_['total_layer']}"})
        spent = stats.get("print_duration")
        if state in ("printing", "paused") and fraction and fraction >= ESTIMATE_FROM and isinstance(spent, (int, float)):
            secondary.append({"label": "Left", "value": _duration(spent / fraction - spent)})
        for label, heater, metric in (("Nozzle", extruder, "extruder"), ("Bed", bed, "bed")):
            if isinstance(heater.get("temperature"), (int, float)):
                target = heater.get("target")
                said = f"{heater['temperature']:.0f}" + (f" / {target:.0f}" if isinstance(target, (int, float)) and target > 0 else "")
                secondary.append({"label": label, "value": said, "unit": "°C", "metric": metric})
        # Klippy itself in trouble outranks whatever the job says.
        if klippy and klippy != "ready":
            colour, headline = "bad", klippy
        else:
            colour = JOB.get(state, "unknown")
            headline = f"{fraction * 100:.0f}%" if state in ("printing", "paused") and fraction is not None else state
        return WidgetData(
            status=colour,
            primary={"label": {"printing": "Printing", "paused": "Paused"}.get(state, "Printer"), "value": headline},
            secondary=secondary,
            metrics=measured({"progress": round(fraction * 100, 1) if fraction is not None and state in ("printing", "paused") else None,
                              "extruder": extruder.get("temperature") if isinstance(extruder.get("temperature"), (int, float)) else None,
                              "bed": bed.get("temperature") if isinstance(bed.get("temperature"), (int, float)) else None}),
            meta={"status_reason": str(info.get("state_message") or "") if colour == "bad" else ""},
        )

    @staticmethod
    def _history(jobs: list[dict[str, Any]], totals: dict[str, Any], limit: int) -> WidgetData:
        rows = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            ended = str(job.get("status") or "")
            metres = job.get("filament_used")
            parts = [_duration(job.get("total_duration")),
                     f"{float(metres) / 1000:.1f} m" if isinstance(metres, (int, float)) else "",
                     ago(job.get("end_time")) if job.get("end_time") else ""]
            rows.append({
                "id": job.get("job_id"),
                "title": str(job.get("filename") or "?").rsplit("/", 1)[-1],
                "subtitle": " · ".join(part for part in parts if part),
                "value": ended.replace("_", " "),
                "status": ENDED.get(ended, "unknown"),
            })
        latest = rows[0]["status"] if rows else "ok"
        printed = totals.get("total_print_time")
        return WidgetData(
            # The newest job says how the printer stands; an old failure fixed since does not.
            status="bad" if latest == "bad" else "ok",
            items=rows[:limit],
            primary={"label": "Jobs", "value": totals.get("total_jobs", len(rows))},
            secondary=[{"label": "Printed", "value": _duration(printed)}] if isinstance(printed, (int, float)) else [],
            meta={"empty": "Nothing printed yet"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "history":
            return self._history([
                {"job_id": "000041", "filename": "gcodes/benchy_0.2mm_PLA.gcode", "status": "completed", "total_duration": 5421.0, "filament_used": 4810.0, "end_time": None},
                {"job_id": "000040", "filename": "gcodes/rack_bracket_PETG.gcode", "status": "cancelled", "total_duration": 812.0, "filament_used": 640.0, "end_time": None},
                {"job_id": "000039", "filename": "gcodes/cable_clip_x8.gcode", "status": "completed", "total_duration": 2280.0, "filament_used": 1920.0, "end_time": None},
                {"job_id": "000038", "filename": "gcodes/lamp_shade_vase.gcode", "status": "klippy_shutdown", "total_duration": 9300.0, "filament_used": 12200.0, "end_time": None},
            ], {"total_jobs": 41, "total_print_time": 412_000.0}, max(1, int(options.get("limit") or 8)))
        progress = (tick % 400) / 400
        return self._printer({"state": "ready"}, {
            "print_stats": {"state": "printing", "filename": "gcodes/rack_bracket_PETG.gcode", "print_duration": 60 + progress * 7200,
                            "info": {"current_layer": int(progress * 180), "total_layer": 180}},
            "display_status": {"progress": progress},
            "extruder": {"temperature": fake.walk("mr-ext", tick, 238, 242), "target": 240},
            "heater_bed": {"temperature": fake.walk("mr-bed", tick, 79, 81), "target": 80},
        })


ADAPTER = MoonrakerAdapter()
