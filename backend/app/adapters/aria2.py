"""aria2: the download utility, over its JSON-RPC interface.

aria2 runs as a daemon with ``--enable-rpc`` and answers JSON-RPC on
``/jsonrpc`` (port 6800 unless changed). ``aria2.getGlobalStat`` gives the
speeds and the counts; ``tellActive``, ``tellWaiting`` and ``tellStopped``
the downloads, each with only the keys asked for.

⚠️ The secret is not a header: it is the first parameter of every call, as
``"token:<secret>"``. A wrong or missing one is answered with HTTP 400 and
``{"error": {"code": 1, "message": "Unauthorized"}}``, not with 401.

⚠️ Every number is a string in aria2's answers, lengths and speeds alike.

⚠️ A download that has not started yet, or failed before it learned a name,
has an empty path; its name then comes from its address. A torrent's name
is in ``bittorrent.info.name``.

Checked against aria2 1.37.0 running locally on 2026-10-13, with a secret,
two downloads running, one paused, one waiting, one finished and one that
failed with "Resource not found", and a wrong secret.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit

from .base import AdapterError, AuthFailed, Context, Field, base_url
from .downloads_base import DownloadAdapter, QueueItem, Snapshot

KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "errorMessage", "files", "bittorrent"]
#: How many waiting and failed downloads are read at most.
QUEUE_WINDOW = 100


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def name_of(download: dict[str, Any]) -> str:
    """What a download is called: the torrent's name, the file's, or the end of its address."""
    info = (download.get("bittorrent") or {}).get("info") or {}
    if info.get("name"):
        return str(info["name"])
    files = [one for one in download.get("files") or [] if isinstance(one, dict)]
    for file in files:
        path = str(file.get("path") or "")
        if path:
            return path.rsplit("/", 1)[-1]
    for file in files:
        for uri in file.get("uris") or []:
            address = str((uri or {}).get("uri") or "")
            tail = unquote(urlsplit(address).path.rsplit("/", 1)[-1])
            if tail:
                return tail
    return str(download.get("gid") or "?")


def rpc_url(config: dict[str, Any]) -> str:
    url = base_url(config)
    return url if url.endswith("/jsonrpc") else f"{url}/jsonrpc"


class Aria2Adapter(DownloadAdapter):
    kind = "aria2"
    label = "aria2"
    description = "The downloads of aria2 over JSON-RPC: speed, progress, what failed, and pause or resume."
    icon = "lucide:download"
    docs_url = "https://aria2.github.io/manual/en/html/aria2c.html#rpc-interface"
    has_upload = True
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://aria2:6800",
              help="The RPC address, /jsonrpc is added when it is missing. aria2 has to run with --enable-rpc, and --rpc-listen-all to be reached from another machine."),
        Field("secret", "Secret", type="password", secret=True, help="The --rpc-secret aria2 was started with, if any."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )

    async def _call(self, config: dict[str, Any], ctx: Context, method: str, *params: Any) -> Any:
        secret = str(config.get("secret") or "")
        arguments = ([f"token:{secret}"] if secret else []) + list(params)
        response = await ctx.request("POST", rpc_url(config), json_body={"jsonrpc": "2.0", "id": "hexdeck", "method": method, "params": arguments},
                                     verify=not config.get("insecure"), auth_errors=False)
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError(f"aria2 answered with HTTP {response.status_code} and no JSON.", code="not_json",
                               hint="The RPC address of aria2, usually port 6800.") from failure
        error = answer.get("error") if isinstance(answer, dict) else None
        if isinstance(error, dict):
            if str(error.get("message")) == "Unauthorized":
                raise AuthFailed("aria2 refused the secret." if secret else "aria2 asks for its secret.",
                                 hint="The value of --rpc-secret, without the token: in front.")
            raise AdapterError(f"aria2 answered: {error.get('message') or 'an error'}.", code="rpc_error")
        if response.status_code >= 400 or not isinstance(answer, dict):
            raise AdapterError(f"aria2 answered with HTTP {response.status_code}.", code="http_error",
                               hint="The RPC address of aria2, usually port 6800.")
        return answer.get("result")

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        version = await self._call(config, ctx, "aria2.getVersion") or {}
        stat = await self._call(config, ctx, "aria2.getGlobalStat") or {}
        running = int(_number(stat.get("numActive")))
        return f"aria2 {version.get('version') or ''} answers, {running} download{'s' if running != 1 else ''} running.".replace("aria2  ", "aria2 ")

    async def snapshot(self, config: dict[str, Any], ctx: Context) -> Snapshot:
        stat = await self._call(config, ctx, "aria2.getGlobalStat") or {}
        active = await self._call(config, ctx, "aria2.tellActive", KEYS) or []
        waiting = await self._call(config, ctx, "aria2.tellWaiting", 0, QUEUE_WINDOW, KEYS) or []
        stopped = await self._call(config, ctx, "aria2.tellStopped", 0, QUEUE_WINDOW, KEYS) or []
        items = []
        remaining = 0.0
        for download in [*active, *waiting, *(one for one in stopped if one.get("status") == "error")]:
            total, done, speed = (_number(download.get(key)) for key in ("totalLength", "completedLength", "downloadSpeed"))
            status = str(download.get("status") or "")
            if status != "error":
                remaining += max(total - done, 0.0)
            state = {"active": "downloading", "waiting": "queued", "paused": "paused", "error": "failed"}.get(status, status)
            items.append(QueueItem(
                name=name_of(download), progress=done / total * 100 if total else 0.0, size_bytes=total or None,
                eta_seconds=(total - done) / speed if speed and total > done else None,
                state=f"failed: {download['errorMessage']}" if status == "error" and download.get("errorMessage") else state,
                identifier=str(download.get("gid") or ""),
            ))
        queued = [one for one in items if not one.state.startswith("failed")]
        return Snapshot(
            download_bps=_number(stat.get("downloadSpeed")),
            upload_bps=_number(stat.get("uploadSpeed")),
            paused=bool(queued) and all(one.state == "paused" for one in queued),
            remaining_bytes=remaining,
            items=items,
            total=len(queued),
        )

    async def pause(self, config: dict[str, Any], ctx: Context) -> None:
        await self._call(config, ctx, "aria2.pauseAll")

    async def resume(self, config: dict[str, Any], ctx: Context) -> None:
        await self._call(config, ctx, "aria2.unpauseAll")


ADAPTER = Aria2Adapter()
