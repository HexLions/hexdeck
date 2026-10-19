"""Umami: who is on the sites now, and visitors and views over the last day.

Written from Umami's source at release v3.4.0 (``src/app/api/websites``,
``src/queries/sql/getActiveVisitors.ts`` and ``getWebsiteStats.ts``,
``src/lib/auth.ts``, ``src/lib/api-key.ts``, ``src/lib/response.ts``). Not
yet run against an installation: its build needed more memory than the
machine it was written on had.

⚠️ Two ways in. Since v3 a self-hosted Umami takes API keys (``umami_`` and
32 characters, made under Settings > API keys) as a bearer token; an older
one, or a key that is not wanted, signs in with ``POST /api/auth/login``,
whose ``token`` is the bearer from then on. A token from before a password
change is refused, so a 401 signs in once more before giving up.

⚠️ A 401 is also the answer for a site the account may not see:
``canViewWebsiteSection`` failing answers ``unauthorized()`` like a bad key.

⚠️ ``/stats`` wants a period, ``startAt`` and ``endAt`` in milliseconds, and
answers ``pageviews``, ``visitors``, ``visits``, ``bounces`` and
``totaltime`` with a ``comparison`` block for the period before. On
PostgreSQL the numbers are bigints made into JSON numbers by a ``toJSON``
patch; on ClickHouse they can come as strings, so both are read.

⚠️ ``/active`` counts distinct sessions of the last five minutes, as
``{"visitors": n}``.

⚠️ ``GET /api/websites`` is paged, ``{"data": [...], "count", "page",
"pageSize"}``, twenty to a page unless ``pageSize`` says otherwise.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

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

TOKEN_KEY = "umami:token"
#: Sites read for the list card; each one costs two requests.
MAX_SITES = 10
DAY_MS = 86_400_000


def _count(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _bounce_rate(stats: dict[str, Any]) -> int | None:
    visits = _count(stats.get("visits"))
    return round(100 * _count(stats.get("bounces")) / visits) if visits else None


class UmamiAdapter(Adapter):
    kind = "umami"
    label = "Umami"
    category = "monitoring"
    description = "Umami web analytics: visitors on each site right now, and visitors and views over the last 24 hours."
    icon = "umami"
    beta = True
    docs_url = "https://github.com/umami-software/umami"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://umami:3000"),
        Field("api_key", "API key", secret=True,
              help="An Umami 3 API key (umami_...), from Settings > API keys. Or leave it empty and sign in below."),
        Field("username", "User name", help="Only without an API key."),
        Field("password", "Password", type="password", secret=True, help="Only without an API key."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="site", label="Site", description="Visitors right now, and visitors, views and bounce rate over the last 24 hours.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("active", "visitors"),
                   options=(Field("website", "Site", placeholder="example.com", help="Its name or domain. Empty means the first site."),)),
        WidgetType(kind="sites", label="Sites", description="Every site with its visitors right now and over the last 24 hours.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120,
                   options=(Field("limit", "Entries", type="number", default=5),)),
    )

    # -- signing in ----------------------------------------------------------

    async def _bearer(self, config: dict[str, Any], ctx: Context, fresh: bool = False) -> str:
        key = str(config.get("api_key") or "").strip()
        if key:
            return key
        token = ctx.cache.get(TOKEN_KEY)
        if token and not fresh:
            return str(token)
        if not config.get("username") or not config.get("password"):
            raise AuthFailed("Umami needs an API key, or a user name and password.",
                             hint="Umami 3 makes API keys under Settings > API keys.")
        response = await ctx.request("POST", f"{base_url(config)}/api/auth/login",
                                     json_body={"username": config.get("username"), "password": config.get("password")},
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.status_code in (400, 401, 403):
            raise AuthFailed("Umami refused the user name or password.")
        if response.status_code >= 400:
            raise AdapterError(f"Umami answered the sign-in with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way Umami does.", code="not_umami") from error
        if answer.get("requiresTwoFactor"):
            raise AuthFailed("This account has two-factor sign-in, which a dashboard cannot answer.",
                             hint="Use an API key instead.")
        token = str(answer.get("token") or "")
        if not token:
            raise AdapterError("Umami signed in without handing over a token.", code="not_umami")
        ctx.cache[TOKEN_KEY] = token
        return token

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None,
                   cache: float = 30, retry: bool = True) -> Any:
        bearer = await self._bearer(config, ctx)
        response = await ctx.request("GET", f"{base_url(config)}{path}", params=params, headers={"Authorization": f"Bearer {bearer}"},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code == 401 and retry and not config.get("api_key"):
            # A token from before a password change: sign in again, once.
            await self._bearer(config, ctx, fresh=True)
            return await self._get(config, ctx, path, params, cache, retry=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Umami refused the API key or the sign-in.",
                             hint="Umami says the same when the account may not see the site.")
        if response.status_code >= 400:
            raise AdapterError(f"Umami answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Umami's own address, without /api.")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way Umami does.", code="not_umami") from error

    async def _sites(self, config: dict[str, Any], ctx: Context, cache: float = 300) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/api/websites", {"pageSize": 100}, cache=cache)
        if not isinstance(answer, dict) or not isinstance(answer.get("data"), list):
            raise AdapterError("This address answers, but not the way Umami does.", code="not_umami")
        return [one for one in answer["data"] if isinstance(one, dict) and one.get("id")]

    async def _numbers(self, config: dict[str, Any], ctx: Context, site: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        site_id = quote(str(site["id"]), safe="")
        # To the minute, so two cards in the same minute share one answer.
        end = int(time.time() // 60 * 60 * 1000)
        active = await self._get(config, ctx, f"/api/websites/{site_id}/active", cache=30)
        stats = await self._get(config, ctx, f"/api/websites/{site_id}/stats", {"startAt": end - DAY_MS, "endAt": end}, cache=60)
        return _count((active or {}).get("visitors")), stats if isinstance(stats, dict) else {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        sites = await self._sites(config, ctx, cache=0)
        return f"Umami answers with {len(sites)} site{'' if len(sites) == 1 else 's'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        sites = await self._sites(config, ctx)
        if widget_kind == "sites":
            chosen = sorted(sites, key=lambda one: str(one.get("name") or "").lower())[: max(1, min(MAX_SITES, int(options.get("limit") or 5)))]
            return self._list([(site, *await self._numbers(config, ctx, site)) for site in chosen], len(sites))
        wanted = str(options.get("website") or "").strip().lower()
        site = next((one for one in sites if not wanted or wanted in (str(one.get("name") or "").lower(), str(one.get("domain") or "").lower())), None)
        if site is None:
            raise AdapterError(f"Umami has no site named {wanted!r}." if wanted else "Umami has no site yet.", code="no_such_site",
                               hint="Use the site's name or domain as Umami shows it.")
        active, stats = await self._numbers(config, ctx, site)
        return self._site(site, active, stats)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _site(site: dict[str, Any], active: int, stats: dict[str, Any]) -> WidgetData:
        visitors = _count(stats.get("visitors"))
        secondary: list[dict[str, Any]] = [
            {"label": "Visitors", "value": visitors},
            {"label": "Views", "value": _count(stats.get("pageviews"))},
        ]
        bounce = _bounce_rate(stats)
        if bounce is not None:
            secondary.append({"label": "Bounce rate", "value": bounce, "unit": "%"})
        return WidgetData(
            status="ok",
            primary={"label": "Right now", "value": active},
            secondary=secondary,
            metrics={"active": float(active), "visitors": float(visitors)},
        )

    @staticmethod
    def _list(rows: list[tuple[dict[str, Any], int, dict[str, Any]]], total: int) -> WidgetData:
        items = [{
            "title": str(site.get("name") or site.get("domain") or "?"),
            "subtitle": f"{_count(stats.get('visitors'))} visitors · {_count(stats.get('pageviews'))} views in 24 h",
            "status": "ok" if active else "unknown",
            "value": f"{active} now",
        } for site, active, stats in rows]
        return WidgetData(status="ok", items=items, secondary=[{"label": "Sites", "value": total}],
                          meta={"empty": "Umami has no site yet."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        sites = [{"id": "1", "name": "Blog", "domain": "blog.example.com"}, {"id": "2", "name": "Shop", "domain": "shop.example.com"},
                 {"id": "3", "name": "Docs", "domain": "docs.example.com"}]

        def numbers(site: dict[str, Any], scale: float) -> tuple[int, dict[str, Any]]:
            visitors = int(fake.walk(f"umami-{site['id']}", tick, 380 * scale, 520 * scale))
            return (int(fake.walk(f"umami-now-{site['id']}", tick, 1, 14 * scale)),
                    {"visitors": visitors, "visits": int(visitors * 1.2), "pageviews": int(visitors * 2.7), "bounces": int(visitors * 0.5)})

        scales = {"1": 1.0, "2": 0.6, "3": 0.25}
        if widget_kind == "sites":
            return self._list([(site, *numbers(site, scales[site["id"]])) for site in sites], len(sites))
        return self._site(sites[0], *numbers(sites[0], 1.0))


ADAPTER = UmamiAdapter()
