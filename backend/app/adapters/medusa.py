"""Medusa: the series it follows, the episodes it missed and the ones coming up.

Checked against Medusa v1.0.27 running locally on 16.11.2026, with three series added from TVmaze (two that ended,
one airing every weekday), a wrong key and no key. Medusa speaks its own ``/api/v2``, not the ``/api/v3`` of
Sonarr, so it does not inherit from ``ArrAdapter``.

- The key goes in ``X-Api-Key`` (``medusa/server/api/v2/base.py``); Medusa also reads it from ``?api_key=``, which
  this adapter never uses, so the key stays out of addresses and logs. A wrong key and no key both answer 401 with
  ``{"error": "No authorization token."}``.
- ``/stats`` counts the series (``shows.active``, ``shows.total``) and the episodes (``episodes.downloaded``,
  ``snatched``, ``total``) over the whole library.
- ``/schedule`` answers the categories asked for with ``category[]``, each a list: ``missed`` (aired, still wanted),
  ``today``, ``soon`` and ``later``. Without a category it answers ``missed`` alone, so every card names them.
  ``airdate`` is a plain ``YYYY-MM-DD``; ``paused=false`` leaves out the series on pause.

⚠️ The version comes from ``/config/system``, which also names the folders Medusa runs from. Only ``release`` is read.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

from . import demo as fake
from .base import Adapter, Context, Field, WidgetData, WidgetType, base_url

CATEGORIES = ("missed", "today", "soon", "later")


class MedusaAdapter(Adapter):
    kind = "medusa"
    label = "Medusa"
    category = "media"
    description = "The series Medusa follows, the episodes it missed and the ones coming up."
    icon = "medusa"
    docs_url = "https://github.com/pymedusa/Medusa/wiki"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://medusa:8081"),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="Settings > General > Interface > API Key."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(
            kind="status",
            label="Library",
            description="How many series are followed, the episodes downloaded and the ones it missed. Yellow when an aired episode is still wanted.",
            renderer="value",
            default_size=(2, 2),
            min_size=(1, 1),
            refresh_seconds=300,
            metrics=("missed",),
        ),
        WidgetType(
            kind="calendar",
            label="Upcoming",
            description="Episodes airing in the next days, from the series Medusa follows.",
            renderer="calendar",
            default_size=(3, 3),
            refresh_seconds=900,
            options=(Field("days", "Days ahead", type="number", default=7),),
        ),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 60) -> Any:
        return await ctx.get_json(
            f"{base_url(config)}/api/v2/{path}",
            headers={"X-Api-Key": str(config.get("api_key") or "")},
            verify=not config.get("insecure"),
            cache_seconds=cache,
        )

    async def _schedule(self, config: dict[str, Any], ctx: Context) -> dict[str, list[dict[str, Any]]]:
        # The same name four times, which a dict of parameters cannot hold.
        query = urlencode([("category[]", name) for name in CATEGORIES] + [("paused", "false")])
        answer = await self._get(config, ctx, f"schedule?{query}", cache=300)
        if not isinstance(answer, dict):
            return {name: [] for name in CATEGORIES}
        return {name: [one for one in answer.get(name) or [] if isinstance(one, dict)] for name in CATEGORIES}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        system = await self._get(config, ctx, "config/system", cache=0)
        release = system.get("release") if isinstance(system, dict) else None
        return f"Medusa {release or '?'} answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        schedule = await self._schedule(config, ctx)
        if widget_kind == "calendar":
            return self._calendar(schedule, int(options.get("days") or 7), date.today())
        stats = await self._get(config, ctx, "stats", cache=300)
        return self._library(stats if isinstance(stats, dict) else {}, len(schedule["missed"]))

    @staticmethod
    def _library(stats: dict[str, Any], missed: int) -> WidgetData:
        shows = stats.get("shows") if isinstance(stats.get("shows"), dict) else {}
        episodes = stats.get("episodes") if isinstance(stats.get("episodes"), dict) else {}
        return WidgetData(
            status="warn" if missed else "ok",
            primary={"label": "Series", "value": int(shows.get("total") or 0)},
            secondary=[
                {"label": "Continuing", "value": int(shows.get("active") or 0)},
                {"label": "Downloaded", "value": int(episodes.get("downloaded") or 0)},
                {"label": "Snatched", "value": int(episodes.get("snatched") or 0)},
                {"label": "Missed", "value": missed},
            ],
            metrics={"missed": float(missed)},
        )

    @staticmethod
    def _calendar(schedule: dict[str, list[dict[str, Any]]], days: int, today: date) -> WidgetData:
        last = (today + timedelta(days=max(1, days))).isoformat()
        items = []
        for name in ("today", "soon", "later"):
            for episode in schedule[name]:
                aired = str(episode.get("airdate") or "")[:10]
                if not aired or aired < today.isoformat() or aired > last:
                    continue
                slug = str(episode.get("episodeSlug") or "").upper()
                items.append({
                    "date": aired,
                    "title": str(episode.get("showName") or "?"),
                    "subtitle": f"{slug} {episode.get('epName') or ''}".strip(),
                    "status": "ok",
                    "source": "Medusa",
                })
        items.sort(key=lambda item: item["date"])
        return WidgetData(items=items, secondary=[{"label": "Next days", "value": len(items)}])

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "calendar":
            today = date.today()
            rows = [(0, "Harbour Lights", "S03E05 The Long Tide"), (1, "Signal Lost", "S02E01 Static"),
                    (3, "Northern Shore", "S01E07 Lanterns"), (5, "Harbour Lights", "S03E06 Low Water")]
            items = [{"date": (today + timedelta(days=offset)).isoformat(), "title": title, "subtitle": episode,
                      "status": "ok", "source": "Medusa"} for offset, title, episode in rows]
            return WidgetData(items=items, secondary=[{"label": "Next days", "value": len(items)}])
        missed = 1 if fake.flicker("medusa-missed", tick, 0.3) else 0
        downloaded = int(fake.walk("medusa-downloaded", tick, 1180, 1220))
        return self._library({"shows": {"total": 42, "active": 17}, "episodes": {"downloaded": downloaded, "snatched": 2}}, missed)


ADAPTER = MedusaAdapter()
