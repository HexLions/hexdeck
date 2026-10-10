"""Owncast: whether the stream is live, for how long, and who is watching.

Owncast's API, read-only. ``/api/status`` is public and says whether the
stream is live, its title and when it last started or stopped. With the
admin password, ``/api/admin/status`` adds the viewers, the peaks and what
the broadcaster sends; the user name is always "admin", as basic auth.

⚠️ Since 0.3 the public status no longer carries the viewer count; it needs
the admin password.

⚠️ A viewer counts while their player pings Owncast, and drops out about a
minute after it stops, so the count trails real viewers a little.

⚠️ The admin answer carries the broadcaster's IP address and port; it is
not shown.

Checked against Owncast 0.3.0 running locally on 2026-10-10, verified by its
digest, live from a test pattern pushed over RTMP and offline afterwards.
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

STATUS_SECONDS = 30


class OwncastAdapter(Adapter):
    kind = "owncast"
    label = "Owncast"
    category = "media"
    description = "Your Owncast stream: whether it is live and since when, its title, and with the admin password the viewers and what is being sent."
    icon = "owncast"
    docs_url = "https://owncast.online/api/latest/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://live.example.com"),
        Field("admin_password", "Admin password", type="password", secret=True,
              help="Only for the viewers and the stream details. Without it the card shows whether the stream is live."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="stream", label="Stream", description="Whether the stream is live and since when, its title, the viewers and their peak, and the resolution and bitrate sent.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("viewers",)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, admin: bool = False, cache: float = STATUS_SECONDS) -> Any:
        password = str(config.get("admin_password") or "")
        response = await ctx.request("GET", f"{base_url(config)}{path}", verify=not config.get("insecure"),
                                     headers={"Accept": "application/json"}, cache_seconds=cache, auth_errors=False,
                                     auth=("admin", password) if admin else None)
        if response.status_code in (401, 403):
            raise AuthFailed("Owncast refused the admin password.", hint="The admin password of Owncast, the one its /admin page asks for.")
        if response.status_code >= 400:
            raise AdapterError(f"Owncast answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address the stream is watched at.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Owncast did not answer with JSON.", code="not_json", hint="The address the stream is watched at.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        status = await self._get(config, ctx, "/api/status", cache=0) or {}
        if config.get("admin_password"):
            await self._get(config, ctx, "/api/admin/status", admin=True, cache=0)
        return f"Owncast {status.get('versionNumber') or '?'} answers; the stream is {'live' if status.get('online') else 'offline'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        status = await self._get(config, ctx, "/api/status") or {}
        admin = await self._get(config, ctx, "/api/admin/status", admin=True) if config.get("admin_password") else None
        return self._stream(status, admin)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _stream(status: dict[str, Any], admin: dict[str, Any] | None) -> WidgetData:
        live = bool(status.get("online"))
        secondary: list[dict[str, Any]] = []
        if live and status.get("lastConnectTime"):
            secondary.append({"label": "Live for", "value": ago(status["lastConnectTime"])})
        elif not live and status.get("lastDisconnectTime"):
            secondary.append({"label": "Last live", "value": ago(status["lastDisconnectTime"])})
        viewers = None
        if admin is not None:
            viewers = int(admin.get("viewerCount") or 0)
            secondary.insert(0, {"label": "Viewers", "value": viewers, "metric": "viewers"})
            secondary.append({"label": "Peak", "value": int(admin.get("sessionPeakViewerCount" if live else "overallPeakViewerCount") or 0)})
            details = ((admin.get("broadcaster") or {}).get("streamDetails") or {}) if live else {}
            if details.get("height"):
                secondary.append({"label": "Sending", "value": f"{details.get('height')}p · {details.get('videoBitrate') or 0} kbit/s"})
        if status.get("streamTitle"):
            secondary.append({"label": "Title", "value": str(status["streamTitle"])[:60]})
        return WidgetData(
            status="ok" if live else "unknown",
            primary={"label": "Stream", "value": "live" if live else "offline"},
            secondary=secondary,
            metrics=measured({"viewers": float(viewers) if viewers is not None else None}),
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        live = not fake.flicker("owncast-off", tick, 0.2)
        viewers = int(fake.walk("owncast-viewers", tick, 3, 41)) if live else 0
        status = {"online": live, "streamTitle": "Building a homelab rack", "lastConnectTime": "", "lastDisconnectTime": "", "versionNumber": "0.3.0"}
        admin = {"viewerCount": viewers, "sessionPeakViewerCount": max(viewers, 44), "overallPeakViewerCount": 128,
                 "broadcaster": {"streamDetails": {"height": 1080, "videoBitrate": 6000}} if live else None}
        return self._stream(status, admin)


ADAPTER = OwncastAdapter()
