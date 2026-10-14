"""Pterodactyl: the game servers, which run, what they use, and start or stop.

The panel's client API answers with a client key (Account, API Credentials,
``ptlc_...``) as a Bearer token. ``/api/client`` lists the servers the key's
user can reach, ``/api/client/servers/<identifier>/resources`` gives one
server's live state from its node, and ``.../power`` takes
``{"signal": "start" | "stop" | "restart" | "kill"}``.

⚠️ Every object comes wrapped: ``{"object": "server", "attributes": {...}}``
inside ``{"object": "list", "data": [...]}``.

⚠️ ``current_state`` comes from Wings on the node: ``running``,
``starting``, ``stopping`` or ``offline``. A node that cannot be reached
fails that one server's call, not the list; its row says so.

⚠️ ``status`` is the panel's: null when all is well, otherwise
``installing``, ``install_failed``, ``reinstall_failed``, ``suspended`` or
``restoring_backup``. Limits are in MiB and per cent of one core, 0 meaning
no limit.

Written from the panel's source (``routes/api-client.php``, the client
``ServerTransformer`` and ``StatsTransformer``, release v1.15.1). Not yet
run against a panel.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from . import demo as fake
from .base import (
    Action,
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    duration_short,
    human_bytes,
)

LIST_SECONDS = 60
STATS_SECONDS = 15
#: Servers read at most; one call each for their live state.
MOST_SERVERS = 25
PANEL_STATUS = {"installing": "installing", "install_failed": "install failed", "reinstall_failed": "reinstall failed",
                "suspended": "suspended", "restoring_backup": "restoring a backup"}


def _attributes(entry: Any) -> dict[str, Any]:
    return (entry.get("attributes") or {}) if isinstance(entry, dict) else {}


class PterodactylAdapter(Adapter):
    kind = "pterodactyl"
    label = "Pterodactyl"
    category = "hosts"
    description = "The game servers of a Pterodactyl panel: which run, what they use, and buttons to start and stop them."
    icon = "pterodactyl"
    docs_url = "https://pterodactyl.io/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://panel.example.com"),
        Field("token", "Client API key", type="password", secret=True, required=True,
              help="Made under Account, API Credentials (ptlc_...). Not an application key: the card sees the servers this user can reach."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many servers run, how many are offline, and the ones the panel flags.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("running",)),
        WidgetType(kind="servers", label="Servers", description="Each server with its state, processor, memory and uptime, and buttons to start and stop it.",
                   renderer="list", default_size=(3, 4), refresh_seconds=30,
                   options=(Field("power", "Offer start and stop", type="bool", default=False,
                                  help="Needs a key whose user may control the server. Asks before it stops one."),)),
    )

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {config.get('token') or ''}"}

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/api/client{path}", params=params, headers=self._headers(config),
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        self._refused(response.status_code)
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("The panel did not answer with JSON.", code="not_json", hint="The address of the panel itself.") from failure
        return answer if isinstance(answer, dict) else {}

    @staticmethod
    def _refused(status: int) -> None:
        if status in (401, 403):
            raise AuthFailed("The panel refused the API key.", hint="A client key from Account, API Credentials, starting with ptlc_.")
        if status == 404:
            raise AdapterError("The panel has no client API at this address.", code="http_error", hint="The address of the panel itself.")
        if status >= 400:
            raise AdapterError(f"The panel answered with HTTP {status}.", code="http_error")

    async def _servers(self, config: dict[str, Any], ctx: Context, cache: float = LIST_SECONDS) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "", cache, {"per_page": MOST_SERVERS})
        return [_attributes(entry) for entry in answer.get("data") or [] if _attributes(entry)]

    async def _state(self, config: dict[str, Any], ctx: Context, identifier: str) -> dict[str, Any] | None:
        """One server's live state, or None when its node does not answer."""
        try:
            return _attributes(await self._get(config, ctx, f"/servers/{quote(identifier, safe='')}/resources", STATS_SECONDS))
        except AuthFailed:
            raise
        except AdapterError:
            return None

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        servers = await self._servers(config, ctx, cache=0)
        return f"The panel answers with {len(servers)} server{'s' if len(servers) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        servers = await self._servers(config, ctx)
        states = {}
        for server in servers:
            identifier = str(server.get("identifier") or "")
            if identifier and not server.get("status"):
                states[identifier] = await self._state(config, ctx, identifier)
        if widget_kind == "servers":
            return self._list(servers, states, options)
        return self._overview(servers, states)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id not in ("start", "stop", "restart"):
            raise AdapterError("Unknown action.", code="no_such_action")
        identifier = str(params.get("server") or "")
        # Only a server the panel lists for this key, never one the browser made up.
        servers = {str(one.get("identifier")): one for one in await self._servers(config, ctx, cache=0)}
        if identifier not in servers:
            raise AdapterError("The panel has no such server.", code="no_such_server")
        response = await ctx.request("POST", f"{base_url(config)}/api/client/servers/{quote(identifier, safe='')}/power",
                                     json_body={"signal": action_id}, headers=self._headers(config),
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.status_code == 403:
            raise AuthFailed("This user may not control the server.", hint="A subuser with the power permissions, or the owner.")
        self._refused(response.status_code)
        ctx.cache.clear()
        return f"{servers[identifier].get('name') or identifier}: {action_id} sent."

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _state_word(server: dict[str, Any], state: dict[str, Any] | None) -> str:
        if server.get("status"):
            return PANEL_STATUS.get(str(server["status"]), str(server["status"]))
        if server.get("is_node_under_maintenance"):
            return "node in maintenance"
        if state is None:
            return "node unreachable"
        return str(state.get("current_state") or "offline")

    @classmethod
    def _overview(cls, servers: list[dict[str, Any]], states: dict[str, Any]) -> WidgetData:
        words = [cls._state_word(server, states.get(str(server.get("identifier")))) for server in servers]
        running = words.count("running")
        flagged = [word for word in words if word not in ("running", "offline", "starting", "stopping")]
        return WidgetData(
            status="bad" if any("failed" in word or word == "node unreachable" for word in flagged) else "warn" if flagged else "ok",
            primary={"label": "Running", "value": f"{running}/{len(servers)}", "metric": "running"},
            secondary=[{"label": "Offline", "value": words.count("offline")}, {"label": "Flagged", "value": len(flagged)}],
            metrics={"running": float(running)},
            meta={"notice": ", ".join(sorted(set(flagged)))},
        )

    @classmethod
    def _list(cls, servers: list[dict[str, Any]], states: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        rows = []
        for server in servers:
            identifier = str(server.get("identifier") or "")
            state = states.get(identifier)
            word = cls._state_word(server, state)
            resources = (state or {}).get("resources") or {}
            limits = server.get("limits") or {}
            parts = [word]
            if word == "running":
                cpu = float(resources.get("cpu_absolute") or 0)
                parts.append(f"CPU {cpu:.0f} %" + (f" of {limits['cpu']} %" if limits.get("cpu") else ""))
                memory = human_bytes(float(resources.get("memory_bytes") or 0))
                parts.append(memory + (f" of {human_bytes(float(limits['memory']) * 1024 * 1024)}" if limits.get("memory") else ""))
                if resources.get("uptime"):
                    parts.append(f"up {duration_short(float(resources['uptime']) / 1000)}")
            actions = []
            if options.get("power") and word in ("running", "offline"):
                if word == "running":
                    actions = [Action(id="stop", label="Stop", icon="square", confirm=True, danger=True, params={"server": identifier}).model_dump(),
                               Action(id="restart", label="Restart", icon="rotate-cw", confirm=True, params={"server": identifier}).model_dump()]
                else:
                    actions = [Action(id="start", label="Start", icon="play", params={"server": identifier}).model_dump()]
            rows.append({
                "id": identifier,
                "title": str(server.get("name") or identifier),
                "subtitle": " · ".join(parts),
                "status": {"running": "ok", "offline": "unknown", "starting": "warn", "stopping": "warn"}.get(
                    word, "bad" if "failed" in word or word == "node unreachable" else "warn"),
                "actions": actions,
            })
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "ok", items=rows,
                          meta={"empty": "No server this key can reach"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        servers = [
            {"identifier": "1a7ce997", "name": "Minecraft survival", "limits": {"memory": 6144, "cpu": 300}, "status": None},
            {"identifier": "5b0e2f11", "name": "Valheim", "limits": {"memory": 4096, "cpu": 200}, "status": None},
            {"identifier": "9c4d8a02", "name": "Terraria", "limits": {"memory": 1024, "cpu": 100}, "status": None},
        ]
        states = {
            "1a7ce997": {"current_state": "running", "resources": {"cpu_absolute": fake.walk("ptero-cpu", tick, 40, 160),
                                                                    "memory_bytes": fake.walk("ptero-mem", tick, 3.1e9, 4.2e9), "uptime": 3_600_000 * 41 + tick * 15_000}},
            "5b0e2f11": {"current_state": "running", "resources": {"cpu_absolute": fake.walk("ptero-cpu2", tick, 20, 70),
                                                                    "memory_bytes": 2.4e9, "uptime": 3_600_000 * 5}},
            "9c4d8a02": {"current_state": "offline", "resources": {}},
        }
        if widget_kind == "servers":
            return self._list(servers, states, options)
        return self._overview(servers, states)


ADAPTER = PterodactylAdapter()
