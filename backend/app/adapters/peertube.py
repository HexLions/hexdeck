"""PeerTube: how big the instance is, and the videos published last.

PeerTube's REST API answers these without signing in: ``/api/v1/server/stats``
(videos, views, storage, users, followers, reports), ``/api/v1/config`` (the
version), ``/api/v1/config/about`` (the instance's name) and
``/api/v1/videos`` (``isLocal=true``, ``sort=-publishedAt``) for what was
published here, newest first.

⚠️ ``totalVideos`` counts the federated ones too; the instance's own are
``totalLocalVideos``, and so on for views and comments. The card shows the
local ones.

⚠️ Abuse reports are counted for everyone to see, handled or not; the card
turns amber while some wait for a moderator.

Checked against Framatube (PeerTube 8.3.1) on 2026-10-15.
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
    duration_short,
    human_bytes,
)

STATS_SECONDS = 600
VIDEOS_SECONDS = 300


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class PeerTubeAdapter(Adapter):
    kind = "peertube"
    label = "PeerTube"
    category = "media"
    description = "A PeerTube instance: its own videos, views, storage, users and followers, the reports waiting, and the videos published last."
    icon = "peertube"
    docs_url = "https://docs.joinpeertube.org/api-rest-reference.html"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://videos.example.com",
              help="The instance's address. Everything shown is public on every instance, so no account is needed."),
    )
    widgets = (
        WidgetType(kind="instance", label="Instance", description="The instance's own videos, their views and storage, the users, the followers and the reports waiting.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("videos", "views")),
        WidgetType(kind="latest", label="Published last", description="The videos published on this instance last, with their channel and views, each opening the video.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=6),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float, params: dict[str, Any] | None = None) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/api/v1{path}", params=params, headers={"Accept": "application/json"},
                                     cache_seconds=cache)
        if response.status_code >= 400:
            raise AdapterError(f"PeerTube answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of the instance itself, without /api.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("PeerTube did not answer with JSON.", code="not_json", hint="The address of the instance itself, without /api.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        about = await self._get(config, ctx, "/config/about", 0) or {}
        version = (await self._get(config, ctx, "/config", 0) or {}).get("serverVersion") or ""
        name = (about.get("instance") or {}).get("name") or "The instance"
        return f"{name} answers, PeerTube {version}.".replace("PeerTube .", "PeerTube.")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "latest":
            limit = max(1, min(int(options.get("limit") or 6), 25))
            videos = await self._get(config, ctx, "/videos", VIDEOS_SECONDS, {"isLocal": "true", "sort": "-publishedAt", "count": limit})
            return self._latest((videos or {}).get("data") or [], limit)
        return self._instance(await self._get(config, ctx, "/server/stats", STATS_SECONDS) or {})

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _instance(stats: dict[str, Any]) -> WidgetData:
        videos, views = _count(stats.get("totalLocalVideos")), _count(stats.get("totalLocalVideoViews"))
        waiting = max(0, _count(stats.get("totalAbuses")) - _count(stats.get("totalAbusesProcessed")))
        secondary: list[dict[str, Any]] = [
            {"label": "Views", "value": views, "metric": "views"},
            {"label": "Storage", "value": human_bytes(float(stats.get("totalLocalVideoFilesSize") or 0))},
            {"label": "Users", "value": _count(stats.get("totalUsers"))},
            {"label": "Followers", "value": _count(stats.get("totalInstanceFollowers"))},
        ]
        if waiting:
            secondary.append({"label": "Reports open", "value": waiting})
        return WidgetData(
            status="warn" if waiting else "ok",
            primary={"label": "Videos", "value": videos, "metric": "videos"},
            secondary=secondary,
            metrics={"videos": float(videos), "views": float(views)},
        )

    @staticmethod
    def _latest(videos: list[Any], limit: int) -> WidgetData:
        rows = []
        for video in videos[:limit]:
            if not isinstance(video, dict):
                continue
            when = ago(video.get("publishedAt"))
            rows.append({
                "id": str(video.get("uuid") or video.get("id")),
                "title": str(video.get("name") or "?"),
                "subtitle": " · ".join(part for part in (
                    str((video.get("channel") or {}).get("displayName") or ""),
                    "live" if video.get("isLive") else duration_short(float(video.get("duration") or 0)) if video.get("duration") else "",
                    f"{when} ago" if when else "") if part),
                "value": f"{_count(video.get('views'))} views",
                "url": video.get("url"),
                "status": "ok",
            })
        return WidgetData(status="ok", items=rows, meta={"empty": "No video yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "latest":
            videos = [{"uuid": str(index), "name": name, "channel": {"displayName": channel}, "duration": 600 + index * 330, "views": 40 + index * 17}
                      for index, (name, channel) in enumerate((("Racking the new switch", "Homelab notes"), ("Sourdough, week three", "Kitchen"),
                                                              ("Backups that restore", "Homelab notes")))]
            return self._latest(videos, max(1, int(options.get("limit") or 6)))
        return self._instance({"totalLocalVideos": 142, "totalLocalVideoViews": 18_400 + fake.counter("peertube-views", tick, 0, 3),
                               "totalLocalVideoFilesSize": 312 * 1024 ** 3, "totalUsers": 6, "totalInstanceFollowers": 23,
                               "totalAbuses": 3, "totalAbusesProcessed": 3})


ADAPTER = PeerTubeAdapter()
