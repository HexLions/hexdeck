"""Protezione Civile: today's and tomorrow's alert for a municipality, from the
national criticality bulletin.

Checked against the bulletin of 03.11.2026 at 14:30 (the page of the
Department of Civil Protection and its GeoJSON on GitHub), for Milano and
for a municipality of a zone on yellow alert. Written after the HACS
integration "Dipartimento Protezione Civile" (caiosweet, MIT), which reads
the same files; no code taken.

⚠️ There is no API. The page ``bollettino-di-criticita`` names the latest
bulletin as ``YYYYMMDD_HHMM`` somewhere in its HTML (about a megabyte), and
the bulletin itself is two GeoJSON files in the Department's repository
``pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica``:
``files/geojson/<id>_today.json`` and ``<id>_tomorrow.json``, five and a half
megabytes each, one feature per alert zone with its polygon. The page is
read once an hour and the files only when the id changes.

⚠️ A bulletin is issued in the afternoon. Before the day's one, the latest is
yesterday's, and its ``tomorrow`` is today; the card then knows today only.

⚠️ Each zone lists its municipalities under ``Comuni``, in Italian, and a
municipality can lie in two zones; the card takes the worse. The polygons are
not read. Each of the three risks (``Per rischio idraulico``,
``idrogeologico``, ``temporali``) reads "<words> / NESSUNA ALLERTA", "ALLERTA
GIALLA", "ALLERTA ARANCIONE" or "ALLERTA ROSSA".
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

from .base import Adapter, AdapterError, Context, Field, WidgetData, WidgetType

PAGE = "https://mappe.protezionecivile.gov.it/it/mappe-rischi/bollettino-di-criticita/"
FILES = "https://raw.githubusercontent.com/pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica/master/files/geojson/{}_{}.json"
_ID = re.compile(r"(\d{8})_(\d{4})")
RISKS = (("Per rischio idraulico", "Flooding"), ("Per rischio idrogeologico", "Landslides"), ("Per rischio temporali", "Storms"))
#: The four levels, worst last, with the card's word and colour.
LEVELS = (("NESSUNA", "No alert", "ok"), ("GIALLA", "Yellow", "warn"), ("ARANCIONE", "Orange", "bad"), ("ROSSA", "Red", "bad"))


def _plain(name: str) -> str:
    """A name compared without case, accents or apostrophes: "Sant'Omero" is "santomero"."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _level(text: Any) -> int:
    upper = str(text or "").upper()
    for index in range(len(LEVELS) - 1, -1, -1):
        if LEVELS[index][0] in upper:
            return index
    return 0


