"""Cloudflare: the tunnels and how they stand, the zones, and a day of traffic.

API v4 at ``https://api.cloudflare.com/client/v4`` with an API token sent as
``Authorization: Bearer``: ``/zones`` for the zones,
``/accounts/{account_id}/cfd_tunnel`` for the tunnels, and the GraphQL
Analytics API at ``/graphql`` for the requests and the security events of the
last 24 hours. All read-only.

⚠️ A tunnel's ``status`` is Cloudflare's own verdict: ``healthy`` serves
traffic, ``degraded`` serves it in an unhealthy state, ``down`` has no
connection to the edge and serves nothing, and ``inactive`` has never been
run, which is a tunnel set up and not used rather than a fault.

⚠️ The tunnels belong to an account, not a zone. The account is taken from
the field when it is filled, and otherwise from the first zone the token
sees, so a token that may read zones needs no account typed in.

⚠️ GraphQL answers 200 with an ``errors`` list when it refuses, a missing
Analytics permission included, so the list is read before the data.

⚠️ The adaptive datasets are sampled, and their ``count`` is an estimate.
Since 2026-10-02 they keep at least 31 days on every plan, so a day is within
reach of a Free zone.

Read from Cloudflare's OpenAPI schema (cloudflare/api-schemas) and the GraphQL
Analytics API documentation (cloudflare/cloudflare-docs) on 2026-10-08.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    measured,
)

API = "https://api.cloudflare.com/client/v4"
#: A tunnel's status as a colour.
TUNNEL = {"healthy": "ok", "degraded": "warn", "down": "bad", "inactive": "unknown"}
#: A zone's status as a colour. "moved" means the name servers no longer point here.
ZONE = {"active": "ok", "pending": "warn", "initializing": "unknown", "moved": "bad"}
#: Error codes that mean the token itself was refused, whatever the HTTP status.
AUTH_CODES = {6003, 6111, 9106}
ZONES_SECONDS = 600
TUNNELS_SECONDS = 60
TRAFFIC_SECONDS = 600

TRAFFIC_QUERY = """query Traffic($zoneTag: string, $start: Time, $end: Time) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      requests: httpRequestsAdaptiveGroups(filter: {datetime_gt: $start, datetime_lt: $end}, limit: 1) { count }
      security: firewallEventsAdaptiveGroups(filter: {datetime_gt: $start, datetime_lt: $end}, limit: 1) { count }
    }
  }
}"""


def _version_key(version: str) -> tuple[int, ...]:
    """cloudflared numbers itself by date, 2026.10.0; as text that sorts before 2026.9.1."""
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


class CloudflareAdapter(Adapter):
    kind = "cloudflare"
    label = "Cloudflare"
    category = "network"
    description = "Whether your tunnels reach the edge, how your zones stand, and the requests and security events of the last day."
    icon = "cloudflare"
    docs_url = "https://developers.cloudflare.com/api/"
    fields = (
        Field("token", "API token", type="password", secret=True, required=True,
              help="A custom API token with Zone: Zone Read, Account: Cloudflare Tunnel Read and, for the traffic card, Account Analytics Read."),
        Field("account_id", "Account ID", placeholder="0123456789abcdef0123456789abcdef",
              help="For the tunnels. Empty takes the account of the first zone the token can read."),
    )
    widgets = (
        WidgetType(kind="tunnels", label="Tunnels", description="Every tunnel with whether it reaches the edge, through how many connections and which data centres.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120, metrics=("down",),
                   options=(Field("hide_inactive", "Hide tunnels that never ran", type="bool", default=True),
                            Field("limit", "Entries", type="number", default=8))),
        WidgetType(kind="zones", label="Zones", description="The domains on Cloudflare with their status and plan, a paused one marked.",
                   renderer="list", default_size=(3, 3), refresh_seconds=1800,
                   options=(Field("limit", "Entries", type="number", default=8),)),
        WidgetType(kind="traffic", label="Traffic", description="Requests and security events of one zone over the last 24 hours.",
                   renderer="value", default_size=(3, 2), refresh_seconds=900, metrics=("requests", "security"),
                   options=(Field("zone", "Zone", placeholder="example.com", help="The domain. Empty takes the first zone the token can read."),)),
    )

    # -- talking to Cloudflare -----------------------------------------------

    @staticmethod
    def _headers(config: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {str(config.get('token') or '').strip()}", "Accept": "application/json"}

    @staticmethod
    def _refused(status: int, answer: Any = None) -> None:
        codes = {int(one.get("code") or 0) for one in (answer or {}).get("errors") or [] if isinstance(one, dict)} \
            if isinstance(answer, dict) else set()
        # ⚠️ A token Cloudflare cannot read comes back as 400, not 401: measured
        # on 2026-10-08, 6003 with 6111 on the REST API and 9106 on GraphQL.
        if status in (401, 403) or codes & AUTH_CODES:
            raise AuthFailed("Cloudflare refused the API token.",
                             hint="A custom token with Zone: Zone Read, Account: Cloudflare Tunnel Read and Account Analytics Read. "
                                  "A token that has expired or was rolled is refused the same way.")
        if status >= 400:
            raise AdapterError(f"Cloudflare answered with HTTP {status}.", code="http_error")

    @staticmethod
    def _json(response: Any) -> Any:
        try:
            return response.json()
        except ValueError as failure:
            if response.status_code >= 400:
                return None
            raise AdapterError("Cloudflare did not answer with JSON.", code="not_json") from failure

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None, cache: float) -> Any:
        response = await ctx.request("GET", f"{API}{path}", headers=self._headers(config), params=params,
                                     cache_seconds=cache, auth_errors=False)
        answer = self._json(response)
        self._refused(response.status_code, answer)
        if not isinstance(answer, dict) or answer.get("success") is False:
            said = "; ".join(str(one.get("message")) for one in (answer or {}).get("errors") or [] if isinstance(one, dict))
            raise AdapterError(f"Cloudflare refused: {said or 'no reason given'}.", code="refused")
        return answer.get("result")

    async def _zones(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        result = await self._get(config, ctx, "/zones", {"per_page": 50, "order": "name"}, ZONES_SECONDS)
        return [one for one in (result if isinstance(result, list) else []) if isinstance(one, dict)]

    async def _account(self, config: dict[str, Any], ctx: Context) -> str:
        written = str(config.get("account_id") or "").strip()
        if written:
            return written
        for zone in await self._zones(config, ctx):
            account = (zone.get("account") or {}).get("id")
            if account:
                return str(account)
        raise AdapterError("No account is named, and the token reads no zone to take one from.", code="no_account",
                           hint="The Account ID is on the right of any zone's overview page in the dashboard.")

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        zones = await self._zones(config, ctx)
        return f"Cloudflare answers with {len(zones)} zone{'s' if len(zones) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "zones":
            return self._zone_list(await self._zones(config, ctx), options)
        if widget_kind == "tunnels":
            account = await self._account(config, ctx)
            result = await self._get(config, ctx, f"/accounts/{account}/cfd_tunnel", {"is_deleted": "false", "per_page": 100}, TUNNELS_SECONDS)
            return self._tunnel_list([one for one in (result if isinstance(result, list) else []) if isinstance(one, dict)], options)
        zones = await self._zones(config, ctx)
        wanted = str(options.get("zone") or "").strip().lower()
        zone = next((one for one in zones if not wanted or str(one.get("name") or "").lower() == wanted), None)
        if zone is None:
            raise AdapterError(f"The token reads no zone called {wanted}." if wanted else "The token reads no zone.", code="no_zone")
        counts = await self._traffic(config, ctx, str(zone.get("id")))
        return self._traffic_card(str(zone.get("name") or ""), counts)

    async def _traffic(self, config: dict[str, Any], ctx: Context, zone_id: str) -> dict[str, int | None]:
        end = datetime.now(UTC).replace(second=0, microsecond=0)
        start = end - timedelta(hours=24)
        variables = {"zoneTag": zone_id, "start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z")}
        response = await ctx.request("POST", f"{API}/graphql", headers=self._headers(config),
                                     json_body={"query": TRAFFIC_QUERY, "variables": variables},
                                     cache_seconds=TRAFFIC_SECONDS, auth_errors=False)
        answer = self._json(response)
        self._refused(response.status_code, answer)
        # ⚠️ A refusal comes as 200 with a list of errors and no data.
        errors = [str(one.get("message")) for one in (answer or {}).get("errors") or [] if isinstance(one, dict)]
        if errors:
            raise AdapterError(f"Cloudflare's analytics refused: {'; '.join(errors)}.", code="analytics_refused",
                               hint="The traffic card needs Account Analytics Read on the token, for this zone.")
        zones = ((answer.get("data") or {}).get("viewer") or {}).get("zones") or []
        first = zones[0] if zones and isinstance(zones[0], dict) else {}

        def total(key: str) -> int | None:
            groups = first.get(key)
            if not isinstance(groups, list):
                return None
            # No dimensions asked for: one group, or none on a day without any.
            return int(sum(int((one or {}).get("count") or 0) for one in groups))

        return {"requests": total("requests"), "security": total("security")}

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _tunnel_list(tunnels: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        rows = []
        for tunnel in tunnels:
            if tunnel.get("deleted_at"):
                continue
            status = str(tunnel.get("status") or "")
            if options.get("hide_inactive", True) and status == "inactive":
                continue
            colour = TUNNEL.get(status, "unknown")
            connections = [one for one in tunnel.get("connections") or [] if isinstance(one, dict)]
            colos = sorted({str(one.get("colo_name") or "") for one in connections} - {""})
            versions = sorted({str(one.get("client_version") or "") for one in connections} - {""}, key=_version_key)
            parts = [f"{len(connections)} connection{'s' if len(connections) != 1 else ''}" if connections else "",
                     ", ".join(colos[:4]), f"cloudflared {versions[-1]}" if versions else ""]
            if status == "down" and tunnel.get("conns_inactive_at"):
                parts.append(f"down for {ago(tunnel.get('conns_inactive_at'))}")
            rows.append({"colour": colour, "row": {
                "id": tunnel.get("id"),
                "title": str(tunnel.get("name") or tunnel.get("id") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": status or "?",
                "status": colour,
            }})
        rows.sort(key=lambda one: (order.get(one["colour"], 4), str(one["row"]["title"]).lower()))
        down = sum(1 for one in rows if one["colour"] == "bad")
        degraded = sum(1 for one in rows if one["colour"] == "warn")
        healthy = sum(1 for one in rows if one["colour"] == "ok")
        return WidgetData(
            status="bad" if down else "warn" if degraded else "ok",
            items=[one["row"] for one in rows][: max(1, int(options.get("limit") or 8))],
            primary={"label": "Healthy", "value": f"{healthy} / {len(rows)}"},
            metrics=measured({"down": float(down)}),
            meta={"empty": "No tunnel in this account"},
        )

    @staticmethod
    def _zone_list(zones: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        rows = []
        for zone in zones:
            status = str(zone.get("status") or "")
            colour = ZONE.get(status, "unknown")
            # A paused zone answers DNS only: no proxy, no cache, no firewall.
            if zone.get("paused") and colour == "ok":
                colour, status = "warn", "paused"
            plan = (zone.get("plan") or {}).get("name") if isinstance(zone.get("plan"), dict) else ""
            rows.append({
                "id": zone.get("id"),
                "title": str(zone.get("name") or "?"),
                "subtitle": " · ".join(part for part in (str(plan or ""), str(zone.get("type") or "")) if part),
                "value": status or "?",
                "status": colour,
            })
        worst = "bad" if any(row["status"] == "bad" for row in rows) else "warn" if any(row["status"] == "warn" for row in rows) else "ok"
        return WidgetData(
            status=worst,
            items=rows[: max(1, int(options.get("limit") or 8))],
            primary={"label": "Zones", "value": len(rows)},
            meta={"empty": "The token reads no zone"},
        )

    @staticmethod
    def _traffic_card(zone: str, counts: dict[str, int | None]) -> WidgetData:
        requests, security = counts.get("requests"), counts.get("security")
        secondary: list[dict[str, Any]] = []
        if security is not None:
            secondary.append({"label": "Security events", "value": security, "metric": "security"})
        secondary.append({"label": "Zone", "value": zone})
        return WidgetData(
            status="ok",
            primary={"label": "Requests in 24 h", "value": requests if requests is not None else "?", "metric": "requests"},
            secondary=secondary,
            metrics=measured({"requests": float(requests) if requests is not None else None,
                              "security": float(security) if security is not None else None}),
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = datetime.now(UTC)
        stamp = (now - timedelta(minutes=37)).isoformat().replace("+00:00", "Z")
        nas_up = not fake.flicker("cf-nas", tick, 0.3)
        tunnels = [
            {"id": "t1", "name": "homelab", "status": "healthy", "connections": [
                {"colo_name": "fra06", "client_version": "2026.9.1"}, {"colo_name": "fra08", "client_version": "2026.9.1"},
                {"colo_name": "zrh01", "client_version": "2026.9.1"}, {"colo_name": "mxp01", "client_version": "2026.9.1"}]},
            {"id": "t2", "name": "nas", "status": "healthy" if nas_up else "down", "conns_inactive_at": stamp,
             "connections": [{"colo_name": "fra06", "client_version": "2026.8.0"}] if nas_up else []},
            {"id": "t3", "name": "office", "status": "degraded", "connections": [{"colo_name": "mil01", "client_version": "2026.9.1"}]},
            {"id": "t4", "name": "test", "status": "inactive", "connections": []},
        ]
        zones = [
            {"id": "z1", "name": "example.com", "status": "active", "paused": False, "type": "full", "plan": {"name": "Free Website"}},
            {"id": "z2", "name": "example.net", "status": "active", "paused": True, "type": "full", "plan": {"name": "Free Website"}},
            {"id": "z3", "name": "example.org", "status": "pending", "paused": False, "type": "full", "plan": {"name": "Free Website"}},
        ]
        if widget_kind == "tunnels":
            return self._tunnel_list(tunnels, options)
        if widget_kind == "zones":
            return self._zone_list(zones, options)
        return self._traffic_card("example.com", {"requests": fake.counter("cf-requests", tick, 48_000, 3.0),
                                                  "security": fake.counter("cf-security", tick, 310, 0.05)})


ADAPTER = CloudflareAdapter()
