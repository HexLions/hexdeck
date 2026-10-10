"""Caddy: the sites it serves and the upstreams it proxies to.

The admin API, read-only: ``/config/apps/http/servers`` for the sites and
what handles each, ``/reverse_proxy/upstreams`` for the upstreams with their
active requests and the failures passive health checks remember.

⚠️ The admin API has no sign-in and can rewrite the whole configuration. It
listens on localhost:2019 by default, which another container cannot reach;
``admin 0.0.0.0:2019`` in the Caddyfile opens it to every host on the
network. Keep it on an internal network, or put it behind a proxy with basic
authentication, which the user name and password are for. This adapter only
ever sends GET.

⚠️ An upstream is not "up" or "down" in that answer: Caddy reports the
requests it is handling and the failures it remembers, and its own
documentation says availability has to be judged against the proxy's
configuration. The cards say what was reported and colour an upstream with
remembered failures, without calling it down.

⚠️ A request that carries an Origin header is refused with 403 when the
admin API listens beyond localhost. HexDeck asks from the server and sends
none.

Read from Caddy's admin API documentation (caddyserver.com/docs/api) and
checked against Caddy v2.11.7 running locally on 2026-10-09.
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
    measured,
)

CONFIG_SECONDS = 300
UPSTREAMS_SECONDS = 30


def _handlers(routes: Any) -> list[dict[str, Any]]:
    """Every handler under a list of routes, subroutes followed."""
    found: list[dict[str, Any]] = []
    for route in routes if isinstance(routes, list) else []:
        for handler in (route or {}).get("handle") or []:
            if not isinstance(handler, dict):
                continue
            found.append(handler)
            if handler.get("handler") == "subroute":
                found.extend(_handlers(handler.get("routes")))
    return found


def sites(servers: Any) -> list[dict[str, Any]]:
    """The hosts each server answers for, and what answers them.

    One entry per host. ``kind`` is the first handler that does the work: a
    reverse proxy with its upstreams' dial addresses, a file server, a static
    response or a redirect. A route without a host matcher is the catch-all
    and is named after its server's listen addresses.
    """
    found: list[dict[str, Any]] = []
    for name, server in (servers or {}).items() if isinstance(servers, dict) else []:
        listen = [str(one) for one in (server or {}).get("listen") or []]
        for route in (server or {}).get("routes") or []:
            if not isinstance(route, dict):
                continue
            hosts = [str(host) for match in route.get("match") or [] if isinstance(match, dict) for host in match.get("host") or []]
            handlers = _handlers([route])
            proxy = next((one for one in handlers if one.get("handler") == "reverse_proxy"), None)
            if proxy is not None:
                kind = "proxy"
                dials = [str(up.get("dial")) for up in proxy.get("upstreams") or [] if isinstance(up, dict) and up.get("dial")]
            else:
                worker = next((one for one in handlers if one.get("handler") not in ("subroute", "vars", "headers", "encode")), {})
                kind = {"file_server": "files", "static_response": "static"}.get(str(worker.get("handler") or ""), str(worker.get("handler") or "?"))
                dials = []
            for host in hosts or [f"{name} ({', '.join(listen) or 'no listen'})"]:
                found.append({"host": host, "server": str(name), "listen": listen, "kind": kind, "upstreams": dials})
    return found


class CaddyAdapter(Adapter):
    kind = "caddy"
    label = "Caddy"
    category = "network"
    description = "The sites Caddy serves, what answers each, and the upstreams it proxies to with their failures."
    icon = "caddy"
    docs_url = "https://caddyserver.com/docs/api"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://caddy:2019",
              help="The admin API, on port 2019. It listens on localhost unless the Caddyfile says admin 0.0.0.0:2019; keep it on an internal network."),
        Field("username", "User name", help="Only behind basic authentication."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Overview", description="How many sites and upstreams there are, and how many upstreams remember failures.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("failing", "active")),
        WidgetType(kind="sites", label="Sites", description="Every host Caddy answers for, with what answers it: a proxy and its upstreams, files or a fixed response.",
                   renderer="list", default_size=(4, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=12),)),
        WidgetType(kind="upstreams", label="Upstreams", description="The proxied backends with their active requests and the failures passive health checks remember.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60, metrics=("failing",),
                   options=(Field("limit", "Entries", type="number", default=12),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        auth = (str(config["username"]), str(config.get("password") or "")) if config.get("username") else None
        response = await ctx.request("GET", f"{base_url(config)}{path}", auth=auth, headers={"Accept": "application/json"},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Caddy's admin API, or the proxy in front of it, refused the request.",
                             hint="Behind a proxy, the user name and password of its basic authentication. Caddy itself refuses "
                                  "a request with an Origin header it does not expect.")
        if response.status_code == 404:
            # An empty configuration has no http app, and its path answers 404.
            return None
        if response.status_code >= 400:
            raise AdapterError(f"Caddy answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Caddy did not answer with JSON.", code="not_json",
                               hint="The address is the admin API, usually port 2019, not a site Caddy serves.") from failure

    async def _sites(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        return sites(await self._get(config, ctx, "/config/apps/http/servers", CONFIG_SECONDS))

    async def _upstreams(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/reverse_proxy/upstreams", UPSTREAMS_SECONDS)
        return [one for one in (answer if isinstance(answer, list) else []) if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        found = await self._sites(config, ctx)
        upstreams = await self._upstreams(config, ctx)
        return f"Caddy answers with {len(found)} site{'s' if len(found) != 1 else ''} and {len(upstreams)} upstream{'s' if len(upstreams) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "upstreams":
            return self._upstream_list(await self._upstreams(config, ctx), await self._sites(config, ctx), options)
        if widget_kind == "sites":
            return self._site_list(await self._sites(config, ctx), await self._upstreams(config, ctx), options)
        return self._summary(await self._sites(config, ctx), await self._upstreams(config, ctx))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _fails(upstreams: list[dict[str, Any]]) -> dict[str, int]:
        return {str(one.get("address")): int(one.get("fails") or 0) for one in upstreams}

    @classmethod
    def _summary(cls, found: list[dict[str, Any]], upstreams: list[dict[str, Any]]) -> WidgetData:
        failing = sum(1 for one in upstreams if int(one.get("fails") or 0) > 0)
        active = sum(int(one.get("num_requests") or 0) for one in upstreams)
        secondary: list[dict[str, Any]] = [{"label": "Upstreams", "value": len(upstreams)}]
        if failing:
            secondary.append({"label": "With failures", "value": failing, "metric": "failing"})
        secondary.append({"label": "Active requests", "value": active, "metric": "active"})
        return WidgetData(
            status="warn" if failing else "ok",
            primary={"label": "Sites", "value": len(found)},
            secondary=secondary,
            metrics=measured({"failing": float(failing), "active": float(active)}),
        )

    @classmethod
    def _site_list(cls, found: list[dict[str, Any]], upstreams: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        fails = cls._fails(upstreams)
        rows = []
        for site in found:
            failing = [dial for dial in site["upstreams"] if fails.get(dial, 0) > 0]
            if site["kind"] == "proxy":
                said = ", ".join(site["upstreams"][:3]) + (f" +{len(site['upstreams']) - 3}" if len(site["upstreams"]) > 3 else "")
                value = f"{len(failing)} of {len(site['upstreams'])} failing" if failing else "proxy"
            else:
                said, value = "", site["kind"]
            rows.append({
                "id": f"{site['server']}:{site['host']}",
                "title": site["host"],
                "subtitle": said,
                "value": value,
                # Every upstream failing is worth red; some of them, yellow.
                "status": ("bad" if failing and len(failing) == len(site["upstreams"]) else "warn") if failing else "ok",
            })
        order = {"bad": 0, "warn": 1, "ok": 2}
        rows.sort(key=lambda row: (order.get(row["status"], 3), row["title"]))
        return WidgetData(
            status="bad" if any(row["status"] == "bad" for row in rows) else "warn" if any(row["status"] == "warn" for row in rows) else "ok",
            items=rows[: max(1, int(options.get("limit") or 12))],
            primary={"label": "Sites", "value": len(rows)},
            meta={"empty": "Caddy serves no site over HTTP"},
        )

    @classmethod
    def _upstream_list(cls, upstreams: list[dict[str, Any]], found: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        hosts: dict[str, list[str]] = {}
        for site in found:
            for dial in site["upstreams"]:
                hosts.setdefault(dial, []).append(site["host"])
        rows = []
        for one in upstreams:
            address = str(one.get("address") or "?")
            failed = int(one.get("fails") or 0)
            active = int(one.get("num_requests") or 0)
            names = hosts.get(address, [])
            rows.append({
                "id": address,
                "title": address,
                "subtitle": ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else ""),
                "value": f"{failed} failed" if failed else f"{active} active",
                "status": "warn" if failed else "ok",
            })
        rows.sort(key=lambda row: (row["status"] != "warn", row["title"]))
        failing = sum(1 for row in rows if row["status"] == "warn")
        return WidgetData(
            status="warn" if failing else "ok",
            items=rows[: max(1, int(options.get("limit") or 12))],
            primary={"label": "Upstreams", "value": len(rows)},
            metrics=measured({"failing": float(failing)}),
            meta={"empty": "Caddy proxies to no upstream"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        jellyfin_fails = 2 if fake.flicker("caddy-jf", tick, 0.3) else 0
        servers = {"srv0": {"listen": [":443"], "routes": [
            {"match": [{"host": ["jellyfin.example.com"]}], "handle": [{"handler": "subroute", "routes": [
                {"handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "jellyfin:8096"}]}]}]}]},
            {"match": [{"host": ["cloud.example.com"]}], "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "nextcloud:80"}]}]},
            {"match": [{"host": ["ha.example.com"]}], "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "homeassistant:8123"}]}]},
            {"match": [{"host": ["hexdeck.example.com"]}], "handle": [{"handler": "reverse_proxy", "upstreams": [{"dial": "hexdeck:8000"}]}]},
            {"match": [{"host": ["status.example.com"]}], "handle": [{"handler": "file_server"}]},
        ]}}
        upstreams = [
            {"address": "jellyfin:8096", "num_requests": int(fake.walk("caddy-jf-r", tick, 0, 6)), "fails": jellyfin_fails},
            {"address": "nextcloud:80", "num_requests": int(fake.walk("caddy-nc-r", tick, 0, 3)), "fails": 0},
            {"address": "homeassistant:8123", "num_requests": int(fake.walk("caddy-ha-r", tick, 1, 4)), "fails": 0},
            {"address": "hexdeck:8000", "num_requests": 1, "fails": 0},
        ]
        found = sites(servers)
        if widget_kind == "sites":
            return self._site_list(found, upstreams, options)
        if widget_kind == "upstreams":
            return self._upstream_list(upstreams, found, options)
        return self._summary(found, upstreams)


ADAPTER = CaddyAdapter()
