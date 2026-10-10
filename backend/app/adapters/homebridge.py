"""Homebridge: whether it runs, its child bridges, and what has an update.

The API of the Homebridge UI (homebridge-config-ui-x), read-only. It takes no
API key: the adapter logs in with a UI user at ``/api/auth/login`` and keeps
the token for the connection until it expires (eight hours by default) or is
refused. ``/api/status/homebridge`` says whether Homebridge runs,
``/api/status/homebridge/child-bridges`` lists the child bridges, and
``/api/plugins`` with ``/api/status/homebridge-version`` say what can be
updated.

⚠️ A child bridge's answer carries its HomeKit setup code (``pin`` and
``setupUri``), which pairs it with any phone. Only its name, plugin and
status are read.

⚠️ A user with two-factor login is answered 412 "2FA Code Required", and a
code from an authenticator cannot be kept in a connection. A separate user
without it is the way.

⚠️ The status only knows whether Homebridge runs when the UI runs it, under
hb-service, as in the official images and packages. A UI started on its own
next to Homebridge always says "down".

⚠️ With the UI's login turned off (``"auth": "none"``), the token comes from
``/api/auth/noauth``, and the user name and password are left empty.

Checked against Homebridge 2.4.0 with Homebridge UI 5.29.0 running locally
under hb-service on 2026-10-10, with one child bridge running and stopped.
"""

from __future__ import annotations

import time
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

#: Homebridge's and a child bridge's status as a colour and a word.
STATE = {"ok": ("ok", "running"), "pending": ("warn", "starting"), "down": ("bad", "down")}
STATUS_SECONDS = 30
PLUGINS_SECONDS = 1800


