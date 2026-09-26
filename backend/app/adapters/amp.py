"""AMP (CubeCoders): every game server on the panel, and the buttons to run them.

AMP has one shape of call for everything: ``POST /API/<Module>/<Method>`` with a
JSON body that carries the parameters and ``SESSIONID``. Nothing here is a REST
path, and there are no API keys: a session comes from ``Core/Login`` with an
account's own credentials.

Two things about the panel are worth knowing before reading the code:

* The controller (ADS) knows its instances through ``ADSModule/GetInstances``,
  which answers one entry per target, each carrying its ``AvailableInstances``.
  On a single-instance installation that call does not exist, and the address is
  the game server itself; that case falls back to ``Core/GetStatus`` and the
  cards show the one server.
* Numbers for one server come from *that server's* session, not the
  controller's. ``ADSModule/Servers/<id>/API/Core/Login`` proxies a login
  through the controller, and the session it hands back is used against
  ``ADSModule/Servers/<id>/API/Core/...``.

⚠️ A session expires after a few minutes of silence. Both kinds are kept a
little under that, and a call that comes back unauthenticated logs in again once
before it gives up.

⚠️ AMP has no service accounts and no API tokens, so this is a real account with
whatever that account may do. Make one for HexDeck, give it the panel rights it
needs and no more, and do not put two-factor on it: the second factor is a code
per login, which nothing unattended can produce.

⚠️ An error is a 200 with ``Title``, ``Message`` and ``StackTrace`` in it. Read
as an ordinary answer it looks like a server with no metrics.
"""

from __future__ import annotations

import time
from typing import Any

from . import demo as fake
from .base import (
    Action,
    Adapter,
    AdapterError,
    Context,
    Deed,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    human_bytes,
    measured,
)

#: AMP's own state numbers. The names are the ones the panel shows.
STATES = {
    -1: "Undefined", 0: "Stopped", 5: "Pre-start", 7: "Configuring", 10: "Starting",
    20: "Ready", 30: "Restarting", 40: "Stopping", 45: "Preparing to sleep", 50: "Sleeping",
    60: "Waiting", 70: "Installing", 75: "Updating", 80: "Awaiting input", 100: "Failed",
    200: "Suspended", 250: "Maintenance", 999: "Indeterminate",
}
#: What each state is worth as a colour. A stopped server is not a fault.
COLOURS = {
    0: "unknown", 50: "unknown", 200: "unknown", 250: "unknown", 999: "unknown",
    20: "ok",
    5: "warn", 7: "warn", 10: "warn", 30: "warn", 40: "warn", 45: "warn", 60: "warn", 70: "warn", 75: "warn", 80: "warn",
    100: "bad", -1: "unknown",
}
#: The metrics AMP reports, under the names it gives them.
CPU_METRIC = "CPU Usage"
MEMORY_METRIC = "Memory Usage"
USERS_METRIC = "Active Users"
#: Sessions go stale quietly; the official clients log in again every five minutes.
SESSION_SECONDS = 240
INSTANCES_SECONDS = 30
STATUS_SECONDS = 15
#: What a button may do, and nothing else.
POWER = {"start": "Start", "stop": "Stop", "restart": "Restart"}


def metric(status: dict[str, Any], name: str) -> dict[str, Any]:
    found = (status.get("Metrics") or {}).get(name)
    return found if isinstance(found, dict) else {}


def percent(status: dict[str, Any], name: str) -> float | None:
    """A metric's share, from the panel's own percentage or from the two values."""
    one = metric(status, name)
    if isinstance(one.get("Percent"), (int, float)):
        return round(float(one["Percent"]), 1)
    raw, ceiling = one.get("RawValue"), one.get("MaxValue")
    if isinstance(raw, (int, float)) and isinstance(ceiling, (int, float)) and ceiling:
        return round(100.0 * float(raw) / float(ceiling), 1)
    return None


