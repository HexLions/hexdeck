"""Flood: the torrents of the client behind it, their speed, and pause or resume.

Checked against Flood 4.16.4 running locally on 16.11.2026 in front of Transmission 4.1.0, with one torrent complete
and seeding, one without its data, a stop and a start, a wrong password, an unknown user and a request without the
cookie. Flood speaks for rTorrent, qBittorrent, Transmission and Deluge alike, so one adapter serves all four.

- ``POST /api/auth/authenticate`` with ``{"username", "password"}`` sets the cookie ``jwt``; a wrong password and an
  unknown user both answer 401 ``{"message": "Failed login."}``. Without the cookie every route answers 401
  ``{"code": "FLOOD_UNAUTHORIZED"}``, which is also what an expired one looks like, so a refused read signs in once.
- ``GET /api/torrents`` answers ``{"id", "torrents": {hash: {...}}}`` with ``downRate`` and ``upRate`` in bytes a
  second, ``sizeBytes``, ``bytesDone``, ``percentComplete``, ``eta`` (-1 when unknown) and ``status``, a list of
  words: ``downloading``, ``seeding``, ``complete``, ``stopped``, ``checking``, ``error``, ``active``, ``inactive``.
  There is no total of the speeds; they are added up here.
- ``GET /api/client/connection-test`` says ``{"isConnected": false}`` when Flood answers but its client does not.
- ``POST /api/torrents/stop`` and ``/start`` take ``{"hashes": [...]}`` and answer 200 with no body.

⚠️ With Flood's ``--auth none`` there is no sign-in at all; the routes answer without a cookie, and the user name and
password may stay empty.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import AdapterError, AuthFailed, Context, Field, Unreachable, base_url, outbound_client
from .downloads_base import DownloadAdapter, QueueItem, Snapshot


class FloodAdapter(DownloadAdapter):
    kind = "flood"
    label = "Flood"
    description = "The torrents behind Flood, whichever client it drives, with speed and pause or resume."
    icon = "flood"
    docs_url = "https://github.com/jesec/flood"
    has_upload = True
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://flood:3000"),
        Field("username", "User name", help="Empty when Flood runs with --auth none."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )

    def _client(self, config: dict[str, Any], ctx: Context) -> httpx.AsyncClient:
        client = ctx.cache.get("flood_client")
        if client is None or client.is_closed:
            # One client per connection, and it keeps the jwt cookie the sign-in sets.
            client = outbound_client(base_url=base_url(config), verify=not config.get("insecure"), timeout=15, keep_cookies=True)
            ctx.cache["flood_client"] = client
        return client

    async def _send(self, config: dict[str, Any], ctx: Context, method: str, path: str, body: Any = None) -> httpx.Response:
        try:
            return await self._client(config, ctx).request(method, path, json=body)
        except httpx.HTTPError as error:
            raise Unreachable(f"Flood could not be reached: {error.__class__.__name__}.") from error

    async def _login(self, config: dict[str, Any], ctx: Context) -> None:
        response = await self._send(config, ctx, "POST", "/api/auth/authenticate",
                                    {"username": config.get("username") or "", "password": config.get("password") or ""})
        if response.status_code == 401:
            raise AuthFailed("Flood rejected the user name or password.", hint="The account you sign in to Flood with.")
        if response.status_code >= 400:
            raise AdapterError(f"Flood answered with HTTP {response.status_code} on the sign-in.", code="http_error")

    async def _call(self, config: dict[str, Any], ctx: Context, method: str, path: str, body: Any = None) -> Any:
        response = await self._send(config, ctx, method, path, body)
        if response.status_code == 401:
            await self._login(config, ctx)
            response = await self._send(config, ctx, method, path, body)
        if response.status_code == 401:
            raise AuthFailed("Flood refused the request after signing in.")
        if response.status_code >= 400:
            raise AdapterError(f"Flood answered with HTTP {response.status_code}.", code="http_error")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("Flood did not answer with JSON.", code="not_json",
                               hint="Check the URL; it should be the address of Flood itself.") from error

    async def _torrents(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        answer = await self._call(config, ctx, "GET", "/api/torrents")
        found = answer.get("torrents") if isinstance(answer, dict) else None
        if not isinstance(found, dict):
            raise AdapterError("Flood did not answer with its torrents.", code="not_json",
                               hint="Check the URL; it should be the address of Flood itself.")
        return [one for one in found.values() if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        torrents = await self._torrents(config, ctx)
        connected = await self._call(config, ctx, "GET", "/api/client/connection-test")
        if isinstance(connected, dict) and connected.get("isConnected") is False:
            raise AdapterError("Flood answers, but its torrent client does not.", code="client_unreachable",
                               hint="Check the client's settings in Flood under Settings, Client.")
        return f"Flood answers, {len(torrents)} torrents."

    @staticmethod
    def _snapshot(torrents: list[dict[str, Any]]) -> Snapshot:
        items = []
        remaining = 0.0
        for torrent in torrents:
            words = set(torrent.get("status") or [])
            if "complete" in words:
                continue
            size = float(torrent.get("sizeBytes") or 0)
            remaining += max(0.0, size - float(torrent.get("bytesDone") or 0))
            if "error" in words:
                state = f"error: {torrent.get('message')}" if torrent.get("message") else "error"
            elif "stopped" in words:
                state = "paused"
            elif "checking" in words:
                state = "checking"
            elif "inactive" in words:
                state = "stalled"
            else:
                state = "downloading"
            eta = torrent.get("eta")
            items.append(QueueItem(
                name=str(torrent.get("name") or "?"), progress=float(torrent.get("percentComplete") or 0), size_bytes=size,
                eta_seconds=float(eta) if isinstance(eta, (int, float)) and eta > 0 else None,
                state=state, identifier=str(torrent.get("hash") or ""),
            ))
        return Snapshot(
            download_bps=sum(float(torrent.get("downRate") or 0) for torrent in torrents),
            upload_bps=sum(float(torrent.get("upRate") or 0) for torrent in torrents),
            paused=bool(torrents) and all("stopped" in (torrent.get("status") or []) for torrent in torrents),
            remaining_bytes=remaining,
            items=items,
            total=len(items),
        )

    async def snapshot(self, config: dict[str, Any], ctx: Context) -> Snapshot:
        return self._snapshot(await self._torrents(config, ctx))

    async def pause(self, config: dict[str, Any], ctx: Context) -> None:
        # The torrents of a fresh read, never a hash from a card's options.
        hashes = [str(one["hash"]) for one in await self._torrents(config, ctx) if one.get("hash") and "stopped" not in (one.get("status") or [])]
        if hashes:
            await self._call(config, ctx, "POST", "/api/torrents/stop", {"hashes": hashes})

    async def close(self, config: dict[str, Any], ctx: Context) -> None:
        # The client holds the cookie; a Test button that left it open kept a socket per press.
        client = ctx.cache.pop("flood_client", None)
        if client is not None and not client.is_closed:
            await client.aclose()

    async def resume(self, config: dict[str, Any], ctx: Context) -> None:
        hashes = [str(one["hash"]) for one in await self._torrents(config, ctx) if one.get("hash") and "stopped" in (one.get("status") or [])]
        if hashes:
            await self._call(config, ctx, "POST", "/api/torrents/start", {"hashes": hashes})


ADAPTER = FloodAdapter()