class HomebridgeAdapter(Adapter):
    kind = "homebridge"
    label = "Homebridge"
    category = "home"
    description = "Whether Homebridge runs, the state of each child bridge, and which plugins have an update."
    icon = "homebridge"
    docs_url = "https://github.com/homebridge/homebridge-config-ui-x/wiki"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://homebridge:8581"),
        Field("username", "Username",
              help="A user of the Homebridge UI without two-factor login. Empty when the UI's login is turned off."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="Whether Homebridge runs, how many child bridges are up, and how many updates wait.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("bridges_down",)),
        WidgetType(kind="bridges", label="Bridges", description="The main bridge and every child bridge with its plugin and state, the ones down first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("limit", "Entries", type="number", default=10),)),
        WidgetType(kind="updates", label="Updates", description="Homebridge and the plugins with a newer version, from the installed one to the latest.",
                   renderer="list", default_size=(3, 2), refresh_seconds=1800,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _token(self, config: dict[str, Any], ctx: Context, *, fresh: bool = False) -> str:
        kept = ctx.cache.get("homebridge_token")
        if kept and not fresh and kept[1] > time.time():
            return kept[0]
        username = str(config.get("username") or "").strip()
        if username:
            response = await ctx.request("POST", f"{base_url(config)}/api/auth/login", verify=not config.get("insecure"), auth_errors=False,
                                         json_body={"username": username, "password": str(config.get("password") or "")})
        else:
            response = await ctx.request("POST", f"{base_url(config)}/api/auth/noauth", verify=not config.get("insecure"), auth_errors=False)
        if response.status_code == 412:
            raise AuthFailed("This Homebridge user has two-factor login.",
                             hint="A separate user of the Homebridge UI without two-factor login, for the dashboard.")
        if response.status_code == 429:
            raise AdapterError("Homebridge has locked out this user for a few minutes after failed logins.", code="rate_limited")
        if response.status_code in (401, 403):
            raise AuthFailed("Homebridge refused the user name and password." if username else "Homebridge asks for a user name and password.",
                             hint="A user of the Homebridge UI, as on its login page.")
        if response.status_code >= 400:
            raise AdapterError(f"Homebridge answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of the Homebridge UI, usually port 8581.")
        try:
            answer = response.json()
            token = str(answer["access_token"])
        except (ValueError, KeyError, TypeError) as failure:
            raise AdapterError("Homebridge did not answer the login as its UI does.", code="not_json",
                               hint="The address of the Homebridge UI, usually port 8581.") from failure
        # A minute short of the expiry, so a token is never used on its last second.
        lifetime = float(answer.get("expires_in") or 28800)
        ctx.cache["homebridge_token"] = (token, time.time() + max(60.0, lifetime - 60))
        return token

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float, *, retry: bool = True) -> Any:
        token = await self._token(config, ctx)
        response = await ctx.request("GET", f"{base_url(config)}/api{path}", verify=not config.get("insecure"),
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code == 401 and retry:
            # Restarted UI, changed password or a token from before: log in again once.
            ctx.cache.pop("homebridge_token", None)
            return await self._get(config, ctx, path, cache, retry=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Homebridge refused the login's token.")
        if response.status_code >= 400:
            raise AdapterError(f"Homebridge answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Homebridge did not answer with JSON.", code="not_json") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        await self._token(config, ctx, fresh=True)
        status = await self._get(config, ctx, "/status/homebridge", 0) or {}
        version = await self._get(config, ctx, "/status/homebridge-version", 0) or {}
        return f"Homebridge {version.get('installedVersion') or '?'} answers, and is {STATE.get(status.get('status'), ('', 'in an unknown state'))[1]}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "updates":
            version = await self._get(config, ctx, "/status/homebridge-version", PLUGINS_SECONDS) or {}
            plugins = await self._get(config, ctx, "/plugins", PLUGINS_SECONDS) or []
            return self._updates(version, [one for one in plugins if isinstance(one, dict)], max(1, int(options.get("limit") or 8)))
        status = await self._get(config, ctx, "/status/homebridge", STATUS_SECONDS) or {}
        children = [one for one in (await self._get(config, ctx, "/status/homebridge/child-bridges", STATUS_SECONDS) or []) if isinstance(one, dict)]
        if widget_kind == "bridges":
            return self._bridges(status, children, max(1, int(options.get("limit") or 10)))
        version = await self._get(config, ctx, "/status/homebridge-version", PLUGINS_SECONDS) or {}
        plugins = await self._get(config, ctx, "/plugins", PLUGINS_SECONDS) or []
        return self._status(status, children, version, [one for one in plugins if isinstance(one, dict)])

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(status: dict[str, Any], children: list[dict[str, Any]], version: dict[str, Any], plugins: list[dict[str, Any]]) -> WidgetData:
        colour, word = STATE.get(str(status.get("status") or ""), ("unknown", "unknown"))
        up = sum(1 for one in children if one.get("status") == "ok")
        # A child bridge stopped by hand from the UI is not a fault.
        down = sum(1 for one in children if one.get("status") == "down" and not one.get("manuallyStopped"))
        updates = int(bool(version.get("updateAvailable"))) + sum(1 for one in plugins if one.get("updateAvailable"))
        secondary: list[dict[str, Any]] = []
        if children:
            secondary.append({"label": "Child bridges", "value": f"{up} / {len(children)}"})
        secondary.append({"label": "Updates", "value": updates})
        secondary.append({"label": "Version", "value": str(version.get("installedVersion") or "?")})
        return WidgetData(
            status="bad" if colour == "bad" else "warn" if down or colour == "warn" else "ok" if colour == "ok" else "unknown",
            primary={"label": "Homebridge", "value": word},
            secondary=secondary,
            metrics=measured({"bridges_down": float(down)}),
        )

    @staticmethod
    def _bridges(status: dict[str, Any], children: list[dict[str, Any]], limit: int) -> WidgetData:
        main_colour, main_word = STATE.get(str(status.get("status") or ""), ("unknown", "unknown"))
        rows = [{"id": "main", "title": "Homebridge", "subtitle": "main bridge", "value": main_word, "status": main_colour}]
        for child in children:
            colour, word = STATE.get(str(child.get("status") or ""), ("unknown", str(child.get("status") or "?")))
            if child.get("status") == "down" and child.get("manuallyStopped"):
                colour, word = "unknown", "stopped"
            rows.append({
                "id": str(child.get("username") or child.get("name") or ""),
                "title": str(child.get("name") or child.get("identifier") or "?"),
                "subtitle": str(child.get("plugin") or ""),
                "value": word,
                "status": colour,
            })
        order = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}
        rows[1:] = sorted(rows[1:], key=lambda row: (order.get(row["status"], 4), row["title"].lower()))
        return WidgetData(
            status="bad" if any(row["status"] == "bad" for row in rows) else "warn" if any(row["status"] == "warn" for row in rows) else "ok",
            items=rows[:limit],
        )

    @staticmethod
    def _updates(version: dict[str, Any], plugins: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        if version.get("updateAvailable"):
            rows.append({"id": "homebridge", "title": "Homebridge", "subtitle": "homebridge",
                         "value": f"{version.get('installedVersion')} → {version.get('latestVersion')}", "status": "warn"})
        for plugin in sorted(plugins, key=lambda one: str(one.get("displayName") or one.get("name") or "").lower()):
            if not plugin.get("updateAvailable"):
                continue
            rows.append({
                "id": str(plugin.get("name") or ""),
                "title": str(plugin.get("displayName") or plugin.get("name") or "?"),
                "subtitle": str(plugin.get("name") or ""),
                "value": f"{plugin.get('installedVersion')} → {plugin.get('latestVersion')}",
                "status": "warn",
            })
        return WidgetData(status="warn" if rows else "ok", items=rows[:limit], primary={"label": "Updates", "value": len(rows)},
                          meta={"empty": "Everything is up to date"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        restarting = fake.flicker("homebridge-ring", tick, 0.15)
        children = [
            {"name": "Ring", "plugin": "homebridge-ring", "username": "0E:00:00:00:00:01", "status": "pending" if restarting else "ok"},
            {"name": "Tuya", "plugin": "homebridge-tuya-platform", "username": "0E:00:00:00:00:02", "status": "ok"},
            {"name": "Camera FFmpeg", "plugin": "homebridge-camera-ffmpeg", "username": "0E:00:00:00:00:03", "status": "down", "manuallyStopped": True},
        ]
        version = {"installedVersion": "2.4.0", "latestVersion": "2.4.0", "updateAvailable": False}
        plugins = [
            {"name": "homebridge-ring", "displayName": "Ring", "installedVersion": "14.1.2", "latestVersion": "14.2.0", "updateAvailable": True},
            {"name": "homebridge-tuya-platform", "displayName": "Tuya", "installedVersion": "1.7.0", "latestVersion": "1.7.0", "updateAvailable": False},
        ]
        if widget_kind == "updates":
            return self._updates(version, plugins, max(1, int(options.get("limit") or 8)))
        if widget_kind == "bridges":
            return self._bridges({"status": "ok"}, children, max(1, int(options.get("limit") or 10)))
        return self._status({"status": "ok"}, children, version, plugins)


ADAPTER = HomebridgeAdapter()