class DpcAdapter(Adapter):
    kind = "dpc"
    label = "Protezione Civile"
    category = "feeds"
    description = "The alert of the Italian Civil Protection for a municipality, today and tomorrow: floods, landslides and storms."
    icon = "lucide:shield"
    beta = True
    docs_url = "https://github.com/pcm-dpc/DPC-Bollettini-Criticita-Idrogeologica-Idraulica"
    needs_integration = False
    widgets = (
        WidgetType(kind="alert", label="Alert", description="The worst alert for the municipality today, with its zone and tomorrow's.",
                   renderer="value", default_size=(3, 2), refresh_seconds=1800,
                   options=(Field("municipality", "Municipality", required=True, placeholder="Milano",
                                  help="As the Civil Protection writes it, in Italian: Milano, Reggio di Calabria, Sant'Omero."),)),
        WidgetType(kind="risks", label="Alert by risk", description="Floods, landslides and storms, today and tomorrow, each with its level.",
                   renderer="list", default_size=(3, 3), refresh_seconds=1800,
                   options=(Field("municipality", "Municipality", required=True, placeholder="Milano"),)),
    )

    def default_link(self, config: dict[str, Any]) -> str:
        return PAGE

    async def _bulletin_id(self, ctx: Context, cache: float = 3600) -> str:
        response = await ctx.request("GET", PAGE, cache_seconds=cache, auth_errors=False)
        if response.status_code >= 400:
            raise AdapterError(f"The Civil Protection's page answered with HTTP {response.status_code}.", code="http_error",
                               hint="The page is sometimes away for a while; the card asks again by itself.")
        found = sorted(set(_ID.findall(response.text)))
        if not found:
            raise AdapterError("The Civil Protection's page names no bulletin.", code="no_bulletin",
                               hint="The page may have changed; the bulletins are on GitHub in the meantime.")
        return "_".join(found[-1])

    async def _day(self, ctx: Context, bulletin: str, which: str) -> list[dict[str, Any]]:
        response = await ctx.request("GET", FILES.format(bulletin, which), cache_seconds=6 * 3600, auth_errors=False)
        if response.status_code >= 400:
            raise AdapterError(f"The bulletin {bulletin} is not on GitHub ({response.status_code}).", code="http_error")
        try:
            features = (response.json() or {}).get("features") or []
        except ValueError as error:
            raise AdapterError("The bulletin is not GeoJSON.", code="bad_answer") from error
        return [feature.get("properties") or {} for feature in features]

    @staticmethod
    def _for(zones: list[dict[str, Any]], municipality: str) -> dict[str, Any] | None:
        """The worst of the zones the municipality lies in: per risk, and the name of the worst zone."""
        wanted = _plain(municipality)
        mine = [zone for zone in zones if any(_plain(str(name)) == wanted for name in zone.get("Comuni") or [])]
        if not mine:
            return None
        risks = {key: max(_level(zone.get(key)) for zone in mine) for key, _word in RISKS}
        worst = max(mine, key=lambda zone: max(_level(zone.get(key)) for key, _word in RISKS))
        return {"risks": risks, "zone": str(worst.get("Nome zona") or ""), "level": max(risks.values())}

    async def _read(self, options: dict[str, Any], ctx: Context, cache: float = 3600) -> dict[str, Any]:
        municipality = str(options.get("municipality") or "").strip()
        if not municipality:
            raise AdapterError("No municipality is set.", code="missing_option", hint="Fill it in the card's settings.")
        bulletin = await self._bulletin_id(ctx, cache)
        issued = datetime.strptime(bulletin, "%Y%m%d_%H%M").date()
        days: dict[str, dict[str, Any] | None] = {}
        if issued >= date.today():
            days["Today"] = self._for(await self._day(ctx, bulletin, "today"), municipality)
            days["Tomorrow"] = self._for(await self._day(ctx, bulletin, "tomorrow"), municipality)
        else:
            # Yesterday's bulletin: its tomorrow is today, and tomorrow is not out yet.
            days["Today"] = self._for(await self._day(ctx, bulletin, "tomorrow"), municipality)
        if all(day is None for day in days.values()):
            raise AdapterError(f"No alert zone lists {municipality}.", code="unknown_municipality",
                               hint="Write it as the Civil Protection does, in Italian: Milano, Reggio di Calabria.")
        return {"bulletin": bulletin, "days": days}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        bulletin = await self._bulletin_id(ctx, cache=0)
        when = datetime.strptime(bulletin, "%Y%m%d_%H%M")
        return f"The Civil Protection's latest bulletin is of {when:%d.%m.%Y} at {when:%H:%M}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        found = await self._read(options, ctx)
        return self._risks(found) if widget_kind == "risks" else self._alert(found)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _alert(found: dict[str, Any]) -> WidgetData:
        today = found["days"].get("Today")
        tomorrow = found["days"].get("Tomorrow")
        level = today["level"] if today else 0
        _key, word, state = LEVELS[level]
        secondary: list[dict[str, Any]] = []
        if today and today["zone"]:
            secondary.append({"label": "Zone", "value": today["zone"]})
        if tomorrow:
            secondary.append({"label": "Tomorrow", "value": LEVELS[tomorrow["level"]][1]})
        return WidgetData(status=state, primary={"label": "Today", "value": word}, secondary=secondary,
                          meta={"status_reason": f"{word} alert today"} if level else {})

    @staticmethod
    def _risks(found: dict[str, Any]) -> WidgetData:
        items = []
        for day, data in found["days"].items():
            if data is None:
                continue
            for key, word in RISKS:
                level = data["risks"][key]
                items.append({"title": word, "subtitle": day, "status": LEVELS[level][2], "value": LEVELS[level][1]})
        worst = max((LEVELS[data["level"]][2] for data in found["days"].values() if data), key=("ok", "warn", "bad").index, default="ok")
        return WidgetData(status=worst, items=items, meta={"empty": "The municipality is in no alert zone."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        yellow = tick % 6 >= 3
        found = {"bulletin": "20261103_1430", "days": {
            "Today": {"zone": "Nodo idraulico di Milano", "level": 1 if yellow else 0,
                      "risks": {"Per rischio idraulico": 0, "Per rischio idrogeologico": 0, "Per rischio temporali": 1 if yellow else 0}},
            "Tomorrow": {"zone": "Nodo idraulico di Milano", "level": 2,
                         "risks": {"Per rischio idraulico": 2, "Per rischio idrogeologico": 1, "Per rischio temporali": 1}},
        }}
        return self._risks(found) if widget_kind == "risks" else self._alert(found)


ADAPTER = DpcAdapter()
