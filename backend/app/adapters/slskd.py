"""slskd: the Soulseek client, whether it is on the network, and its transfers.

API v0, read-only, with an API key as ``X-API-Key`` (a Bearer header with the
same key is refused): ``/api/v0/application`` for the server connection, the
shares and the version, ``/api/v0/transfers/downloads`` and ``/uploads`` for
the transfers.

⚠️ The transfer lists are grouped by user and then by directory, and Swagger
documents no shape for them; it was read from the controller in slskd's own
source at 0.26.0 (TransfersController, UserResponse, DirectoryResponse,
Transfer).

⚠️ A transfer's state is a set of flags written as one string, such as
"Completed, Succeeded", "Completed, Errored" or "Queued, Remotely". It is
split on the comma before it is read.

⚠️ Without Soulseek credentials slskd runs and answers, but is not on the
network: ``server.isLoggedIn`` is false and the card says so in red.

Checked against slskd 0.26.0 running locally on 2026-10-10, verified by its
digest, without a Soulseek account; the transfer shapes are the source's.
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

STATUS_SECONDS = 30
TRANSFERS_SECONDS = 15


def states(transfer: dict[str, Any]) -> set[str]:
    return {part.strip() for part in str(transfer.get("state") or "").split(",") if part.strip()}


def flatten(groups: Any) -> list[dict[str, Any]]:
    """The transfers out of their users and directories."""
    found = []
    for user in groups if isinstance(groups, list) else []:
        for directory in (user or {}).get("directories") or []:
            for transfer in (directory or {}).get("files") or []:
                if isinstance(transfer, dict):
                    found.append(transfer)
    return found


class SlskdAdapter(Adapter):
    kind = "slskd"
    label = "slskd"
    category = "downloads"
    description = "The Soulseek client slskd: whether it is on the network, what it shares, and its downloads and uploads."
    icon = "slskd"
    docs_url = "https://github.com/slskd/slskd/blob/master/docs/config.md"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://slskd:5030"),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="An API key from slskd's configuration, under web > authentication > api_keys."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="Whether slskd is logged in to Soulseek, what it shares, and how many transfers are running.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("downloading", "uploading")),
        WidgetType(kind="downloads", label="Downloads", description="The downloads running and waiting, with their progress, and the ones that failed.",
                   renderer="list", default_size=(4, 3), refresh_seconds=30,
                   options=(Field("uploads", "Uploads instead", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=10))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/api/v0{path}", verify=not config.get("insecure"),
                                     headers={"X-API-Key": str(config.get("api_key") or "").strip(), "Accept": "application/json"},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("slskd refused the API key.",
                             hint="A key listed under web > authentication > api_keys in slskd's configuration, sent as it is written there.")
        if response.status_code >= 400:
            raise AdapterError(f"slskd answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of slskd's web interface, usually port 5030.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("slskd did not answer with JSON.", code="not_json") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        app = await self._get(config, ctx, "/application", 0) or {}
        server = app.get("server") or {}
        return f"slskd {(app.get('version') or {}).get('current') or '?'} answers, {'logged in to Soulseek' if server.get('isLoggedIn') else 'not on the Soulseek network'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "downloads":
            uploads = bool(options.get("uploads"))
            groups = await self._get(config, ctx, "/transfers/uploads" if uploads else "/transfers/downloads", TRANSFERS_SECONDS)
            return self._transfers(flatten(groups), uploads, max(1, int(options.get("limit") or 10)))
        app = await self._get(config, ctx, "/application", STATUS_SECONDS) or {}
        downloads = flatten(await self._get(config, ctx, "/transfers/downloads", TRANSFERS_SECONDS))
        uploads = flatten(await self._get(config, ctx, "/transfers/uploads", TRANSFERS_SECONDS))
        return self._status(app, downloads, uploads)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(app: dict[str, Any], downloads: list[dict[str, Any]], uploads: list[dict[str, Any]]) -> WidgetData:
        server = app.get("server") or {}
        shares = app.get("shares") or {}
        version = app.get("version") or {}
        running_down = sum(1 for one in downloads if "InProgress" in states(one))
        running_up = sum(1 for one in uploads if "InProgress" in states(one))
        secondary: list[dict[str, Any]] = [
            {"label": "Downloading", "value": running_down, "metric": "downloading"},
            {"label": "Uploading", "value": running_up, "metric": "uploading"},
        ]
        if isinstance(shares.get("files"), int):
            secondary.append({"label": "Shared files", "value": shares["files"]})
        if version.get("isUpdateAvailable"):
            secondary.append({"label": "Update", "value": str(version.get("latest") or "")})
        logged_in = bool(server.get("isLoggedIn"))
        return WidgetData(
            status="ok" if logged_in else "warn" if server.get("isConnecting") or server.get("isLoggingIn") else "bad",
            primary={"label": "Soulseek", "value": "logged in" if logged_in else "connecting" if server.get("isConnecting") else "offline"},
            secondary=secondary,
            metrics=measured({"downloading": float(running_down), "uploading": float(running_up)}),
            meta={"notice": "Shares are being scanned." if shares.get("scanning") else ""},
        )

    @staticmethod
    def _transfers(transfers: list[dict[str, Any]], uploads: bool, limit: int) -> WidgetData:
        def rank(one: dict[str, Any]) -> int:
            flags = states(one)
            if "InProgress" in flags:
                return 0
            if flags & {"Errored", "TimedOut", "Rejected", "Aborted"}:
                return 1
            if flags & {"Queued", "Requested", "Initializing"}:
                return 2
            return 3

        rows = []
        for one in sorted(transfers, key=lambda one: (rank(one), str(one.get("requestedAt") or "")), reverse=False):
            flags = states(one)
            failed = bool(flags & {"Errored", "TimedOut", "Rejected", "Aborted"})
            done = "Succeeded" in flags
            name = str(one.get("filename") or "?").replace("\\", "/").rsplit("/", 1)[-1]
            speed = one.get("averageSpeed")
            parts = [str(one.get("username") or ""), human_bytes(one.get("size") or 0)]
            if "InProgress" in flags and isinstance(speed, (int, float)) and speed > 0:
                parts.append(f"{human_bytes(speed)}/s")
            if failed and one.get("exception"):
                parts.append(str(one["exception"])[:60])
            row: dict[str, Any] = {
                "id": one.get("id"),
                "title": name,
                "subtitle": " · ".join(part for part in parts if part),
                "value": ", ".join(sorted(flags - {"Completed"})) or "?",
                "status": "bad" if failed else "ok" if done or "InProgress" in flags else "unknown",
            }
            if isinstance(one.get("percentComplete"), (int, float)) and not done:
                row["progress"] = round(float(one["percentComplete"]), 1)
            rows.append(row)
        what = "upload" if uploads else "download"
        return WidgetData(
            status="warn" if any(row["status"] == "bad" for row in rows) else "ok",
            items=rows[:limit],
            meta={"empty": f"No {what} yet"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        progress = (tick * 3) % 100
        downloads = [
            {"id": "a", "username": "vinylhead", "filename": "Music\\Album (1977)\\01 - Track.flac", "size": 31_457_280,
             "state": "InProgress", "percentComplete": progress, "averageSpeed": 812_000},
            {"id": "b", "username": "vinylhead", "filename": "Music\\Album (1977)\\02 - Track.flac", "size": 28_311_552, "state": "Queued, Remotely"},
            {"id": "c", "username": "jazzarchive", "filename": "Jazz\\Live (1959)\\03.flac", "size": 40_000_000,
             "state": "Completed, Errored", "exception": "Transfer rejected: File not shared."},
            {"id": "d", "username": "jazzarchive", "filename": "Jazz\\Live (1959)\\01.flac", "size": 38_000_000, "state": "Completed, Succeeded"},
        ]
        if widget_kind == "downloads":
            return self._transfers(downloads, bool(options.get("uploads")), max(1, int(options.get("limit") or 10)))
        return self._status({"server": {"isLoggedIn": not fake.flicker("slskd-off", tick, 0.1)},
                             "shares": {"files": 12_480, "scanning": False}, "version": {"current": "0.26.0", "isUpdateAvailable": False}},
                            downloads, [{"state": "InProgress"}])


ADAPTER = SlskdAdapter()
