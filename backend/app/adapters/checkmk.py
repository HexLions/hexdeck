"""Checkmk: how many hosts and services are fine, and the problems, through its REST API.

Written from Checkmk 2.5.0p20's source (``cmk/gui/openapi/endpoints/version``, ``.../host``, ``.../service`` and
``cmk/gui/auth.py``), compared with 2.4.0. Not yet run against a site.

- The API lives under the site: ``<address>/<site>/check_mk/api/1.0``. ``GET /version`` names the site, the edition
  and ``versions.checkmk``.
- A user signs in with ``Authorization: Bearer <user> <secret>``, an automation user with its automation secret.
  Wrong credentials answer 401.
- Hosts and services are listed with ``POST /domain-types/host/collections/all`` and
  ``.../service/collections/all`` and a JSON body: ``columns`` and an optional ``query`` in Checkmk's query language
  (``{"op": "!=", "left": "state", "right": "0"}``). Each row comes back as ``value[].extensions``. Checkmk 2.4 added
  the POST; the GET with the same parameters in the address is deprecated and leaves in 2.5, so this adapter needs
  Checkmk 2.4 or newer. ``extensions.unreachable_sites`` names the sites of a distributed setup that did not answer.
- Livestatus states: a host is 0 up, 1 down, 2 unreachable; a service 0 OK, 1 WARN, 2 CRIT, 3 UNKNOWN.

⚠️ Checkmk counts failed sign-ins and locks a normal user after as many as the site allows
(``lock_on_logon_failures``). A refusal is therefore remembered for a quarter of an hour, and the cards do not ask in
the meantime; saving the connection or pressing Test asks at once.
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
)

REFUSED_SECONDS = 900
REFUSED_KEY = "checkmk:refused"
REFUSED_HINT = "An automation user and its secret, from Setup, Users. Repeated wrong tries can lock a normal user."
HOST_STATES = {0: "Up", 1: "Down", 2: "Unreachable"}
SERVICE_STATES = {0: "OK", 1: "WARN", 2: "CRIT", 3: "UNKNOWN"}


class CheckmkAdapter(Adapter):
    kind = "checkmk"
    label = "Checkmk"
    category = "monitoring"
    description = "Hosts and services of a Checkmk site: how many are fine, and what is down or critical."
    icon = "checkmk"
    beta = True
    docs_url = "https://docs.checkmk.com/latest/en/rest_api.html"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://checkmk.example.com",
              help="The address of the Checkmk server, without the site."),
        Field("site", "Site", required=True, placeholder="mysite", help="The name of the site, as it stands in the address after the server."),
        Field("username", "User name", required=True, default="automation", help="Best an automation user, from Setup, Users."),
        Field("secret", "Secret", type="password", secret=True, required=True, help="The automation secret, or the user's password."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(
            kind="summary",
            label="Overview",
            description="Hosts up and down, services OK, warning, critical and unknown. Red with a host down or a critical service.",
            renderer="value",
            default_size=(3, 2),
            min_size=(1, 1),
            refresh_seconds=120,
            metrics=("hosts_down", "services_crit", "services_warn"),
        ),
        WidgetType(
            kind="problems",
            label="Problems",
            description="Hosts down and services that are not OK, the worst first, with what the check said.",
            renderer="list",
            default_size=(4, 3),
            refresh_seconds=120,
            options=(
                Field("limit", "Entries", type="number", default=10),
                Field("acknowledged", "Show acknowledged", type="bool", default=True),
            ),
        ),
    )

    def _api(self, config: dict[str, Any]) -> str:
        site = str(config.get("site") or "").strip().strip("/")
        if not site or "/" in site:
            raise AdapterError("That is not the name of a site.", code="bad_site", hint="The word after the server in Checkmk's address.")
        return f"{base_url(config)}/{site}/check_mk/api/1.0"

    async def _call(self, config: dict[str, Any], ctx: Context, method: str, path: str, body: Any = None, *, asked: bool = False) -> Any:
        if not asked and time.time() < float(ctx.cache.get(REFUSED_KEY) or 0):
            raise AuthFailed("Checkmk refused the user or the secret.", hint=REFUSED_HINT)
        token = f"{str(config.get('username') or '').strip()} {config.get('secret') or ''}"
        response = await ctx.request(method, f"{self._api(config)}{path}", json_body=body,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.status_code == 401:
            ctx.cache[REFUSED_KEY] = time.time() + REFUSED_SECONDS
            raise AuthFailed("Checkmk refused the user or the secret.", hint=REFUSED_HINT)
        ctx.cache.pop(REFUSED_KEY, None)
        if response.status_code == 403:
            raise AuthFailed("This user may not read the monitoring.", hint="Give it a role that may see all hosts and services.")
        if response.status_code == 404:
            raise AdapterError("Checkmk has no API at that address.", code="not_found",
                               hint="Check the site name; the address is the server, the site is its own field.")
        if response.status_code == 405:
            raise AdapterError("This Checkmk is older than 2.4.", code="too_old", hint="The cards need Checkmk 2.4 or newer.")
        if response.status_code >= 400:
            raise AdapterError(f"Checkmk answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("Checkmk did not answer with JSON.", code="not_json",
                               hint="The address probably points at the sign-in page; check the site name.") from error

    async def _rows(self, config: dict[str, Any], ctx: Context, table: str, columns: list[str], query: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        """The rows of one table, kept a minute: two cards on one connection ask once."""
        key = f"checkmk:{table}:{','.join(columns)}:{query}"
        kept = ctx.cache.get(key)
        if kept and time.time() - kept[0] < 60:
            return kept[1]
        body: dict[str, Any] = {"columns": columns}
        if query:
            body["query"] = query
        answer = await self._call(config, ctx, "POST", f"/domain-types/{table}/collections/all", body)
        values = answer.get("value") if isinstance(answer, dict) else None
        rows = [one.get("extensions") for one in values or [] if isinstance(one, dict) and isinstance(one.get("extensions"), dict)]
        extensions = answer.get("extensions") if isinstance(answer, dict) and isinstance(answer.get("extensions"), dict) else {}
        found = (rows, [str(site) for site in extensions.get("unreachable_sites") or []])
        ctx.cache[key] = (time.time(), found)
        return found

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        version = await self._call(config, ctx, "GET", "/version", asked=True)
        if not isinstance(version, dict):
            raise AdapterError("Checkmk did not say its version.", code="not_json")
        number = (version.get("versions") or {}).get("checkmk") or "?"
        await self._rows(config, ctx, "host", ["name", "state"])
        return f"Checkmk {number} ({version.get('edition') or '?'}) answers for the site {version.get('site') or '?'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        hosts, away = await self._rows(config, ctx, "host", ["name", "state", "acknowledged", "scheduled_downtime_depth"])
        if widget_kind == "problems":
            services, _ = await self._rows(config, ctx, "service",
                                           ["host_name", "description", "state", "plugin_output", "acknowledged", "scheduled_downtime_depth"],
                                           {"op": "!=", "left": "state", "right": "0"})
            return self._problems(hosts, services, away, int(options.get("limit") or 10), bool(options.get("acknowledged", True)))
        services, _ = await self._rows(config, ctx, "service", ["host_name", "description", "state"])
        return self._summary(hosts, services, away)

    @staticmethod
    def _count(rows: list[dict[str, Any]], state: int) -> int:
        return sum(1 for row in rows if row.get("state") == state)

    @staticmethod
    def _summary(hosts: list[dict[str, Any]], services: list[dict[str, Any]], away: list[str]) -> WidgetData:
        count = CheckmkAdapter._count
        down, crit, warn, unknown = count(hosts, 1) + count(hosts, 2), count(services, 2), count(services, 1), count(services, 3)
        secondary: list[dict[str, Any]] = [
            {"label": "Hosts down", "value": down},
            {"label": "Critical", "value": crit},
            {"label": "Warning", "value": warn},
            {"label": "Unknown", "value": unknown},
            {"label": "Services", "value": len(services)},
        ]
        if away:
            secondary.append({"label": "Sites not answering", "value": ", ".join(away)})
        return WidgetData(
            status="bad" if down or crit else ("warn" if warn or unknown or away else "ok"),
            primary={"label": "Hosts up", "value": f"{count(hosts, 0)} / {len(hosts)}"},
            secondary=secondary,
            metrics={"hosts_down": float(down), "services_crit": float(crit), "services_warn": float(warn)},
        )

    @staticmethod
    def _problems(hosts: list[dict[str, Any]], services: list[dict[str, Any]], away: list[str], limit: int, acknowledged: bool) -> WidgetData:
        rows: list[tuple[int, dict[str, Any]]] = []

        def quiet(row: dict[str, Any]) -> bool:
            return bool(row.get("acknowledged")) or int(row.get("scheduled_downtime_depth") or 0) > 0

        for host in hosts:
            state = host.get("state")
            if state not in (1, 2) or (quiet(host) and not acknowledged):
                continue
            rows.append((0, {"title": str(host.get("name") or "?"), "subtitle": HOST_STATES[state],
                             "status": "warn" if quiet(host) else "bad"}))
        rank = {2: 1, 3: 2, 1: 3}
        for service in services:
            state = service.get("state")
            if state not in rank or (quiet(service) and not acknowledged):
                continue
            rows.append((rank[state], {
                "title": f"{service.get('host_name') or '?'}: {service.get('description') or '?'}",
                "subtitle": str(service.get("plugin_output") or "")[:160],
                "value": SERVICE_STATES[state],
                "status": "warn" if quiet(service) or state != 2 else "bad",
            }))
        for site in away:
            rows.append((0, {"title": site, "subtitle": "Site not answering", "status": "warn"}))
        rows.sort(key=lambda pair: pair[0])
        items = [item for _, item in rows]
        worst = "bad" if any(item["status"] == "bad" for item in items) else ("warn" if items else "ok")
        return WidgetData(status=worst, items=items[:max(1, limit)], secondary=[{"label": "Problems", "value": len(items)}])

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        hosts = [{"name": f"host-{number}", "state": 0} for number in range(1, 24)]
        flapping = fake.flicker("checkmk-crit", tick, 0.3)
        services = [{"host_name": f"host-{number % 23 + 1}", "description": "CPU load", "state": 0} for number in range(412)]
        problems = [
            {"host_name": "nas", "description": "Filesystem /mnt/tank", "state": 1, "plugin_output": "Used: 86.2% - 3.44 TiB of 4.00 TiB, (warn/crit at 80.00%/90.00% used)"},
            {"host_name": "router", "description": "Interface 2", "state": 2 if flapping else 1, "plugin_output": "Operational state: down"},
        ]
        if widget_kind == "problems":
            return self._problems(hosts, problems, [], int(options.get("limit") or 10), True)
        return self._summary(hosts, services + problems, [])


ADAPTER = CheckmkAdapter()
