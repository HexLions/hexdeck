"""INGV: the earthquakes in and around Italy, the latest first, near a place or all.

Checked against the FDSN event service of INGV (webservices.ingv.it, service
version fdsnws-event v1.105.0) on 03.11.2026, with a radius around Milan, a
magnitude no quake reached and a magnitude that is not a number. The HACS
integration "INGV Earthquakes" (caiosweet, MIT) reads the same service as
QuakeML; this card asks for GeoJSON, which carries the same fields.

⚠️ ``time`` comes without a time zone (``2026-11-02T00:51:05.410000``) and is
UTC; read as local time it would be an hour or two off.

⚠️ No earthquake matching the question is 204 with an empty body, not an
empty list; a parameter it does not understand is 400 with a page of text.

⚠️ ``geometry.coordinates`` is longitude, latitude, depth in km, in that
order. ``place`` is INGV's own wording, Italian place names with the province
("4 km SE Morfasso (PC)"), and stays as it is.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from . import demo as fake
from .base import Adapter, AdapterError, Context, Field, WidgetData, WidgetType, ago

API = "https://webservices.ingv.it/fdsnws/event/1/query"
#: A quake this strong is amber on the card, and from the second one red.
WARN_MAGNITUDE = 4.0
BAD_MAGNITUDE = 5.0


def _when(value: Any) -> float | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Along the surface, haversine on a sphere of 6371 km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


OPTIONS = (
    Field("latitude", "Latitude", type="number", placeholder="45.46",
          help="With the longitude, only quakes within the radius count. Empty: all of Italy and its seas."),
    Field("longitude", "Longitude", type="number", placeholder="9.19"),
    Field("radius_km", "Radius (km)", type="number", default=200),
    Field("min_magnitude", "From magnitude", type="number", default=2.5),
    Field("days", "Days back", type="number", default=7),
)


class IngvAdapter(Adapter):
    kind = "ingv"
    label = "INGV earthquakes"
    category = "feeds"
    description = "The earthquakes INGV has recorded in and around Italy, the latest first, near a place or all of them."
    icon = "lucide:activity"
    beta = True
    docs_url = "https://webservices.ingv.it/swagger-ui/dist/?url=https://ingv.github.io/openapi/fdsnws/event/0.0.1/event.yaml"
    needs_integration = False
    widgets = (
        WidgetType(kind="latest", label="Earthquakes", description="The latest earthquakes with their magnitude, depth, distance and when.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(*OPTIONS, Field("limit", "Entries", type="number", default=8))),
        WidgetType(kind="summary", label="Earthquakes in short", description="How many earthquakes, and the strongest, in the days chosen.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("count",), options=OPTIONS),
    )

    def default_link(self, config: dict[str, Any]) -> str:
        return "https://terremoti.ingv.it/"

    async def _quakes(self, options: dict[str, Any], ctx: Context, cache: float = 300) -> list[dict[str, Any]]:
        days = max(1, min(90, int(_number(options.get("days")) or 7)))
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        params: dict[str, Any] = {"starttime": since, "minmag": _number(options.get("min_magnitude")) or 2.5,
                                  "format": "geojson", "orderby": "time", "limit": 200}
        lat, lon = _number(options.get("latitude")), _number(options.get("longitude"))
        if lat is not None and lon is not None:
            params.update(lat=lat, lon=lon, maxradiuskm=max(1, min(2000, int(_number(options.get("radius_km")) or 200))))
        response = await ctx.request("GET", API, params=params, cache_seconds=cache, auth_errors=False)
        if response.status_code == 204:
            return []
        if response.status_code == 400:
            raise AdapterError("INGV did not understand the question.", code="bad_request",
                               hint="Check the latitude, longitude, radius and magnitude: numbers, with a dot.")
        if response.status_code >= 400:
            raise AdapterError(f"INGV answered with HTTP {response.status_code}.", code="http_error",
                               hint="Its service is sometimes away for a while; the card asks again by itself.")
        try:
            features = (response.json() or {}).get("features")
        except ValueError as error:
            raise AdapterError("INGV answered with something other than GeoJSON.", code="bad_answer") from error
        quakes = []
        for feature in features or []:
            properties = feature.get("properties") or {}
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coordinates) < 3:
                continue
            away = _distance_km(lat, lon, float(coordinates[1]), float(coordinates[0])) if lat is not None and lon is not None else None
            quakes.append({"magnitude": _number(properties.get("mag")), "kind": str(properties.get("magType") or ""),
                           "place": str(properties.get("place") or "?"), "when": _when(properties.get("time")),
                           "depth": _number(coordinates[2]), "away": away})
        return quakes

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        quakes = await self._quakes({"days": 7, "min_magnitude": 2.5}, ctx, cache=0)
        return f"INGV answers with {len(quakes)} earthquake{'' if len(quakes) == 1 else 's'} of magnitude 2.5 or more in the last week."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        quakes = await self._quakes(options, ctx)
        if widget_kind == "summary":
            return self._summary(quakes)
        return self._list(quakes, max(1, min(30, int(_number(options.get("limit")) or 8))))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _state(magnitude: float | None) -> str:
        if magnitude is None:
            return "unknown"
        return "bad" if magnitude >= BAD_MAGNITUDE else "warn" if magnitude >= WARN_MAGNITUDE else "ok"

    @classmethod
    def _list(cls, quakes: list[dict[str, Any]], limit: int) -> WidgetData:
        items = []
        for quake in quakes[:limit]:
            when = ago(quake["when"])
            parts = [f"{quake['depth']:.0f} km deep" if quake["depth"] is not None else "",
                     f"{quake['away']:.0f} km away" if quake["away"] is not None else "",
                     f"{when} ago" if when else ""]
            items.append({"title": quake["place"], "subtitle": " · ".join(part for part in parts if part),
                          "status": cls._state(quake["magnitude"]),
                          "value": f"M{quake['magnitude']:.1f}" if quake["magnitude"] is not None else "?"})
        worst = max((quake["magnitude"] or 0 for quake in quakes[:limit]), default=0)
        return WidgetData(status=cls._state(worst) if items else "ok", items=items,
                          meta={"empty": "No earthquake of that size in the days chosen."})

    @classmethod
    def _summary(cls, quakes: list[dict[str, Any]]) -> WidgetData:
        strongest = max(quakes, key=lambda quake: quake["magnitude"] or 0, default=None)
        secondary: list[dict[str, Any]] = []
        if strongest is not None and strongest["magnitude"] is not None:
            secondary.append({"label": "Strongest", "value": f"M{strongest['magnitude']:.1f}"})
            secondary.append({"label": "Where", "value": strongest["place"]})
        if quakes and quakes[0]["when"]:
            secondary.append({"label": "Latest", "value": f"{ago(quakes[0]['when'])} ago"})
        return WidgetData(status=cls._state(strongest["magnitude"]) if strongest else "ok",
                          primary={"label": "Earthquakes", "value": len(quakes)}, secondary=secondary,
                          metrics={"count": float(len(quakes))})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = time.time()
        quakes = [
            {"magnitude": 2.7, "kind": "ML", "place": "2 km W Tarzo (TV)", "when": now - fake.walk("ingv-1", tick, 600, 4000), "depth": 14.4, "away": 238.0},
            {"magnitude": 3.1, "kind": "ML", "place": "5 km NE Norcia (PG)", "when": now - 9 * 3600, "depth": 9.0, "away": None},
            {"magnitude": 2.3, "kind": "ML", "place": "4 km SE Morfasso (PC)", "when": now - 26 * 3600, "depth": 25.1, "away": 72.0},
            {"magnitude": 4.2, "kind": "Mw", "place": "Costa Calabra sud-orientale (Reggio di Calabria)", "when": now - 3 * 86400, "depth": 31.0, "away": None},
        ]
        if widget_kind == "summary":
            return self._summary(quakes)
        return self._list(quakes, max(1, min(30, int(_number(options.get("limit")) or 8))))


ADAPTER = IngvAdapter()
