"""Node-RED: whether the flows run, what is missing, and the flows themselves.

Node-RED's admin HTTP API, read-only: ``/flows`` for the flows, ``/nodes``
for the node types installed, ``/flows/state`` for whether the flows are
started, ``/diagnostics`` for the version and the memory in use.

⚠️ With ``adminAuth`` set, the adapter asks ``/auth/token`` for a token as a
user of the editor, with the scope "read": a user with read permission only
is refused the scope "*". The token lasts seven days by default and is kept
for the connection. Without ``adminAuth`` no user is needed.

⚠️ Node-RED does not start the flows while a node type they use is not
installed ("Waiting for missing types to be registered"), yet
``/flows/state`` still says "start". The missing types are found by
comparing the types the flows use with the ones installed.

⚠️ The flows are only counted: they can hold function code, addresses and
other things not meant for a dashboard.

⚠️ ``/flows/state`` needs ``runtimeState`` enabled in settings.js, and
``/diagnostics`` needs ``diagnostics`` enabled (the default); without them
the cards do without.

Checked against Node-RED 5.0.8 running locally on 2026-10-10, with three
flows, one disabled, and one node of a type not installed, as an admin and
as a read-only user.
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
    human_bytes,
    measured,
)

#: Types in a flow file that are not node types.
NOT_NODES = {"tab", "subflow", "group", "junction"}
FLOWS_SECONDS = 120


def missing_types(flows: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> list[str]:
    """The node types the flows use that are not installed."""
    installed = {kind for node in nodes for kind in (node.get("types") or [])}
    used = {str(one.get("type") or "") for one in flows}
    return sorted(kind for kind in used if kind and kind not in installed and kind not in NOT_NODES and not kind.startswith("subflow:"))


class NodeRedAdapter(Adapter):
    kind = "nodered"
    label = "Node-RED"
    category = "home"
    description = "Node-RED: whether the flows run, the node types they miss, and each flow with its nodes."
    icon = "node-red"
    docs_url = "https://nodered.org/docs/api/admin/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://nodered:1880"),
        Field("username", "Username", help="A user of the editor, read permission is enough. Empty when adminAuth is not set."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="Whether the flows run, how many flows and nodes there are, the node types missing, and the memory in use.",
                   renderer="value", default_size=(3, 2), refresh_seconds=120, metrics=("memory",)),
        WidgetType(kind="flows", label="Flows", description="Each flow with its number of nodes, the disabled ones marked, and the missing node types first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    async def _token(self, config: dict[str, Any], ctx: Context, *, fresh: bool = False) -> str | None:
        username = str(config.get("username") or "").strip()
        if not username:
            return None
        kept = ctx.cache.get("nodered_token")
        if kept and not fresh and kept[1] > time.time():
            return kept[0]
        response = await ctx.request("POST", f"{base_url(config)}/auth/token", verify=not config.get("insecure"), auth_errors=False,
                                     data={"client_id": "node-red-admin", "grant_type": "password", "scope": "read",
                                           "username": username, "password": str(config.get("password") or "")})
        if response.status_code in (401, 403):
            raise AuthFailed("Node-RED refused the user name and password.", hint="A user of the editor, as in adminAuth in settings.js.")
        if response.status_code >= 400:
            raise AdapterError(f"Node-RED answered the login with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
            token = str(answer["access_token"])
        except (ValueError, KeyError, TypeError) as failure:
            raise AdapterError("Node-RED did not answer the login as its editor does.", code="not_json") from failure
        ctx.cache["nodered_token"] = (token, time.time() + max(60.0, float(answer.get("expires_in") or 604800) - 60))
        return token

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, *, optional: bool = False, retry: bool = True) -> Any:
        token = await self._token(config, ctx)
        headers = {"Accept": "application/json", "Node-RED-API-Version": "v2"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = await ctx.request("GET", f"{base_url(config)}{path}", verify=not config.get("insecure"), headers=headers,
                                     cache_seconds=FLOWS_SECONDS, auth_errors=False)
        if response.status_code == 401 and token and retry:
            ctx.cache.pop("nodered_token", None)
            return await self._get(config, ctx, path, optional=optional, retry=False)
        if response.status_code == 401:
            raise AuthFailed("Node-RED asks for a user name and password." if not token else "Node-RED refused the token.",
                             hint="A user of the editor, as in adminAuth in settings.js.")
        if response.status_code >= 400:
            if optional:
                return None
            raise AdapterError(f"Node-RED answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of the Node-RED editor, usually port 1880.")
        try:
            return response.json()
        except ValueError as failure:
            if optional:
                return None
            raise AdapterError("Node-RED did not answer with JSON.", code="not_json",
                               hint="The address of the Node-RED editor, usually port 1880.") from failure

    async def _flows(self, config: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        answer = await self._get(config, ctx, "/flows")
        flows = answer.get("flows") if isinstance(answer, dict) else answer
        nodes = await self._get(config, ctx, "/nodes") or []
        return [one for one in (flows or []) if isinstance(one, dict)], [one for one in nodes if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        await self._token(config, ctx, fresh=True)
        flows, _ = await self._flows(config, ctx)
        tabs = sum(1 for one in flows if one.get("type") == "tab")
        return f"Node-RED answers, with {tabs} flow{'s' if tabs != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        flows, nodes = await self._flows(config, ctx)
        if widget_kind == "flows":
            return self._list(flows, nodes, max(1, int(options.get("limit") or 10)))
        state = await self._get(config, ctx, "/flows/state", optional=True)
        diagnostics = await self._get(config, ctx, "/diagnostics", optional=True)
        return self._status(flows, nodes, state if isinstance(state, dict) else None, diagnostics if isinstance(diagnostics, dict) else None)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(flows: list[dict[str, Any]], nodes: list[dict[str, Any]], state: dict[str, Any] | None, diagnostics: dict[str, Any] | None) -> WidgetData:
        missing = missing_types(flows, nodes)
        tabs = [one for one in flows if one.get("type") == "tab"]
        count = sum(1 for one in flows if one.get("z") and one.get("type") not in NOT_NODES)
        stopped = state is not None and state.get("state") != "start"
        word = "waiting" if missing else "stopped" if stopped else "running"
        secondary: list[dict[str, Any]] = [
            {"label": "Enabled flows", "value": f"{sum(1 for one in tabs if not one.get('disabled'))} / {len(tabs)}"},
            {"label": "Nodes", "value": count},
        ]
        memory = ((diagnostics or {}).get("nodejs") or {}).get("memoryUsage", {}).get("rss")
        if isinstance(memory, (int, float)):
            secondary.append({"label": "Memory", "value": human_bytes(memory), "metric": "memory"})
        version = ((diagnostics or {}).get("runtime") or {}).get("version")
        if version:
            secondary.append({"label": "Version", "value": str(version)})
        return WidgetData(
            status="bad" if missing else "warn" if stopped else "ok",
            primary={"label": "Flows", "value": word},
            secondary=secondary,
            metrics=measured({"memory": float(memory) if isinstance(memory, (int, float)) else None}),
            meta={"notice": f"Missing node types: {', '.join(missing)}" if missing else ""},
        )

    @staticmethod
    def _list(flows: list[dict[str, Any]], nodes: list[dict[str, Any]], limit: int) -> WidgetData:
        installed = {kind for node in nodes for kind in (node.get("types") or [])}
        rows = []
        for tab in (one for one in flows if one.get("type") == "tab"):
            members = [one for one in flows if one.get("z") == tab.get("id") and one.get("type") not in NOT_NODES]
            missing = sorted({str(one.get("type")) for one in members
                              if str(one.get("type")) not in installed and not str(one.get("type")).startswith("subflow:")})
            disabled = bool(tab.get("disabled"))
            rows.append({
                "id": tab.get("id"),
                "title": str(tab.get("label") or tab.get("id") or "?"),
                "subtitle": f"missing {', '.join(missing)}" if missing else f"{len(members)} node{'s' if len(members) != 1 else ''}",
                "value": "disabled" if disabled else "",
                "status": "bad" if missing and not disabled else "unknown" if disabled else "ok",
            })
        order = {"bad": 0, "ok": 1, "unknown": 2}
        rows.sort(key=lambda row: order.get(row["status"], 3))
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "ok", items=rows[:limit],
                          meta={"empty": "No flow yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        flows = [
            # Flow names are the user's own words, never translated.
            *({"id": f"t{index}", "type": "tab", "label": name, "disabled": index == 4}
              for index, name in enumerate(("Lights", "Heating", "Notifications", "Old experiments"), start=1)),
            *({"id": f"n{index}", "type": "inject" if index % 3 else "function", "z": f"t{1 + index % 4}"} for index in range(42)),
        ]
        nodes = [{"types": ["inject", "function", "debug"]}]
        if widget_kind == "flows":
            return self._list(flows, nodes, max(1, int(options.get("limit") or 10)))
        memory = fake.walk("nodered-rss", tick, 95e6, 140e6)
        return self._status(flows, nodes, {"state": "start"}, {"nodejs": {"memoryUsage": {"rss": memory}}, "runtime": {"version": "5.0.8"}})


ADAPTER = NodeRedAdapter()
