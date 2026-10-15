"""GoatCounter: how many visit the site, which pages, and where they come from.

GoatCounter's API lives under ``/api/v0`` and takes a token (User, API) as
``Authorization: Bearer``. The token needs the permission "Read
statistics". ``stats/total`` gives the visitors of a stretch with one line
per day, ``stats/hits`` the pages by visitors, ``stats/toprefs`` the
referrers.

⚠️ The API allows 4 requests a second and 500 an hour for a token. The
cards ask for little and keep it ten minutes; three cards on a board stay
far below.

⚠️ A stretch defaults to the last seven days. ``daily`` of the last line of
``stats/total`` is today, in the site's time zone.

⚠️ A wrong token is answered with 401 and an HTML page, not JSON.

⚠️ A referrer without a name is direct traffic, typed or from a bookmark.

Checked against GoatCounter v2.7.0 running locally on 2026-10-15, with 40
pageviews sent through ``/api/v0/count`` to four pages from three
referrers, a token with and without "Read statistics", and a wrong one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
)

READ_SECONDS = 600
PERIODS = (("1", "Today"), ("7", "The last 7 days"), ("30", "The last 30 days"))


def _start(days: int) -> str:
    """The first day of a stretch of ``days`` days ending today, as GoatCounter reads it."""
    first = datetime.now(UTC).date() - timedelta(days=max(1, days) - 1)
    return f"{first.isoformat()}T00:00:00Z"


class GoatCounterAdapter(Adapter):
    kind = "goatcounter"
    label = "GoatCounter"
    category = "monitoring"
    description = "GoatCounter's web statistics: visitors today and this week, the pages they read most, and where they come from."
    icon = "lucide:bar-chart-3"
    docs_url = "https://github.com/arp242/goatcounter/blob/main/tpl/api.json"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://stats.example.com",
              help="The address of the site in GoatCounter, the one its dashboard opens at."),
        Field("token", "API token", type="password", secret=True, required=True,
              help="Under User, API. It needs the permission Read statistics."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="visitors", label="Visitors", description="Visitors today and in the last seven days.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("today",)),
        WidgetType(kind="pages", label="Pages", description="The pages read most, with their visitors.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("days", "Period", type="select", default="7", options=PERIODS),
                            Field("limit", "Entries", type="number", default=8))),
        WidgetType(kind="referrers", label="Referrers", description="Where visitors come from, with how many each.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("days", "Period", type="select", default="7", options=PERIODS),
                            Field("limit", "Entries", type="number", default=8))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None, cache: float = READ_SECONDS) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/api/v0{path}", params=params,
                                     headers={"Accept": "application/json", "Authorization": f"Bearer {config.get('token') or ''}"},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code == 401:
            raise AuthFailed("GoatCounter does not know the token.", hint="A token from User, API.")
        if response.status_code == 403:
            raise AuthFailed("The token may not read statistics.", hint="Tick Read statistics for the token under User, API.")
        if response.status_code == 429:
            raise AdapterError("GoatCounter asks to slow down.", code="rate_limited",
                               hint="Its API allows 500 requests an hour per token; fewer cards on this token, or a longer refresh.")
        if response.status_code >= 400:
            raise AdapterError(f"GoatCounter answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of the site in GoatCounter.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("GoatCounter did not answer with JSON.", code="not_json", hint="The address of the site in GoatCounter.") from failure
        return answer if isinstance(answer, dict) else {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        total = await self._get(config, ctx, "/stats/total", cache=0)
        return f"GoatCounter answers, {int(total.get('total') or 0)} visitors in the last seven days."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        limit = max(1, min(int(options.get("limit") or 8), 50))
        days = int(options.get("days") or 7)
        if widget_kind == "pages":
            answer = await self._get(config, ctx, "/stats/hits", {"start": _start(days), "limit": limit})
            return self._pages(answer.get("hits") or [], limit)
        if widget_kind == "referrers":
            answer = await self._get(config, ctx, "/stats/toprefs", {"start": _start(days), "limit": limit})
            return self._referrers(answer.get("stats") or [], limit)
        return self._visitors(await self._get(config, ctx, "/stats/total"))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _visitors(total: dict[str, Any]) -> WidgetData:
        days = [day for day in total.get("stats") or [] if isinstance(day, dict)]
        today = int(days[-1].get("daily") or 0) if days else 0
        yesterday = int(days[-2].get("daily") or 0) if len(days) > 1 else None
        secondary: list[dict[str, Any]] = [{"label": "7 days", "value": int(total.get("total") or 0)}]
        if yesterday is not None:
            secondary.append({"label": "Yesterday", "value": yesterday})
        return WidgetData(status="ok", primary={"label": "Today", "value": today, "metric": "today"}, secondary=secondary,
                          metrics={"today": float(today)})

    @staticmethod
    def _pages(hits: list[Any], limit: int) -> WidgetData:
        rows = [{"id": str(hit.get("path_id") or hit.get("path")), "title": str(hit.get("title") or hit.get("path") or "?"),
                 "subtitle": str(hit.get("path") or "") if hit.get("title") else ("event" if hit.get("event") else ""),
                 "value": int(hit.get("count") or 0), "status": "ok"}
                for hit in hits if isinstance(hit, dict)]
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "No visitors in this period"})

    @staticmethod
    def _referrers(stats: list[Any], limit: int) -> WidgetData:
        rows = [{"id": str(entry.get("id") or entry.get("name") or "direct"),
                 "title": str(entry.get("name") or "") or "Direct",
                 "value": int(entry.get("count") or 0), "status": "ok"}
                for entry in stats if isinstance(entry, dict)]
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "No visitors in this period"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        limit = max(1, int(options.get("limit") or 8))
        if widget_kind == "pages":
            return self._pages([{"path": "/", "title": "Home", "count": 412}, {"path": "/blog/backups-that-restore", "title": "Backups that restore", "count": 268},
                                {"path": "/blog/vlan-plan", "title": "VLAN plan", "count": 131}], limit)
        if widget_kind == "referrers":
            return self._referrers([{"name": "https://news.ycombinator.com", "count": 220}, {"name": "", "count": 140},
                                    {"name": "https://duckduckgo.com", "count": 61}], limit)
        today = 80 + fake.counter("goatcounter-today", tick, 0, 0.2)
        return self._visitors({"total": 1_240 + today, "stats": [{"daily": 171}, {"daily": today}]})


ADAPTER = GoatCounterAdapter()
