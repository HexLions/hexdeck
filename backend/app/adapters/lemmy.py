"""Lemmy: how alive the instance is, and what is hot in its communities.

Lemmy's API (``/api/v3``, the one 0.19 speaks) answers these without signing
in: ``/api/v3/site`` gives the version and the counts (users, posts,
comments, communities, users active in the last day, week, month and half
year), ``/api/v3/post/list`` with ``type_=Local`` and ``sort=Hot`` the posts
of the instance's own communities.

⚠️ A local community holds posts from other instances too; a post's ``ap_id``
is its home. The card opens the post here, at ``/post/<id>``.

⚠️ ``url`` is the link a post shares and is missing on a text post.

⚠️ A post marked NSFW is left out of the list, as Lemmy does for a visitor.

Checked against lemmy.ml (Lemmy 0.19.20) on 2026-10-15.
"""

from __future__ import annotations

from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    Context,
    Field,
    WidgetData,
    WidgetType,
    ago,
    base_url,
)

SITE_SECONDS = 600
POSTS_SECONDS = 300


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class LemmyAdapter(Adapter):
    kind = "lemmy"
    label = "Lemmy"
    category = "feeds"
    description = "A Lemmy instance: who was active today and this month, its users, posts, comments and communities, and what is hot in its communities."
    icon = "lemmy"
    docs_url = "https://join-lemmy.org/docs/contributors/04-api.html"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://lemmy.example.com",
              help="The instance's address. Everything shown is public, so no account is needed."),
    )
    widgets = (
        WidgetType(kind="site", label="Instance", description="Users active today and this month, and the users, posts, comments and communities in all.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("active_day",)),
        WidgetType(kind="hot", label="Hot", description="The hot posts of the instance's own communities, with their community, score and comments.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/api/v3{path}", params=params, headers={"Accept": "application/json"},
                                     cache_seconds=cache)
        if response.status_code == 404:
            raise AdapterError("This address has no Lemmy API v3.", code="http_error",
                               hint="The address of the instance itself. Lemmy 0.19 speaks v3; a later version may not.")
        if response.status_code >= 400:
            raise AdapterError(f"Lemmy answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of the instance itself, without /api.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Lemmy did not answer with JSON.", code="not_json", hint="The address of the instance itself.") from failure
        return answer if isinstance(answer, dict) else {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        site = await self._get(config, ctx, "/site", 0)
        name = ((site.get("site_view") or {}).get("site") or {}).get("name") or "The instance"
        return f"{name} answers, Lemmy {site.get('version') or ''}.".replace("Lemmy .", "Lemmy.")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "hot":
            limit = max(1, min(int(options.get("limit") or 8), 50))
            answer = await self._get(config, ctx, "/post/list", POSTS_SECONDS, {"type_": "Local", "sort": "Hot", "limit": limit})
            return self._hot(answer.get("posts") or [], base_url(config), limit)
        return self._site((await self._get(config, ctx, "/site", SITE_SECONDS)).get("site_view") or {})

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _site(view: dict[str, Any]) -> WidgetData:
        counts = view.get("counts") or {}
        today = _count(counts.get("users_active_day"))
        return WidgetData(
            status="ok",
            primary={"label": "Active today", "value": today, "metric": "active_day"},
            secondary=[
                {"label": "This month", "value": _count(counts.get("users_active_month"))},
                {"label": "Users", "value": _count(counts.get("users"))},
                {"label": "Posts", "value": _count(counts.get("posts"))},
                {"label": "Comments", "value": _count(counts.get("comments"))},
                {"label": "Communities", "value": _count(counts.get("communities"))},
            ],
            metrics={"active_day": float(today)},
        )

    @staticmethod
    def _hot(posts: list[Any], base: str, limit: int) -> WidgetData:
        rows = []
        for entry in posts:
            post = (entry or {}).get("post") or {}
            if not post or post.get("nsfw"):
                continue
            community = entry.get("community") or {}
            counts = entry.get("counts") or {}
            when = ago(post.get("published"))
            rows.append({
                "id": str(post.get("id")),
                "title": str(post.get("name") or "?"),
                "subtitle": " · ".join(part for part in (str(community.get("title") or community.get("name") or ""),
                                                         f"{_count(counts.get('comments'))} comment{'s' if _count(counts.get('comments')) != 1 else ''}", f"{when} ago" if when else "") if part),
                "value": f"▲ {_count(counts.get('score'))}",
                "url": f"{base}/post/{post['id']}" if base and post.get("id") else None,
                "status": "ok",
            })
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "Nothing posted yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "hot":
            posts = [{"post": {"id": index, "name": name}, "community": {"title": community}, "counts": {"score": 120 - index * 31, "comments": 40 - index * 9}}
                     for index, (name, community) in enumerate((("Show us your rack", "Homelab"), ("Which UPS for a small NAS?", "Homelab"),
                                                               ("Release notes for this week", "Self-hosted")))]
            return self._hot(posts, "", max(1, int(options.get("limit") or 8)))
        return self._site({"counts": {"users_active_day": 140 + fake.counter("lemmy-day", tick, 0, 0.05), "users_active_month": 910,
                                      "users": 12_400, "posts": 38_200, "comments": 211_000, "communities": 640}})


ADAPTER = LemmyAdapter()
