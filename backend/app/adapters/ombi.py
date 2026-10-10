"""Ombi: the requests waiting for approval, the latest ones, and open issues.

API v1 and v2, read-only, with Ombi's API key in an ``ApiKey`` header:
``/api/v1/Request/count`` for the requests by state, ``/api/v1/Issues/count``
for the issues, ``/api/v2/Requests/recentlyRequested`` for the latest
requests of every kind.

⚠️ ``/api/v1/Request/count`` answers without any key at all, so the test of
the connection asks the issue count, which does check it: a wrong key is a
401 with the text "Invalid API Key".

⚠️ A denied request keeps ``approved: true`` when it had been approved first,
and the count still adds it to the approved ones. A request reads as denied
whenever ``denied`` is true.

⚠️ A request's ``type`` is a number: 0 a series, 1 a film, 2 an album.

⚠️ The longer request lists, ``/api/v2/Requests/movie/...``, carry the
requesting user's whole account with each request. They are not read.

Checked against Ombi 4.53.10 running locally on 2026-10-10, verified by its
digest, with films and a series requested by the admin and by a plain user,
and one request denied.
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

KINDS = {0: "series", 1: "film", 2: "album"}
COUNT_SECONDS = 120
RECENT_SECONDS = 120


def state(request: dict[str, Any]) -> tuple[str, str]:
    """A request's colour and word."""
    if request.get("available"):
        return "ok", "available"
    if request.get("tvPartiallyAvailable"):
        return "ok", "partly available"
    if request.get("denied"):
        return "bad", "denied"
    if request.get("approved"):
        return "ok", "approved"
    return "warn", "pending"


class OmbiAdapter(Adapter):
    kind = "ombi"
    label = "Ombi"
    category = "media"
    description = "Ombi's requests for films, series and music: what waits for approval, the latest requests, and open issues."
    icon = "ombi"
    docs_url = "https://docs.ombi.app/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://ombi:5000"),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="The API key under Settings > Configuration > General in Ombi."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Overview", description="How many requests wait for approval, are approved, available or denied, and how many issues are open.",
                   renderer="value", default_size=(3, 2), refresh_seconds=300, metrics=("pending",)),
        WidgetType(kind="requests", label="Latest requests", description="The latest requests of every kind, who asked and where each stands.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("pending_only", "Only the pending ones", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=8))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/api{path}", verify=not config.get("insecure"),
                                     headers={"ApiKey": str(config.get("api_key") or "").strip(), "Accept": "application/json"},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Ombi refused the API key.", hint="The API key under Settings > Configuration > General in Ombi.")
        if response.status_code >= 400:
            raise AdapterError(f"Ombi answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            # An unknown path under /api gets Ombi's web page, not an error.
            raise AdapterError("Ombi did not answer with JSON.", code="not_json",
                               hint="The address of Ombi itself, with its base URL if it has one.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        await self._get(config, ctx, "/v1/Issues/count", 0)
        counts = await self._get(config, ctx, "/v1/Request/count", 0) or {}
        return f"Ombi answers, with {counts.get('pending') or 0} request{'s' if counts.get('pending') != 1 else ''} waiting for approval."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "requests":
            recent = await self._get(config, ctx, "/v2/Requests/recentlyRequested", RECENT_SECONDS)
            return self._requests([one for one in (recent or []) if isinstance(one, dict)],
                                  bool(options.get("pending_only")), max(1, int(options.get("limit") or 8)))
        issues = await self._get(config, ctx, "/v1/Issues/count", COUNT_SECONDS) or {}
        counts = await self._get(config, ctx, "/v1/Request/count", COUNT_SECONDS) or {}
        return self._summary(counts, issues)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(counts: dict[str, Any], issues: dict[str, Any]) -> WidgetData:
        pending = int(counts.get("pending") or 0)
        open_issues = int(issues.get("pending") or 0) + int(issues.get("inProgress") or 0)
        secondary: list[dict[str, Any]] = [
            {"label": "Approved", "value": int(counts.get("approved") or 0)},
            {"label": "Available", "value": int(counts.get("available") or 0)},
            {"label": "Denied", "value": int(counts.get("denied") or 0)},
            {"label": "Open issues", "value": open_issues},
        ]
        return WidgetData(
            status="warn" if pending or open_issues else "ok",
            primary={"label": "Pending", "value": pending, "metric": "pending"},
            secondary=secondary,
            metrics=measured({"pending": float(pending)}),
        )

    @staticmethod
    def _requests(requests: list[dict[str, Any]], pending_only: bool, limit: int) -> WidgetData:
        rows = []
        for request in requests:
            colour, word = state(request)
            if pending_only and word != "pending":
                continue
            kind = KINDS.get(request.get("type"), "")
            parts = [kind, str(request.get("username") or ""), ago(request.get("requestDate"))]
            rows.append({
                "id": f"{request.get('type')}-{request.get('requestId')}",
                "title": str(request.get("title") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": word,
                "status": colour,
            })
        return WidgetData(
            status="warn" if any(row["value"] == "pending" for row in rows) else "ok",
            items=rows[:limit],
            meta={"empty": "Nothing waits for approval" if pending_only else "No request yet"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        waiting = fake.flicker("ombi-dune", tick, 0.5)
        requests = [
            {"requestId": 7, "type": 1, "username": "sam", "title": "Dune: Part Two", "approved": not waiting, "requestDate": ""},
            {"requestId": 41, "type": 0, "username": "noa", "title": "Severance", "approved": False, "requestDate": ""},
            {"requestId": 6, "type": 1, "username": "alex", "title": "Past Lives", "approved": True, "available": True, "requestDate": ""},
            {"requestId": 5, "type": 1, "username": "sam", "title": "The Room", "approved": True, "denied": True, "requestDate": ""},
            {"requestId": 3, "type": 2, "username": "alex", "title": "Rumours", "approved": True, "requestDate": ""},
        ]
        if widget_kind == "requests":
            return self._requests(requests, bool(options.get("pending_only")), max(1, int(options.get("limit") or 8)))
        return self._summary({"pending": 2 if waiting else 1, "approved": 14, "available": 38, "denied": 3},
                             {"pending": 1, "inProgress": 0, "resolved": 9})


ADAPTER = OmbiAdapter()
