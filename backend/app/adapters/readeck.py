"""Readeck: the reading list, what is left unread, and what failed to save.

Readeck's API, read-only, with an API token from Settings > API Tokens as
Bearer: ``/api/bookmarks`` with its filters. The counts are the
``Total-Count`` header of a filtered list of one, so they cost one small
request each.

⚠️ A token can be limited to roles; "Bookmarks: Read Only" is all the cards
need.

⚠️ A bookmark that could not be fetched is kept, with an empty title and its
reason in ``errors``, which only the bookmark's own answer carries; the list
counts them with ``has_errors=true``.

⚠️ ``read_progress`` is a percentage; "unread" in Readeck's filters means
not started, "reading" started and "read" finished.

Checked against Readeck 0.23.4 running locally on 2026-10-10, verified by its
checksum, with four bookmarks, two of them failing to load, and against its
OpenAPI spec at that tag.
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
    base_url,
    measured,
)

COUNT_SECONDS = 300
#: The counts of the overview, as filters of the list.
COUNTS = {
    "unread": {"read_status": "unread", "is_archived": "false"},
    "total": {},
    "archived": {"is_archived": "true"},
    "marked": {"is_marked": "true"},
    "failed": {"has_errors": "true"},
}
#: The lists the bookmark card can show.
SHOWS = {"unread": {"read_status": ["unread", "reading"], "is_archived": "false"}, "marked": {"is_marked": "true"}, "all": {}}


class ReadeckAdapter(Adapter):
    kind = "readeck"
    label = "Readeck"
    category = "feeds"
    description = "Your Readeck reading list: how much is left unread, the latest bookmarks, and the ones that failed to save."
    icon = "readeck"
    docs_url = "https://readeck.org/en/docs/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://readeck:8000"),
        Field("token", "API token", type="password", secret=True, required=True,
              help="A token from Settings > API Tokens in Readeck; the role Bookmarks: Read Only is enough."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many bookmarks wait unread, how many there are, archived, marked, and failed to save.",
                   renderer="value", default_size=(3, 2), refresh_seconds=900, metrics=("unread",)),
        WidgetType(kind="bookmarks", label="Bookmarks", description="The latest bookmarks still to read, or the marked ones, with their site and progress, linking to each.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("show", "Show", type="select", default="unread",
                                  options=(("unread", "Still to read"), ("marked", "Marked"), ("all", "All"))),
                            Field("limit", "Entries", type="number", default=8))),
    )

    async def _list(self, config: dict[str, Any], ctx: Context, filters: dict[str, Any], limit: int, cache: float) -> tuple[list[dict[str, Any]], int | None]:
        response = await ctx.request("GET", f"{base_url(config)}/api/bookmarks", verify=not config.get("insecure"),
                                     params={"limit": limit, "sort": "-created", **filters},
                                     headers={"Authorization": f"Bearer {str(config.get('token') or '').strip()}", "Accept": "application/json"},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Readeck refused the API token.", hint="A token from Settings > API Tokens with the role Bookmarks: Read Only.")
        if response.status_code >= 400:
            raise AdapterError(f"Readeck answered with HTTP {response.status_code}.", code="http_error", hint="The address of Readeck itself.")
        try:
            listed = response.json()
        except ValueError as failure:
            raise AdapterError("Readeck did not answer with JSON.", code="not_json", hint="The address of Readeck itself.") from failure
        total = response.headers.get("Total-Count")
        return [one for one in (listed or []) if isinstance(one, dict)], int(total) if total and total.isdigit() else None

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        _, total = await self._list(config, ctx, {}, 1, 0)
        return f"Readeck answers, with {total if total is not None else '?'} bookmark{'s' if total != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "bookmarks":
            show = str(options.get("show") or "unread")
            limit = max(1, int(options.get("limit") or 8))
            listed, _ = await self._list(config, ctx, SHOWS.get(show, SHOWS["unread"]), limit, COUNT_SECONDS)
            return self._bookmarks(listed, base_url(config), show, limit)
        counts = {}
        for name, filters in COUNTS.items():
            _, counts[name] = await self._list(config, ctx, filters, 1, COUNT_SECONDS)
        return self._overview(counts)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(counts: dict[str, int | None]) -> WidgetData:
        unread = counts.get("unread")
        failed = counts.get("failed") or 0
        secondary: list[dict[str, Any]] = [
            {"label": "Bookmarks", "value": counts.get("total") if counts.get("total") is not None else "?"},
            {"label": "Archived", "value": counts.get("archived") if counts.get("archived") is not None else "?"},
            {"label": "Marked", "value": counts.get("marked") if counts.get("marked") is not None else "?"},
        ]
        if failed:
            secondary.append({"label": "Failed to save", "value": failed})
        return WidgetData(
            status="warn" if failed else "ok",
            primary={"label": "Unread", "value": unread if unread is not None else "?", "metric": "unread"},
            secondary=secondary,
            metrics=measured({"unread": float(unread) if unread is not None else None}),
        )

    @staticmethod
    def _bookmarks(bookmarks: list[dict[str, Any]], base: str, show: str, limit: int) -> WidgetData:
        rows = []
        for bookmark in bookmarks:
            minutes = bookmark.get("reading_time")
            parts = [str(bookmark.get("site_name") or bookmark.get("site") or "")]
            if isinstance(minutes, int) and minutes:
                parts.append(f"{minutes} min")
            failed = not bookmark.get("title") and bookmark.get("loaded")
            row: dict[str, Any] = {
                "id": bookmark.get("id"),
                "title": str(bookmark.get("title") or bookmark.get("url") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": "★" if bookmark.get("is_marked") else "",
                "status": "warn" if failed else "ok",
                "url": f"{base}/bookmarks/{bookmark.get('id')}" if base and bookmark.get("id") else None,
            }
            progress = bookmark.get("read_progress")
            if isinstance(progress, (int, float)) and 0 < progress < 100:
                row["progress"] = float(progress)
            rows.append(row)
        empty = {"unread": "Nothing left to read", "marked": "No marked bookmark"}.get(show, "No bookmark yet")
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": empty})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        unread = int(fake.counter("readeck-unread", tick, 20, 1 / 300)) % 40 + 12
        if widget_kind == "bookmarks":
            bookmarks = [
                {"id": "a1", "title": "The Best Code is No Code At All", "site_name": "Coding Horror", "reading_time": 3, "read_progress": 40, "loaded": True},
                {"id": "a2", "title": "Self-hosting email in 2026", "site_name": "example.org", "reading_time": 14, "read_progress": 0, "loaded": True, "is_marked": True},
                {"id": "a3", "title": "A field guide to ZFS snapshots", "site_name": "example.com", "reading_time": 22, "read_progress": 0, "loaded": True},
                {"id": "a4", "title": "", "url": "https://example.net/gone", "site": "example.net", "loaded": True},
            ]
            return self._bookmarks(bookmarks, "", str(options.get("show") or "unread"), max(1, int(options.get("limit") or 8)))
        return self._overview({"unread": unread, "total": 612, "archived": 538, "marked": 41, "failed": 1})


ADAPTER = ReadeckAdapter()
