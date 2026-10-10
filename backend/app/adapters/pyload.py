"""pyLoad: the download speed, what runs, what waits and what failed.

pyLoad's own API (pyload-ng 0.5), read-only: ``/api/status_server`` for the
speed and the counts, ``/api/status_downloads`` for the downloads running,
``/api/get_queue_data`` for the links of the queue with their status and
error, ``/api/free_space`` for the room left where it downloads.

⚠️ Two ways in, by version. Since spring 2026 (0.5.0b3.dev97) pyLoad takes
API keys, made under Settings > Users, sent as ``X-API-Key``; user name and
password no longer open the API. Before that the API took the user name and
password as HTTP basic auth. A key is used when one is set, otherwise the
user name and password.

⚠️ A link's status is a number: 0 finished, 8 failed, 9 aborted, 12
downloading, 3 queued, 5 waiting, and so on (DownloadStatus in pyLoad's
source); ``statusmsg`` carries the same as a word.

⚠️ pyLoad refuses links to its own network ("Refusing to download from
Server-Side host"); such links fail at once with that error.

Checked against pyload-ng 0.5.0b3.dev101 running locally on 2026-10-10, with
an API key, one download running and three failed links, and against
0.5.0b3.dev95 with basic auth.
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
    human_bytes,
    measured,
)

FAILED = {8, 9, 1}  # failed, aborted, offline
STATUS_SECONDS = 10
QUEUE_SECONDS = 30


def _eta(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min" if minutes else "< 1 min"


class PyLoadAdapter(Adapter):
    kind = "pyload"
    label = "pyLoad"
    category = "downloads"
    description = "The download manager pyLoad: its speed, the downloads running with their progress, and the links that failed."
    icon = "pyload"
    docs_url = "https://github.com/pyload/pyload/wiki"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://pyload:8000"),
        Field("api_key", "API key", type="password", secret=True,
              help="An API key from Settings > Users, on pyLoad from 2026 on. Older versions take the user name and password instead."),
        Field("username", "Username", help="Only for pyLoad from before API keys."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="The download speed, how many downloads run and wait, and the free space.",
                   renderer="value", default_size=(3, 2), refresh_seconds=15, metrics=("speed",)),
        WidgetType(kind="downloads", label="Downloads", description="The downloads running with their progress and time left, then the links that failed with pyLoad's reason.",
                   renderer="list", default_size=(4, 3), refresh_seconds=15,
                   options=(Field("failed", "Show the failed links", type="bool", default=True),
                            Field("limit", "Entries", type="number", default=10))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, method: str, cache: float) -> Any:
        key = str(config.get("api_key") or "").strip()
        headers = {"Accept": "application/json"}
        auth = None
        if key:
            headers["X-API-Key"] = key
        elif config.get("username"):
            auth = (str(config.get("username")), str(config.get("password") or ""))
        response = await ctx.request("GET", f"{base_url(config)}/api/{method}", verify=not config.get("insecure"),
                                     headers=headers, auth=auth, cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            if key:
                raise AuthFailed("pyLoad refused the API key.", hint="An API key from Settings > Users in pyLoad; it starts with pl_.")
            raise AuthFailed("pyLoad refused the user name and password.",
                             hint="pyLoad from 2026 on opens its API only to API keys, made under Settings > Users.")
        if response.status_code == 429:
            raise AdapterError("pyLoad's limit of a hundred API requests a minute is used up.", code="rate_limited")
        if response.status_code >= 400:
            raise AdapterError(f"pyLoad answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of pyLoad's web interface, usually port 8000.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("pyLoad did not answer with JSON.", code="not_json",
                               hint="The address of pyLoad's web interface, usually port 8000.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        status = await self._get(config, ctx, "status_server", 0) or {}
        version = await self._get(config, ctx, "get_server_version", 0)
        return f"pyLoad {version or '?'} answers, with {status.get('queue') or 0} link{'s' if status.get('queue') != 1 else ''} in the queue."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "downloads":
            running = await self._get(config, ctx, "status_downloads", STATUS_SECONDS) or []
            queue = await self._get(config, ctx, "get_queue_data", QUEUE_SECONDS) if options.get("failed", True) else []
            return self._downloads([one for one in running if isinstance(one, dict)], [one for one in (queue or []) if isinstance(one, dict)],
                                   max(1, int(options.get("limit") or 10)))
        status = await self._get(config, ctx, "status_server", STATUS_SECONDS) or {}
        free = await self._get(config, ctx, "free_space", 300)
        return self._status(status, free if isinstance(free, (int, float)) else None)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(status: dict[str, Any], free: float | None) -> WidgetData:
        speed = float(status.get("speed") or 0)
        paused = bool(status.get("pause"))
        secondary: list[dict[str, Any]] = [
            {"label": "Active", "value": int(status.get("active") or 0)},
            {"label": "Queue", "value": int(status.get("queue") or 0)},
        ]
        if free is not None:
            secondary.append({"label": "Free", "value": human_bytes(free)})
        notice = "A captcha is waiting." if status.get("captcha") else ""
        return WidgetData(
            status="warn" if paused or notice else "ok",
            primary={"label": "Paused" if paused else "Speed", "value": "paused" if paused else f"{human_bytes(speed)}/s", "metric": "speed"},
            secondary=secondary,
            metrics=measured({"speed": speed}),
            meta={"notice": notice},
        )

    @staticmethod
    def _downloads(running: list[dict[str, Any]], queue: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        for one in running:
            parts = [str(one.get("package_name") or ""), str(one.get("format_size") or "")]
            if one.get("speed"):
                parts.append(f"{human_bytes(one['speed'])}/s")
            if _eta(one.get("eta")):
                parts.append(_eta(one.get("eta")))
            row: dict[str, Any] = {
                "id": f"file-{one.get('fid')}",
                "title": str(one.get("name") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": str(one.get("statusmsg") or ""),
                "status": "ok",
            }
            if isinstance(one.get("percent"), (int, float)):
                row["progress"] = float(one["percent"])
            rows.append(row)
        for package in queue:
            for link in package.get("links") or []:
                if not isinstance(link, dict) or link.get("status") not in FAILED:
                    continue
                rows.append({
                    "id": f"file-{link.get('fid')}",
                    "title": str(link.get("name") or "?"),
                    "subtitle": " · ".join(part for part in (str(package.get("name") or ""), str(link.get("error") or "")[:90]) if part),
                    "value": str(link.get("statusmsg") or "failed"),
                    "status": "bad",
                })
        return WidgetData(
            status="warn" if any(row["status"] == "bad" for row in rows) else "ok",
            items=rows[:limit],
            meta={"empty": "Nothing is downloading"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        speed = fake.walk("pyload-speed", tick, 2_000_000, 11_000_000)
        percent = (tick * 2) % 100
        running = [
            {"fid": 1, "name": "debian-13.1.0-amd64-DVD-1.iso", "package_name": "Debian 13", "format_size": "3.72 GiB",
             "speed": speed, "eta": int((100 - percent) * 40), "percent": percent, "statusmsg": "downloading"},
            {"fid": 2, "name": "ubuntu-24.04.3-desktop-amd64.iso", "package_name": "Ubuntu", "format_size": "5.79 GiB",
             "speed": 0, "eta": 0, "percent": 0, "statusmsg": "waiting"},
        ]
        queue = [{"name": "Archive", "links": [{"fid": 3, "name": "photos-2019.zip", "status": 8, "statusmsg": "failed", "error": "File not found"}]}]
        if widget_kind == "downloads":
            return self._downloads(running, queue if options.get("failed", True) else [], max(1, int(options.get("limit") or 10)))
        return self._status({"speed": speed, "active": 1, "queue": 3, "pause": False}, 812_000_000_000)


ADAPTER = PyLoadAdapter()
