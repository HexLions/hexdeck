"""Keycloak: who is signed in to a realm, and the sign-ins that failed.

HexDeck signs in as a client of the realm: a confidential client with
"Service accounts roles" switched on, its secret, and the roles
``view-users``, ``view-realm``, ``view-clients`` and ``view-events`` of
``realm-management`` given to its service account. That is read-only.
The token comes from ``/realms/<realm>/protocol/openid-connect/token``
(``grant_type=client_credentials``); the admin API answers under
``/admin/realms/<realm>``: ``users/count``, ``client-session-stats`` (active
and offline sessions per client, as strings), ``events`` (``type``,
``dateFrom``, ``max``) and the realm itself, which says whether events are
recorded at all.

⚠️ Failed sign-ins are only there when the realm records events (Realm
settings, Events, User events settings) and keeps them long enough. With
events off the card says so instead of showing zero.

⚠️ ``details.username`` is what was typed, also for a user that does not
exist (``user_not_found``).

⚠️ A wrong secret is ``401`` with ``unauthorized_client``; a service account
without the roles gets ``403`` from the admin API.

Checked against Keycloak 26.8.1 running locally on 2026-10-15, with a realm
of four users, two signed in through a public client, four failed sign-ins
(two wrong passwords, a third one, an unknown user), a service account with
the four view roles, a wrong secret, and a realm it may not read.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

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
)

READ_SECONDS = 60
#: Failed sign-ins read at most for one day.
MOST_EVENTS = 500
ERRORS = {"invalid_user_credentials": "wrong password", "user_not_found": "unknown user", "user_disabled": "disabled user",
          "user_temporarily_disabled": "locked out", "invalid_client_credentials": "wrong client secret", "expired_code": "expired code"}


class KeycloakAdapter(Adapter):
    kind = "keycloak"
    label = "Keycloak"
    category = "network"
    description = "A Keycloak realm: who is signed in through which client, how many users it has, and the sign-ins that failed and why."
    icon = "keycloak"
    docs_url = "https://www.keycloak.org/docs-api/latest/rest-api/index.html"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://sso.example.com",
              help="Keycloak's address, without /realms or /admin."),
        Field("realm", "Realm", required=True, placeholder="home"),
        Field("client_id", "Client ID", required=True, placeholder="hexdeck",
              help="A confidential client of this realm with Service accounts roles on. Give its service account the roles view-users, view-realm, view-clients and view-events of realm-management."),
        Field("client_secret", "Client secret", type="password", secret=True, required=True, help="Under the client's Credentials tab."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="realm", label="Realm", description="The sessions active now, the users, and the sign-ins that failed in the last day.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("sessions", "failed"),
                   options=(Field("warn_at", "Amber from", type="number", default=10,
                                  help="Failed sign-ins in a day that turn the card amber."),)),
        WidgetType(kind="failed", label="Failed sign-ins", description="The last sign-ins that failed: who was typed, why, from where and through which client.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("limit", "Entries", type="number", default=8),)),
        WidgetType(kind="clients", label="Sessions by client", description="Each client with its active and offline sessions.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60),
    )

    def _realm(self, config: dict[str, Any]) -> str:
        return quote(str(config.get("realm") or "").strip(), safe="")

    async def _token(self, config: dict[str, Any], ctx: Context) -> str:
        key = f"keycloak-token:{base_url(config)}:{self._realm(config)}:{config.get('client_id')}"
        cached = ctx.cache.get(key)
        if isinstance(cached, tuple) and cached[1] > time.time():
            return str(cached[0])
        response = await ctx.request("POST", f"{base_url(config)}/realms/{self._realm(config)}/protocol/openid-connect/token",
                                     data={"grant_type": "client_credentials", "client_id": str(config.get("client_id") or ""),
                                           "client_secret": str(config.get("client_secret") or "")},
                                     verify=not config.get("insecure"), auth_errors=False)
        try:
            answer = response.json()
        except ValueError:
            answer = {}
        if response.status_code in (400, 401) and isinstance(answer, dict) and answer.get("error"):
            raise AuthFailed(f"Keycloak refused the client: {answer.get('error_description') or answer['error']}.",
                             hint="The client ID and secret of a confidential client with Service accounts roles on.")
        if response.status_code == 404:
            raise AdapterError("Keycloak has no such realm.", code="no_such_realm", hint="The realm's name as Keycloak shows it, not its display name.")
        if response.status_code >= 400 or not isinstance(answer, dict) or not answer.get("access_token"):
            raise AdapterError(f"Keycloak answered the sign-in with HTTP {response.status_code}.", code="http_error",
                               hint="Keycloak's address, without /realms or /admin.")
        lifetime = float(answer.get("expires_in") or 60)
        ctx.cache[key] = (answer["access_token"], time.time() + max(10.0, lifetime - 30))
        return str(answer["access_token"])

    async def _admin(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None) -> Any:
        token = await self._token(config, ctx)
        response = await ctx.request("GET", f"{base_url(config)}/admin/realms/{self._realm(config)}{path}", params=params,
                                     headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                                     verify=not config.get("insecure"), cache_seconds=READ_SECONDS, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("The client's service account may not read this.",
                             hint="Give it the roles view-users, view-realm, view-clients and view-events of realm-management.")
        if response.status_code >= 400:
            raise AdapterError(f"Keycloak answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Keycloak did not answer with JSON.", code="not_json") from failure

    async def _failed(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=1)
        events = await self._admin(config, ctx, "/events", {"type": "LOGIN_ERROR", "dateFrom": since.date().isoformat(), "max": MOST_EVENTS})
        cutoff = since.timestamp() * 1000
        return [event for event in events or [] if isinstance(event, dict) and float(event.get("time") or 0) >= cutoff]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        users = await self._admin(config, ctx, "/users/count")
        return f"Keycloak answers; the realm {config.get('realm')} has {int(users or 0)} users."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "clients":
            return self._clients(await self._admin(config, ctx, "/client-session-stats") or [])
        realm = await self._admin(config, ctx, "") or {}
        failed = await self._failed(config, ctx) if realm.get("eventsEnabled") else None
        if widget_kind == "failed":
            return self._failed_list(failed, max(1, int(options.get("limit") or 8)))
        users = await self._admin(config, ctx, "/users/count")
        sessions = await self._admin(config, ctx, "/client-session-stats") or []
        return self._overview(int(users or 0), sessions, failed, int(options.get("warn_at") or 10))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(users: int, sessions: list[Any], failed: list[dict[str, Any]] | None, warn_at: int) -> WidgetData:
        active = sum(int(entry.get("active") or 0) for entry in sessions if isinstance(entry, dict))
        offline = sum(int(entry.get("offline") or 0) for entry in sessions if isinstance(entry, dict))
        secondary: list[dict[str, Any]] = [{"label": "Users", "value": users}]
        if offline:
            secondary.append({"label": "Offline", "value": offline})
        metrics = {"sessions": float(active)}
        if failed is not None:
            secondary.append({"label": "Failed today", "value": len(failed), "metric": "failed"})
            metrics["failed"] = float(len(failed))
        return WidgetData(
            status="warn" if failed is not None and len(failed) >= max(1, warn_at) else "ok",
            primary={"label": "Sessions", "value": active, "metric": "sessions"},
            secondary=secondary,
            metrics=metrics,
            meta={"notice": "" if failed is not None else "This realm records no sign-in events, so failed sign-ins are not counted."},
        )

    @staticmethod
    def _failed_list(failed: list[dict[str, Any]] | None, limit: int) -> WidgetData:
        if failed is None:
            return WidgetData(status="unknown", items=[], meta={"empty": "This realm records no sign-in events"})
        rows = []
        for event in sorted(failed, key=lambda one: -float(one.get("time") or 0))[:limit]:
            details = event.get("details") or {}
            error = str(event.get("error") or "")
            when = ago(float(event.get("time") or 0) / 1000)
            rows.append({
                "id": str(event.get("id")),
                "title": str(details.get("username") or "?"),
                "subtitle": " · ".join(part for part in (ERRORS.get(error, error.replace("_", " ")), str(event.get("ipAddress") or ""),
                                                         str(event.get("clientId") or ""), f"{when} ago" if when else "") if part),
                "status": "warn",
            })
        return WidgetData(status="warn" if rows else "ok", items=rows, meta={"empty": "No failed sign-in in the last day"})

    @staticmethod
    def _clients(sessions: list[Any]) -> WidgetData:
        rows = [{"id": str(entry.get("id") or entry.get("clientId")), "title": str(entry.get("clientId") or "?"),
                 "subtitle": f"{int(entry.get('offline') or 0)} offline" if int(entry.get("offline") or 0) else "",
                 "value": int(entry.get("active") or 0), "status": "ok"}
                for entry in sessions if isinstance(entry, dict)]
        rows.sort(key=lambda row: -row["value"])
        return WidgetData(status="ok", items=rows, meta={"empty": "Nobody is signed in"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        sessions = [{"clientId": "immich", "active": str(3 + tick % 2), "offline": "0"}, {"clientId": "grafana", "active": "2", "offline": "0"},
                    {"clientId": "nextcloud", "active": "1", "offline": "4"}]
        now = time.time() * 1000
        failed = [{"id": "1", "time": now - 600_000, "error": "invalid_user_credentials", "ipAddress": "192.168.1.20", "clientId": "immich", "details": {"username": "anna"}},
                  {"id": "2", "time": now - 3_600_000, "error": "user_not_found", "ipAddress": "203.0.113.9", "clientId": "grafana", "details": {"username": "admin"}}]
        if fake.flicker("keycloak-quiet", tick, 0.3):
            failed = []
        if widget_kind == "clients":
            return self._clients(sessions)
        if widget_kind == "failed":
            return self._failed_list(failed, max(1, int(options.get("limit") or 8)))
        return self._overview(14, sessions, failed, int(options.get("warn_at") or 10))


ADAPTER = KeycloakAdapter()
