"""BookStack: how big the wiki is and the pages changed last.

BookStack's REST API lives under ``/api`` and takes a token made on the
user's profile (API Tokens) as ``Authorization: Token <id>:<secret>``; the
user's role needs the "Access system API" permission. Every list answers
``{"data": [...], "total": n}`` and takes ``count``, ``offset`` and ``sort``
(``-updated_at`` for the newest first).

⚠️ The token sees only what its user may see; the counts are that user's.

⚠️ A page in the list names its book by number only. The book's slug for
the page's address comes from ``/api/books``.

⚠️ A missing or malformed header is refused with 401, a token without the
API permission or past its expiry with 403; both carry
``{"error": {"code", "message"}}``.

Written from BookStack's source (``routes/api.php``, ``ApiTokenGuard``,
``ListingResponseBuilder`` and ``PageApiController``, release v26.09.1).
Not yet run against an installation.
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
)

COUNT_SECONDS = 600
RECENT_SECONDS = 120
#: Books read for their names and slugs; a wiki with more lists the rest without.
MOST_BOOKS = 500


class BookStackAdapter(Adapter):
    kind = "bookstack"
    label = "BookStack"
    category = "other"
    description = "A BookStack wiki: how many shelves, books, chapters and pages it holds, and the pages changed last."
    icon = "bookstack"
    docs_url = "https://demo.bookstackapp.com/api/docs"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://wiki.example.com"),
        Field("token_id", "Token ID", secret=True, required=True),
        Field("token_secret", "Token secret", type="password", secret=True, required=True,
              help="Made on the user's profile under API Tokens. The user's role needs the permission Access system API."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many shelves, books, chapters and pages the wiki holds.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("pages",)),
        WidgetType(kind="recent", label="Changed last", description="The pages changed last with their book, each opening the page.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _list(self, config: dict[str, Any], ctx: Context, what: str, params: dict[str, Any], cache: float) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/api/{what}", params=params,
                                     headers={"Accept": "application/json",
                                              "Authorization": f"Token {config.get('token_id') or ''}:{config.get('token_secret') or ''}"},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError(f"BookStack answered with HTTP {response.status_code} and no JSON.", code="not_json",
                               hint="The address of BookStack itself, without /api.") from failure
        if response.status_code in (401, 403):
            error = answer.get("error") if isinstance(answer, dict) else None
            message = str(error.get("message") if isinstance(error, dict) else error or "")
            raise AuthFailed(f"BookStack refused the token{f': {message}' if message else '.'}",
                             hint="A token from the user's profile, API Tokens, whose role may Access system API.")
        if response.status_code >= 400 or not isinstance(answer, dict):
            raise AdapterError(f"BookStack answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of BookStack itself, without /api.")
        return answer

    async def _total(self, config: dict[str, Any], ctx: Context, what: str, cache: float = COUNT_SECONDS) -> int:
        return int((await self._list(config, ctx, what, {"count": 1}, cache)).get("total") or 0)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        books = await self._total(config, ctx, "books", cache=0)
        pages = await self._total(config, ctx, "pages", cache=0)
        return f"BookStack answers with {books} book{'s' if books != 1 else ''} and {pages} page{'s' if pages != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "recent":
            limit = max(1, int(options.get("limit") or 8))
            pages = (await self._list(config, ctx, "pages", {"count": limit, "sort": "-updated_at"}, RECENT_SECONDS)).get("data") or []
            books = (await self._list(config, ctx, "books", {"count": MOST_BOOKS}, COUNT_SECONDS)).get("data") or []
            return self._recent(pages, books, base_url(config), limit)
        counts = {what: await self._total(config, ctx, what) for what in ("shelves", "books", "chapters", "pages")}
        return self._overview(counts)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(counts: dict[str, int]) -> WidgetData:
        return WidgetData(
            status="ok",
            primary={"label": "Pages", "value": counts["pages"], "metric": "pages"},
            secondary=[{"label": "Books", "value": counts["books"]}, {"label": "Shelves", "value": counts["shelves"]},
                       {"label": "Chapters", "value": counts["chapters"]}],
            metrics={"pages": float(counts["pages"])},
        )

    @staticmethod
    def _recent(pages: list[Any], books: list[Any], base: str, limit: int) -> WidgetData:
        by_id = {book.get("id"): book for book in books if isinstance(book, dict)}
        rows = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            book = by_id.get(page.get("book_id")) or {}
            when = ago(page.get("updated_at"))
            url = f"{base}/books/{book['slug']}/page/{page['slug']}" if base and book.get("slug") and page.get("slug") else None
            rows.append({
                "id": str(page.get("id")),
                "title": str(page.get("name") or "?"),
                "subtitle": " · ".join(part for part in (str(book.get("name") or ""), "draft" if page.get("draft") else "",
                                                         f"changed {when} ago" if when else "") if part),
                "url": url,
                "status": "ok",
            })
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "No page yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "recent":
            books = [{"id": 1, "name": "Homelab", "slug": "homelab"}, {"id": 2, "name": "Recipes", "slug": "recipes"}]
            names = ("Backup rotation", "Proxmox upgrade notes", "Sourdough", "VLAN plan", "UPS runtime test")
            pages = [{"id": index, "book_id": 2 if name == "Sourdough" else 1, "name": name, "slug": name.lower().replace(" ", "-"),
                      "draft": index == 4, "updated_at": ""} for index, name in enumerate(names)]
            return self._recent(pages, books, "", max(1, int(options.get("limit") or 8)))
        return self._overview({"shelves": 4, "books": 12, "chapters": 37, "pages": 214 + fake.counter("bookstack-pages", tick, 0, 0.01)})


ADAPTER = BookStackAdapter()
