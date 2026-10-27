"""Outline: the documents changed most recently, and how many documents,
collections and people the wiki has.

Written from Outline's source, v1.12.0 (server/middlewares/authentication.ts,
server/routes/api/documents, collections, users, middlewares/pagination.ts,
server/presenters/document.ts). Not yet run against an Outline: it needs
PostgreSQL and Redis besides itself.

⚠️ Every call is a POST with a JSON body, also for reading. An API key (the
settings, then API keys; it starts ``ol_api_``) goes as ``Authorization:
Bearer``. A wrong or expired key is 401; a key whose scopes leave a call out
is 403 "API key does not have access to this resource", so the card names the
three calls a scoped key needs.

⚠️ ``pagination.total`` is the exact count only for callers that do not send
``x-client-version`` (Outline's own app sends it and gets an estimate). The
card sends none and asks for one row to read the totals.

⚠️ ``auth.info`` is left alone: for a key of a user who signs in through SSO it
schedules a check of that sign-in every time it is asked.

⚠️ The read routes are rate limited (``documents.list`` to a hundred a minute
per key); the cards refresh every five minutes. Being POSTs, the answers are
not kept between cards: each refresh costs two or three calls.
"""

from __future__ import annotations

import time
from datetime import datetime
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
)

SCOPES_HINT = "A key with scopes needs documents.list, collections.list and users.list."


class OutlineAdapter(Adapter):
    kind = "outline"
    label = "Outline"
    category = "other"
    description = "Outline wiki: the documents changed most recently, and how many documents, collections and people it has."
    icon = "outline"
    beta = True
    docs_url = "https://www.getoutline.com/developers"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://wiki.example.com"),
        Field("api_key", "API key", secret=True, required=True,
              help="In Outline: Settings, then API keys. It starts with ol_api_. " + SCOPES_HINT),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Wiki", description="How many documents, collections and people the wiki has.",
                   renderer="value", default_size=(3, 2), refresh_seconds=900, metrics=("documents",)),
        WidgetType(kind="recent", label="Recently changed", description="The documents changed most recently, with their collection, who changed them and when.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _post(self, config: dict[str, Any], ctx: Context, method: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await ctx.request("POST", f"{base_url(config)}/api/{method}", json_body=body,
                                     headers={"Authorization": f"Bearer {str(config.get('api_key') or '').strip()}"},
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.status_code == 401:
            raise AuthFailed("Outline refused the API key.", hint="Make a new one in Outline under Settings, then API keys.")
        if response.status_code == 403:
            raise AuthFailed(f"The API key may not call {method}.", hint=SCOPES_HINT)
        if response.status_code == 429:
            raise AdapterError("Outline asks to slow down for a while.", code="rate_limited",
                               hint="Its read calls are limited per key; a slower refresh helps.")
        if response.status_code >= 400:
            raise AdapterError(f"Outline answered {method} with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Outline's own address, without /api.")
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if not isinstance(answer, dict) or not isinstance(answer.get("data"), list):
            raise AdapterError("This address answers, but not the way Outline does.", code="not_outline",
                               hint="Check the URL; it is Outline's own address, without /api.")
        return answer

    @staticmethod
    def _total(answer: dict[str, Any]) -> int:
        total = (answer.get("pagination") or {}).get("total")
        return int(total) if isinstance(total, int | float) else len(answer["data"])

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        collections = self._total(await self._post(config, ctx, "collections.list", {"limit": 1}))
        return f"Outline answers with {collections} collection{'' if collections == 1 else 's'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "recent":
            limit = max(1, min(25, int(options.get("limit") or 8)))
            documents = await self._post(config, ctx, "documents.list", {"sort": "updatedAt", "direction": "DESC", "limit": limit})
            collections = await self._post(config, ctx, "collections.list", {"limit": 100})
            names = {str(one.get("id")): str(one.get("name") or "") for one in collections["data"] if isinstance(one, dict)}
            return self._recent(documents["data"], names)
        documents = self._total(await self._post(config, ctx, "documents.list", {"limit": 1}))
        collections = self._total(await self._post(config, ctx, "collections.list", {"limit": 1}))
        users = self._total(await self._post(config, ctx, "users.list", {"limit": 1}))
        return self._summary(documents, collections, users)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(documents: int, collections: int, users: int) -> WidgetData:
        return WidgetData(status="ok", primary={"label": "Documents", "value": documents},
                          secondary=[{"label": "Collections", "value": collections}, {"label": "Users", "value": users}],
                          metrics={"documents": float(documents)})

    @staticmethod
    def _recent(documents: list[Any], names: dict[str, str]) -> WidgetData:
        items = []
        for one in documents:
            if not isinstance(one, dict):
                continue
            when = ago(one.get("updatedAt"))
            who = str(((one.get("updatedBy") or {}).get("name")) or "")
            items.append({
                "title": str(one.get("title") or "Untitled"),
                "subtitle": " · ".join(part for part in (names.get(str(one.get("collectionId")), ""), who, f"{when} ago" if when else "") if part),
                "status": "ok",
            })
        return WidgetData(status="ok", items=items, meta={"empty": "No document has been written yet."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "recent":
            now = time.time()

            def doc(title: str, collection: str, who: str, minutes: float) -> dict[str, Any]:
                return {"title": title, "collectionId": collection, "updatedBy": {"name": who},
                        "updatedAt": datetime.fromtimestamp(now - minutes * 60).astimezone().isoformat()}

            return self._recent([
                doc("Backup rotation", "c1", "Alex", fake.walk("outline-1", tick, 3, 40)),
                doc("Network map", "c1", "Sam", 180),
                doc("Router upgrade plan", "c2", "Alex", 60 * 26),
                doc("Guest Wi-Fi", "c3", "Sam", 60 * 24 * 4),
            ], {"c1": "Homelab", "c2": "Projects", "c3": "House"})
        return self._summary(128 + tick % 3, 6, 3)


ADAPTER = OutlineAdapter()
