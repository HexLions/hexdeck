"""SFTPGo: who is connected now, how full each user's quota is, and which addresses are banned.

Checked against SFTPGo v2.7.6 running locally on 20.10.2026 with SFTP on,
three users (one disabled, one with a quota and a 3 MB upload), an open SFTP
session, failed sign-ins, an admin API key, a key of an admin allowed only to
see the status and the connections, and a wrong key.

⚠️ The API key goes in ``X-SFTPGO-API-KEY``, and only works for an admin
whose filters have ``allow_api_key_auth`` on. A wrong key is 400 ``{"error":
"the provided api key is not valid"}``, not 401; no key at all is 401.

⚠️ A wrong key counts against the caller in SFTPGo's defender, with the
score of an invalid sign-in (2 by default, against a threshold of 15). A card
left with a wrong key got this machine banned within minutes, and from then
on every call, the right key's included, answered 403 ``{"error": "your IP
address is blocked"}`` for the ban time (30 minutes by default). The card
says which of the two it is.

⚠️ An admin without ``view_users`` or ``view_defender`` gets 403
``{"message": "Forbidden"}`` for those lists; the status card leaves out
what it may not count instead of failing.

⚠️ Times are milliseconds since the epoch. ``used_quota_size`` and
``last_login`` are left out of a user who never uploaded or signed in, and a
``quota_size`` of 0 means no limit.
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
    human_bytes,
)

BANNED = "SFTPGo has banned this machine's address in its defender."
SERVICES = (("ssh", "SFTP"), ("ftp", "FTP"), ("webdav", "WebDAV"))


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _since(milliseconds: Any) -> str:
    value = _number(milliseconds)
    return ago(value / 1000) if value else ""


class SftpGoAdapter(Adapter):
    kind = "sftpgo"
    label = "SFTPGo"
    category = "nas"
    description = "SFTPGo file server: who is connected now, how full each user's quota is, and banned addresses."
    icon = "lucide:server"
    beta = True
    docs_url = "https://docs.sftpgo.com/latest/rest-api/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://sftpgo:8080",
              help="The address of SFTPGo's web admin, without /api/v2."),
        Field("api_key", "API key", secret=True, required=True,
              help="An admin API key; the admin needs API key authentication allowed in its settings."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Server", description="Connections now, enabled users, the services running and banned addresses.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("connections",)),
        WidgetType(kind="connections", label="Connections", description="Who is connected, over which protocol, from where and since when.",
                   renderer="list", default_size=(3, 3), refresh_seconds=30,
                   options=(Field("limit", "Entries", type="number", default=10),)),
        WidgetType(kind="quotas", label="Quotas", description="The users with a quota, fullest first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 30, optional: bool = False) -> Any:
        """The answer, or None for a list the admin may not see when ``optional``."""
        response = await ctx.request("GET", f"{base_url(config)}/api/v2{path}",
                                     headers={"X-SFTPGO-API-KEY": str(config.get("api_key") or "").strip()},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        text = response.text[:300]
        if response.status_code == 403 and "blocked" in text:
            raise AuthFailed(BANNED, hint="It lifts after the ban time (30 minutes by default), or remove it under Defender in SFTPGo's admin.")
        if response.status_code in (400, 401) and ("api key" in text.lower() or "token" in text.lower()):
            raise AuthFailed("SFTPGo refused the API key.",
                             hint="A wrong key counts against this machine in SFTPGo's defender; fix it before the card asks again and again.")
        if response.status_code == 403:
            if optional:
                return None
            raise AuthFailed("The admin of this API key may not see this.",
                             hint="Give the admin the permissions to view the status, connections and users.")
        if response.status_code >= 400:
            raise AdapterError(f"SFTPGo answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is the web admin's address, without /api/v2.")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way SFTPGo does.", code="not_sftpgo",
                               hint="Check the URL; it is the web admin's address, without /api/v2.") from error

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        version = await self._get(config, ctx, "/version", cache=0)
        if not isinstance(version, dict) or "version" not in version:
            raise AdapterError("This address answers, but not the way SFTPGo does.", code="not_sftpgo")
        connections = await self._get(config, ctx, "/connections", cache=0)
        count = len(connections) if isinstance(connections, list) else 0
        return f"SFTPGo {version['version']} answers with {count} connection{'' if count == 1 else 's'} open."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        limit = max(1, int(options.get("limit") or 10))
        if widget_kind == "connections":
            return self._connections(await self._get(config, ctx, "/connections", cache=15) or [], limit)
        if widget_kind == "quotas":
            return self._quotas(await self._get(config, ctx, "/users?limit=500", cache=300) or [], limit)
        status = await self._get(config, ctx, "/status", cache=60)
        connections = await self._get(config, ctx, "/connections", cache=15)
        users = await self._get(config, ctx, "/users?limit=500", cache=300, optional=True)
        banned = await self._get(config, ctx, "/defender/hosts", cache=60, optional=True)
        return self._status(status or {}, connections or [], users, banned)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(status: dict[str, Any], connections: list[Any], users: list[Any] | None, hosts: list[Any] | None) -> WidgetData:
        running = [name for key, name in SERVICES if (status.get(key) or {}).get("is_active")]
        provider = status.get("data_provider") or {}
        secondary: list[dict[str, Any]] = []
        if users is not None:
            secondary.append({"label": "Users", "value": sum(1 for one in users if isinstance(one, dict) and one.get("status") == 1)})
        secondary.append({"label": "Services", "value": ", ".join(running) or "none"})
        banned = [one for one in hosts or [] if isinstance(one, dict) and one.get("ban_time")]
        if hosts is not None:
            secondary.append({"label": "Banned", "value": len(banned)})
        if provider.get("error"):
            state, reason = "bad", f"Data provider: {provider['error']}"
        elif not running:
            state, reason = "warn", "No file service is running."
        else:
            state, reason = ("warn", "") if banned else ("ok", "")
        meta = {"status_reason": reason} if reason else {}
        return WidgetData(status=state, primary={"label": "Connections", "value": len(connections)},
                          secondary=secondary, meta=meta, metrics={"connections": float(len(connections))})

    @staticmethod
    def _connections(connections: list[Any], limit: int) -> WidgetData:
        rows = sorted((one for one in connections if isinstance(one, dict)), key=lambda one: -(_number(one.get("connection_time")) or 0))
        items = [{
            "title": str(one.get("username") or "?"),
            "subtitle": " · ".join(part for part in (str(one.get("protocol") or ""), str(one.get("remote_address") or "").rsplit(":", 1)[0]) if part),
            "status": "ok",
            "value": _since(one.get("connection_time")),
        } for one in rows[:limit]]
        return WidgetData(status="ok", items=items, secondary=[{"label": "Connections", "value": len(rows)}],
                          meta={"empty": "Nobody is connected."})

    @staticmethod
    def _quotas(users: list[Any], limit: int) -> WidgetData:
        rows = []
        for one in users:
            if not isinstance(one, dict):
                continue
            size = _number(one.get("quota_size")) or 0
            if size <= 0:
                continue
            used = _number(one.get("used_quota_size")) or 0
            rows.append((used / size, one, used, size))
        rows.sort(key=lambda row: -row[0])
        items = [{
            "title": str(one.get("username") or "?"),
            "subtitle": f"{human_bytes(used)} of {human_bytes(size)}" + ("" if one.get("status") == 1 else " · disabled"),
            "status": "bad" if share >= 0.98 else "warn" if share >= 0.9 else "ok",
            "value": f"{round(share * 100)}%",
            "progress": round(min(share, 1.0) * 100),
        } for share, one, used, size in rows[:limit]]
        return WidgetData(status="warn" if any(item["status"] != "ok" for item in items) else "ok", items=items,
                          meta={"empty": "No user has a quota."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now_ms = 1_792_492_570_000 + tick * 15_000
        connections = [
            {"username": "scanner", "protocol": "FTP", "remote_address": "192.168.1.40:51022", "connection_time": now_ms - 3_600_000},
            {"username": "anna", "protocol": "SFTP", "remote_address": "192.168.1.23:35974", "connection_time": now_ms - 240_000},
        ][: 1 + tick % 2]
        users = [
            {"username": "anna", "status": 1, "quota_size": 50 * 1024**3, "used_quota_size": fake.walk("sftpgo-anna", tick, 31, 33) * 1024**3},
            {"username": "backup", "status": 1, "quota_size": 500 * 1024**3, "used_quota_size": 462 * 1024**3},
            {"username": "scanner", "status": 1, "quota_size": 0},
            {"username": "old-camera", "status": 0, "quota_size": 10 * 1024**3, "used_quota_size": 2 * 1024**3},
        ]
        if widget_kind == "connections":
            return self._connections(connections, max(1, int(options.get("limit") or 10)))
        if widget_kind == "quotas":
            return self._quotas(users, max(1, int(options.get("limit") or 10)))
        status = {"ssh": {"is_active": True}, "ftp": {"is_active": True}, "webdav": {"is_active": False},
                  "data_provider": {"driver": "sqlite", "is_active": True, "error": ""}}
        return self._status(status, connections, users, [{"ip": "203.0.113.7", "ban_time": "2026-10-20T11:05:17Z"}] if tick % 5 == 0 else [])


ADAPTER = SftpGoAdapter()
