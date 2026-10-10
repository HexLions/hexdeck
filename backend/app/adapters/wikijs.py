"""Wiki.js: how big the wiki is, whether it is up to date, and what changed.

Wiki.js 2's GraphQL API at ``/graphql``, read-only, with an API key as
Bearer: ``system.info`` for the counts and the versions, ``pages.list`` for
the pages changed last.

⚠️ The API is off until it is turned on under Administration > API Access,
where the keys are made too. ``system.info`` needs an administrator's rights,
so the key must have full access.

⚠️ A wrong or expired key is not refused: Wiki.js reads the request as a
guest's, and a guest may usually read the pages. Only ``system.info``
answers "Forbidden", with HTTP 200 and the error in ``errors``, so the
connection test asks it.

⚠️ ``latestVersion`` is what Wiki.js last heard from its update check; in
offline mode it is its own version.

Checked against Wiki.js 2.5.315 running locally on 2026-10-10, verified by
its digest, with SQLite and four pages; the 3.0 betas have another API and
are not supported.
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

INFO = "{ system { info { currentVersion latestVersion pagesTotal usersTotal tagsTotal } } }"
PAGES = "{ pages { list(orderBy: UPDATED, orderByDirection: DESC, limit: %d) { id path locale title updatedAt isPublished } } }"
INFO_SECONDS = 900
PAGES_SECONDS = 300


class WikiJsAdapter(Adapter):
    kind = "wikijs"
    label = "Wiki.js"
    category = "other"
    description = "Wiki.js: how many pages, users and tags it holds, whether an update is out, and the pages changed last."
    icon = "wikijs"
    docs_url = "https://docs.requarks.io/dev/api"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://wiki.example.com"),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="A full-access key from Administration > API Access, after turning the API on there."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many pages, users and tags there are, and whether a newer Wiki.js is out.",
                   renderer="value", default_size=(3, 2), refresh_seconds=1800, metrics=("pages",)),
        WidgetType(kind="pages", label="Pages", description="The pages changed last, with when, linking to each.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _query(self, config: dict[str, Any], ctx: Context, query: str, cache: float) -> dict[str, Any]:
        response = await ctx.request("POST", f"{base_url(config)}/graphql", verify=not config.get("insecure"), json_body={"query": query},
                                     headers={"Authorization": f"Bearer {str(config.get('api_key') or '').strip()}", "Accept": "application/json"},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Wiki.js refused the API key.", hint="A full-access key from Administration > API Access.")
        if response.status_code >= 400:
            raise AdapterError(f"Wiki.js answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of Wiki.js itself, which serves /graphql.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Wiki.js did not answer with JSON.", code="not_json", hint="The address of Wiki.js itself, which serves /graphql.") from failure
        errors = [str((one or {}).get("message") or "") for one in (answer.get("errors") or [])]
        if "Forbidden" in errors:
            raise AuthFailed("Wiki.js took the API key for a guest's.",
                             hint="A full-access key from Administration > API Access, with the API turned on there. A wrong key is read as a guest.")
        if errors and not answer.get("data"):
            raise AdapterError(f"Wiki.js answered: {errors[0]}", code="graphql_error")
        return answer.get("data") or {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        info = ((await self._query(config, ctx, INFO, 0)).get("system") or {}).get("info") or {}
        return f"Wiki.js {info.get('currentVersion') or '?'} answers, with {info.get('pagesTotal') or 0} pages."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "pages":
            limit = max(1, int(options.get("limit") or 8))
            listed = ((await self._query(config, ctx, PAGES % limit, PAGES_SECONDS)).get("pages") or {}).get("list") or []
            return self._pages([one for one in listed if isinstance(one, dict)], base_url(config), limit)
        info = ((await self._query(config, ctx, INFO, INFO_SECONDS)).get("system") or {}).get("info") or {}
        return self._overview(info)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(info: dict[str, Any]) -> WidgetData:
        current, latest = str(info.get("currentVersion") or ""), str(info.get("latestVersion") or "")
        outdated = bool(current and latest and current != latest)
        pages = info.get("pagesTotal")
        secondary: list[dict[str, Any]] = [
            {"label": "Users", "value": int(info.get("usersTotal") or 0)},
            {"label": "Tags", "value": int(info.get("tagsTotal") or 0)},
            {"label": "Version", "value": f"{current} → {latest}" if outdated else current or "?"},
        ]
        return WidgetData(
            status="warn" if outdated else "ok",
            primary={"label": "Pages", "value": int(pages) if isinstance(pages, int) else "?", "metric": "pages"},
            secondary=secondary,
            metrics=measured({"pages": float(pages) if isinstance(pages, int) else None}),
            meta={"notice": f"Wiki.js {latest} is out." if outdated else ""},
        )

    @staticmethod
    def _pages(pages: list[dict[str, Any]], base: str, limit: int) -> WidgetData:
        rows = []
        for page in pages:
            path = str(page.get("path") or "")
            rows.append({
                "id": page.get("id"),
                "title": str(page.get("title") or path or "?"),
                "subtitle": " · ".join(part for part in (path, ago(page.get("updatedAt"))) if part),
                "value": "" if page.get("isPublished", True) else "draft",
                "status": "ok",
                "url": f"{base}/{page.get('locale') or 'en'}/{path}" if base and path else None,
            })
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "No page yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "pages":
            titles = [("homelab/network", "Network layout"), ("homelab/backups", "Backup plan"), ("recipes/bread", "Sourdough bread"),
                      ("home", "Home"), ("homelab/power", "UPS and power"), ("travel/japan", "Japan 2027")]
            first = fake.counter("wikijs-edit", tick, 0, 1 / 150) % len(titles)
            pages = [{"id": index, "path": titles[(first + index) % len(titles)][0], "title": titles[(first + index) % len(titles)][1],
                      "locale": "en", "updatedAt": "", "isPublished": True} for index in range(len(titles))]
            return self._pages(pages, "", max(1, int(options.get("limit") or 8)))
        return self._overview({"currentVersion": "2.5.315", "latestVersion": "2.5.315", "pagesTotal": 214, "usersTotal": 4, "tagsTotal": 37})


ADAPTER = WikiJsAdapter()