class AmpAdapter(Adapter):
    kind = "amp"
    label = "AMP"
    category = "hosts"
    description = "Every game server on a CubeCoders AMP panel, what it is doing, and buttons to start and stop it."
    icon = "amp"
    beta = True
    docs_url = "https://discourse.cubecoders.com/t/amp-api-basics/4699"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://amp.lan:8080",
              help="The panel. On a controller installation this is ADS; a single game server's own address works too."),
        Field("username", "User name", required=True, placeholder="hexdeck",
              help="An account on the panel. AMP has no API tokens, so make one for HexDeck with only the rights it needs."),
        Field("password", "Password", type="password", secret=True, required=True,
              help="Without two-factor: the second factor is a code per login, which nothing unattended can produce."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    deeds = (
        Deed(widget_kind="instances", id="start", label="Start a server",
             target_field="instance", target_label="Server", icon="play"),
        Deed(widget_kind="instances", id="stop", label="Stop a server",
             target_field="instance", target_label="Server", icon="power"),
        Deed(widget_kind="instances", id="restart", label="Restart a server",
             target_field="instance", target_label="Server", icon="rotate-cw"),
    )
    widgets = (
        WidgetType(kind="instances", label="Servers", description="Every instance the panel knows, what it runs and what state it is in.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60, metrics=("running",),
                   options=(
                       Field("running_only", "Only what is running", type="bool", default=False),
                       Field("switching", "Offer start and stop", type="bool", default=False,
                             help="Adds a button per row. Stopping a server puts everybody on it off, so it asks first."),
                       Field("limit", "Entries", type="number", default=12),
                   )),
        WidgetType(kind="instance", label="Server", description="One server with its state, its processor and memory use, and who is on it.",
                   renderer="stats", default_size=(4, 2), refresh_seconds=30, metrics=("cpu", "memory", "players"),
                   options=(
                       Field("instance", "Server", type="choices", required=True,
                             help="The instance to watch. The list comes from the panel."),
                       Field("switching", "Offer start and stop", type="bool", default=False),
                   )),
        WidgetType(kind="panel", label="Panel", description="How many servers are up, and what the panel's own machine is doing.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("running", "cpu")),
    )

    # -- talking to AMP -------------------------------------------------------

    @staticmethod
    def _api(config: dict[str, Any], prefix: str = "") -> str:
        return f"{base_url(config)}/API{prefix}"

    async def _post(self, config: dict[str, Any], ctx: Context, endpoint: str, payload: dict[str, Any],
                    prefix: str = "") -> Any:
        response = await ctx.request(
            "POST", f"{self._api(config, prefix)}/{endpoint}", json_body=payload,
            headers={"Accept": "text/javascript"}, verify=not config.get("insecure"), auth_errors=False,
        )
        if response.status_code == 404:
            raise AdapterError(f"This panel has no {endpoint}.", code="no_such_method",
                               hint="Is the address the panel itself rather than something in front of it?")
        if response.status_code >= 400:
            raise AdapterError(f"AMP answered with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("AMP did not answer with JSON.", code="not_json",
                               hint="The address has to be the panel's own, the one its web interface is on.") from failure
        # ⚠️ AMP reports a failure as a 200 with these three keys in it.
        if isinstance(answer, dict) and "StackTrace" in answer and "Message" in answer:
            raise AdapterError(f"AMP refused the call: {answer.get('Message') or answer.get('Title')}", code="amp_error")
        if isinstance(answer, dict) and len(answer) == 1 and "result" in answer:
            return answer["result"]
        return answer

    async def _login(self, config: dict[str, Any], ctx: Context, prefix: str = "") -> str:
        answer = await self._post(config, ctx, "Core/Login", {
            "username": str(config.get("username") or ""),
            "password": str(config.get("password") or ""),
            "token": "", "rememberMe": False,
        }, prefix)
        if not isinstance(answer, dict) or not answer.get("success") or not answer.get("sessionID"):
            reason = (answer or {}).get("resultReason") if isinstance(answer, dict) else ""
            raise AdapterError(f"AMP refused these credentials{f': {reason}' if reason else ''}.", code="auth_failed",
                               hint="An account with two-factor cannot be used here; the second factor is a code per login.")
        return str(answer["sessionID"])

    async def _session(self, config: dict[str, Any], ctx: Context, instance: str = "") -> str:
        """The panel's session, or a session proxied to one instance."""
        key = f"amp:session:{instance}" if instance else "amp:session"
        kept = ctx.cache.get(key)
        if kept and kept[0] > time.monotonic():
            return str(kept[1])
        # ⚠️ The proxied address carries its own /API: the controller's call is
        # /API/ADSModule/Servers/<id>/API/Core/Login, not one /API short of it.
        session = await self._login(config, ctx, f"/ADSModule/Servers/{instance}/API" if instance else "")
        ctx.cache[key] = (time.monotonic() + SESSION_SECONDS, session)
        return session

    async def _call(self, config: dict[str, Any], ctx: Context, endpoint: str, payload: dict[str, Any] | None = None,
                    instance: str = "", again: bool = True) -> Any:
        prefix = f"/ADSModule/Servers/{instance}/API" if instance else ""
        session = await self._session(config, ctx, instance)
        try:
            return await self._post(config, ctx, endpoint, {**(payload or {}), "SESSIONID": session}, prefix)
        except AdapterError as failure:
            # A session that has gone stale is an ordinary refusal, so the only
            # way to tell it from a real one is to log in again and see.
            if failure.code in ("amp_error", "auth_failed") and again:
                ctx.cache.pop(f"amp:session:{instance}" if instance else "amp:session", None)
                return await self._call(config, ctx, endpoint, payload, instance, again=False)
            raise

    async def _instances(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        """Every instance the controller knows, flattened across its targets.

        A single-instance installation has no ADSModule, and there the panel
        *is* the game server: one entry, read from its own status.
        """
        kept = ctx.cache.get("amp:instances")
        if kept and kept[0] > time.monotonic():
            return list(kept[1])
        try:
            targets = await self._call(config, ctx, "ADSModule/GetInstances", {"ForceIncludeSelf": True})
        except AdapterError as failure:
            if failure.code not in ("no_such_method", "amp_error"):
                raise
            targets = None
        found: list[dict[str, Any]] = []
        if isinstance(targets, list):
            for target in targets:
                if not isinstance(target, dict):
                    continue
                where = str(target.get("FriendlyName") or "")
                for instance in target.get("AvailableInstances") or []:
                    if isinstance(instance, dict):
                        found.append({**instance, "Target": where})
        else:
            status = await self._call(config, ctx, "Core/GetStatus")
            state = status.get("State") if isinstance(status, dict) else None
            found.append({
                "InstanceID": "", "InstanceName": "", "FriendlyName": str(config.get("url") or "This server"),
                "Module": "", "Running": state == 20, "AppState": state, "Target": "", "Alone": True,
            })
        ctx.cache["amp:instances"] = (time.monotonic() + INSTANCES_SECONDS, found)
        return found

    async def _status(self, config: dict[str, Any], ctx: Context, instance: dict[str, Any]) -> dict[str, Any]:
        """One instance's own status, through the controller or straight from it."""
        if instance.get("Alone"):
            answer = await self._call(config, ctx, "Core/GetStatus")
        else:
            answer = await self._call(config, ctx, "Core/GetStatus", instance=str(instance.get("InstanceID") or ""))
        return answer if isinstance(answer, dict) else {}

    async def choices(self, field: str, config: dict[str, Any], ctx: Context) -> list[tuple[str, str]]:
        """The servers on the panel, for the field that picks one."""
        if field != "instance":
            return []
        found = await self._instances(config, ctx)
        return [(str(one.get("InstanceName") or one.get("InstanceID") or ""),
                 str(one.get("FriendlyName") or one.get("InstanceName") or "?")) for one in found]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        found = await self._instances(config, ctx)
        if len(found) == 1 and found[0].get("Alone"):
            return "AMP answers as a single game server, without a controller."
        running = sum(1 for one in found if one.get("Running"))
        return f"AMP answers with {len(found)} instances, {running} of them running."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        found = await self._instances(config, ctx)
        if widget_kind == "instance":
            wanted = str(options.get("instance") or "").strip()
            chosen = next((one for one in found
                           if wanted in (str(one.get("InstanceName") or ""), str(one.get("InstanceID") or ""))), None)
            if chosen is None and wanted:
                raise AdapterError(f"This panel has no server called {wanted!r}.", code="no_such_instance",
                                   hint="Pick it again in the card's settings; an instance that was deleted keeps its name nowhere.")
            chosen = chosen or (found[0] if found else {})
            return self._one(chosen, await self._status(config, ctx, chosen) if chosen else {}, options)
        if widget_kind == "panel":
            return self._panel(found, await self._call(config, ctx, "Core/GetStatus"))
        return self._list(found, options)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id not in POWER:
            raise AdapterError("Unknown action.", code="no_such_action")
        name = str(params.get("instance") or options.get("instance") or "")
        if not name:
            raise AdapterError("No server was named.", code="missing_param")
        found = await self._instances(config, ctx)
        alone = len(found) == 1 and found[0].get("Alone")
        if alone:
            # Nothing to address: the panel is the server.
            await self._call(config, ctx, f"Core/{POWER[action_id]}")
        else:
            answer = await self._call(config, ctx, f"ADSModule/{POWER[action_id]}Instance", {"InstanceName": name})
            if isinstance(answer, dict) and answer.get("Status") is False:
                raise AdapterError(f"AMP would not {action_id} {name}: {answer.get('Reason') or 'no reason given'}.",
                                   code="refused")
        ctx.cache.pop("amp:instances", None)
        return f"{POWER[action_id]} sent to {name}."

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _state_of(instance: dict[str, Any]) -> tuple[int, str, str]:
        raw = instance.get("AppState")
        number = int(raw) if isinstance(raw, (int, float)) else (20 if instance.get("Running") else 0)
        return number, STATES.get(number, "Unknown"), COLOURS.get(number, "unknown")

    @classmethod
    def _buttons(cls, instance: dict[str, Any], switching: bool) -> list[Action]:
        if not switching:
            return []
        name = str(instance.get("InstanceName") or instance.get("InstanceID") or "")
        number, _word, _colour = cls._state_of(instance)
        if number == 20:
            # ⚠️ Stopping a game server puts everybody on it off, so it asks.
            return [Action(id="stop", label="Stop", icon="power", confirm=True, params={"instance": name}),
                    Action(id="restart", label="Restart", icon="rotate-cw", confirm=True, params={"instance": name})]
        return [Action(id="start", label="Start", icon="play", params={"instance": name})]

    @classmethod
    def _list(cls, found: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        switching = bool(options.get("switching"))
        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        rows = []
        for instance in found:
            _number, word, colour = cls._state_of(instance)
            if options.get("running_only") and colour != "ok":
                continue
            module = str(instance.get("ModuleDisplayName") or instance.get("Module") or "")
            rows.append({"colour": colour, "row": {
                "id": str(instance.get("InstanceID") or ""),
                "title": str(instance.get("FriendlyName") or instance.get("InstanceName") or "?"),
                "subtitle": " · ".join(part for part in (module, str(instance.get("Target") or "")) if part),
                "value": word,
                "status": colour,
                "actions": [action.model_dump() for action in cls._buttons(instance, switching)],
            }})
        ranked = sorted(rows, key=lambda one: (order.get(one["colour"], 4), one["row"]["title"].lower()))
        running = sum(1 for one in rows if one["colour"] == "ok")
        return WidgetData(
            status="bad" if any(one["colour"] == "bad" for one in rows) else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 12))],
            primary={"label": "Running", "value": f"{running} / {len(rows)}"},
            metrics=measured({"running": float(running)}),
            meta={"empty": "This panel has no servers yet."},
        )

    @classmethod
    def _one(cls, instance: dict[str, Any], status: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        _number, word, colour = cls._state_of({**instance, "AppState": status.get("State", instance.get("AppState"))})
        cpu = percent(status, CPU_METRIC)
        memory = percent(status, MEMORY_METRIC)
        users = metric(status, USERS_METRIC)
        secondary: list[dict[str, Any]] = [{"label": "State", "value": word}]
        if cpu is not None:
            secondary.append({"label": "CPU", "value": cpu, "unit": "%", "metric": "cpu"})
        if memory is not None:
            spent = metric(status, MEMORY_METRIC)
            # AMP counts memory in megabytes, which is what its own interface shows.
            hint = (f"{human_bytes(float(spent.get('RawValue') or 0) * 1024 * 1024)} of "
                    f"{human_bytes(float(spent.get('MaxValue') or 0) * 1024 * 1024)}") if spent.get("MaxValue") else ""
            secondary.append({"label": "Memory", "value": memory, "unit": "%", "metric": "memory", "hint": hint})
        players = users.get("RawValue")
        if isinstance(players, (int, float)):
            ceiling = users.get("MaxValue")
            secondary.append({"label": "Players", "value": f"{int(players)} / {int(ceiling)}" if ceiling else int(players),
                              "metric": "players"})
        if status.get("Uptime"):
            secondary.append({"label": "Up", "value": str(status["Uptime"])})
        return WidgetData(
            status=colour,
            primary={"label": str(instance.get("FriendlyName") or instance.get("InstanceName") or "Server"), "value": word},
            secondary=secondary,
            actions=cls._buttons(instance, bool(options.get("switching"))),
            metrics=measured({"cpu": cpu, "memory": memory,
                              "players": float(players) if isinstance(players, (int, float)) else None}),
            meta={"module": str(instance.get("Module") or "")},
        )

    @classmethod
    def _panel(cls, found: list[dict[str, Any]], status: Any) -> WidgetData:
        status = status if isinstance(status, dict) else {}
        running = sum(1 for one in found if cls._state_of(one)[2] == "ok")
        failed = sum(1 for one in found if cls._state_of(one)[2] == "bad")
        cpu = percent(status, CPU_METRIC)
        memory = percent(status, MEMORY_METRIC)
        secondary: list[dict[str, Any]] = [{"label": "Servers", "value": len(found)}]
        if cpu is not None:
            secondary.append({"label": "CPU", "value": cpu, "unit": "%", "metric": "cpu"})
        if memory is not None:
            secondary.append({"label": "Memory", "value": memory, "unit": "%"})
        if failed:
            secondary.append({"label": "Failed", "value": failed})
        return WidgetData(
            status="bad" if failed else "ok",
            primary={"label": "Running", "value": f"{running} / {len(found)}"},
            secondary=secondary,
            metrics=measured({"running": float(running), "cpu": cpu}),
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        instances = [
            {"InstanceID": "a", "InstanceName": "mc01", "FriendlyName": "Minecraft survival", "Module": "Minecraft",
             "ModuleDisplayName": "Minecraft", "Running": True, "AppState": 20, "Target": "local"},
            {"InstanceID": "b", "InstanceName": "valheim01", "FriendlyName": "Valheim", "Module": "GenericModule",
             "ModuleDisplayName": "Valheim", "Running": False, "AppState": 0, "Target": "local"},
            {"InstanceID": "c", "InstanceName": "pal01", "FriendlyName": "Palworld", "Module": "GenericModule",
             "ModuleDisplayName": "Palworld", "Running": True, "AppState": 75, "Target": "node-2"},
        ]
        if widget_kind == "panel":
            return self._panel(instances, {"Metrics": {
                CPU_METRIC: {"Percent": round(fake.walk("amp-cpu", tick, 6, 48), 1)},
                MEMORY_METRIC: {"RawValue": 9800, "MaxValue": 32000, "Percent": 30.6},
            }})
        if widget_kind == "instance":
            return self._one(instances[0], {
                "State": 20, "Uptime": "3.04:12:55", "Metrics": {
                    CPU_METRIC: {"Percent": round(fake.walk("amp-one-cpu", tick, 8, 62), 1)},
                    MEMORY_METRIC: {"RawValue": 4100, "MaxValue": 8192, "Percent": 50.0, "Units": "MB"},
                    USERS_METRIC: {"RawValue": int(fake.walk("amp-players", tick, 0, 9)), "MaxValue": 20},
                }}, options)
        return self._list(instances, options)


ADAPTER = AmpAdapter()
