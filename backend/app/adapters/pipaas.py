"""Pi-PaaS: the apps on a Raspberry Pi, which ones run, crash or stop answering.

One read: ``GET /api/summary`` gives the machine (CPU temperature, load,
memory, disk) and every app with its state, port, address, CPU and memory.
The restart button posts to ``/api/apps/<id>/restart``.

⚠️ Sign-in is an API token made in the panel under Settings › API tokens,
sent as ``Authorization: Bearer pp_<id>_<secret>``. A token with the viewer
role can read; the restart button needs one with the operator role, else
Pi-PaaS answers 403 ``This needs the operator role``. A wrong or missing
token gets 401 ``Login required``.

⚠️ An app's ``status`` is ``running``, ``stopped``, ``crashed`` (it crashed
five times in ten minutes and Pi-PaaS stopped restarting it), or a job in
progress: ``deploying``, ``updating``, ``restoring``, ``restarting``,
``stopping``, ``rollingBack``. ``healthy`` is null until the panel's HTTP
health check has run, ``cpu`` is null for the first sample after a start.

⚠️ ``lastError`` is the message of the last failed deploy, update, restore
or rollback, and stays until the next one succeeds. A failed update leaves
the previous version running, and a failed first deploy leaves the app
registered and ``stopped``; either way it is amber, not red, on both cards.
Only ``crashed`` is red.

⚠️ A restart answers 409 for an app with a job in progress, and also when
the app's port is held by another process, which is likely right after a
crash; the card says what Pi-PaaS said. A restart is a stop with up to 8 s
of grace and a start, longer for a Docker app, so it gets 30 s.

⚠️ The card turns amber at 75 °C, the default of Pi-PaaS's own temperature
alert (Settings, Notifications); Pi-PaaS does not send its configured limit.

Checked against Pi-PaaS 1.2.0 (commit 18f88f7, not yet released) running
locally on 2026-11-15, with a Node app running, one that kept crashing, a
stopped static site, a viewer token, an operator token and a wrong token.
``/api/summary`` first appears in Pi-PaaS 1.2.0.
"""

from __future__ import annotations

from typing import Any

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
    human_bytes,
)

READ_SECONDS = 20
BUSY = ("deploying", "updating", "restoring", "restarting", "stopping", "rollingBack")
WORDS = {"rollingBack": "rolling back"}


def app_state(app: dict[str, Any]) -> str:
    """ok, warn, bad or unknown for one app."""
    status = str(app.get("status") or "")
    if status == "crashed":
        return "bad"
    if status in BUSY or app.get("lastError"):
        return "warn"
    if status == "running":
        return "warn" if app.get("healthy") is False else "ok"
    return "unknown"


class PiPaasAdapter(Adapter):
    kind = "pipaas"
    label = "Pi-PaaS"
    category = "hosts"
    description = "Pi-PaaS: how many apps run on the Raspberry Pi, which crashed or stopped answering, the CPU temperature, and a button to restart an app."
    icon = "lucide:server"
    docs_url = "https://github.com/HexLions/pi-paas"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://raspberrypi.local:9000"),
        Field("token", "API token", type="password", secret=True, required=True,
              help="Made in Pi-PaaS under Settings, API tokens. The viewer role is enough without the restart button."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="Apps running out of all, the ones that crashed or do not answer, and the CPU temperature, memory and free disk.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("running", "temperature")),
        WidgetType(kind="apps", label="Apps", description="Every app with its state, address and memory, the troubled ones first, and a button to restart it.",
                   renderer="list", default_size=(3, 4), refresh_seconds=30,
                   options=(
                       Field("only_troubled", "Only the troubled ones", type="bool", default=False),
                       Field("restarting", "Offer the restart button", type="bool", default=False,
                             help="Needs a token with the operator role. Asks before it restarts an app."),
                       Field("limit", "Entries", type="number", default=12),
                   )),
    )

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {config.get('token') or ''}"}

    @staticmethod
    def _refused(status: int) -> None:
        if status == 401:
            raise AuthFailed("Pi-PaaS refused the API token.", hint="A token from Settings, API tokens in the Pi-PaaS panel, pasted whole (pp_…).")
        if status == 403:
            raise AuthFailed("Pi-PaaS refused this request.", hint="A token whose role allows it.")
        if status == 404:
            raise AdapterError("Pi-PaaS has nothing at this address.", code="http_error",
                               hint="The panel's address, for example http://raspberrypi.local:9000, of Pi-PaaS 1.2.0 or newer (not yet released).")
        if status >= 400:
            raise AdapterError(f"Pi-PaaS answered with HTTP {status}.", code="http_error")

    async def _summary(self, config: dict[str, Any], ctx: Context, cache: float = READ_SECONDS) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}/api/summary", headers=self._headers(config),
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        self._refused(response.status_code)
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Pi-PaaS did not answer with JSON.", code="not_json",
                               hint="The panel's address, for example http://raspberrypi.local:9000.") from failure
        if not isinstance(answer, dict) or not isinstance(answer.get("apps"), list):
            raise AdapterError("This does not look like Pi-PaaS.", code="unexpected",
                               hint="Pi-PaaS 1.2.0 or newer (not yet released), at the panel's address.")
        return answer

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        summary = await self._summary(config, ctx, cache=0)
        apps = len(summary["apps"])
        return f"Pi-PaaS {summary.get('version', '?')} answers with {apps} app{'s' if apps != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        summary = await self._summary(config, ctx)
        if widget_kind == "apps":
            return self._list(summary, options)
        return self._overview(summary)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id != "restart":
            raise AdapterError("Unknown action.", code="no_such_action")
        app_id = str(params.get("app") or "")
        # Only an app Pi-PaaS itself lists, never an id the browser made up.
        apps = {str(app.get("id")): app for app in (await self._summary(config, ctx, cache=0))["apps"] if isinstance(app, dict)}
        if app_id not in apps:
            raise AdapterError("Pi-PaaS has no such app.", code="no_such_app")
        response = await ctx.request("POST", f"{base_url(config)}/api/apps/{app_id}/restart", headers=self._headers(config),
                                     verify=not config.get("insecure"), auth_errors=False, timeout=30)
        if response.status_code == 403:
            raise AuthFailed("This Pi-PaaS token may not restart apps.", hint="A token with the operator role.")
        if response.status_code == 404:
            raise AdapterError("Pi-PaaS no longer has this app.", code="no_such_app")
        if response.status_code == 409:
            try:
                said = str((response.json() or {}).get("error") or "")
            except (ValueError, AttributeError):
                said = ""
            if not said or said.startswith("App is busy"):
                raise AdapterError("Pi-PaaS is busy with this app.", code="busy", hint="Try again when its job has finished.")
            raise AdapterError(f"Pi-PaaS refused: {said}", code="refused")
        self._refused(response.status_code)
        ctx.forget_answers()
        return f"{apps[app_id].get('name') or app_id} restarted."

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(summary: dict[str, Any]) -> WidgetData:
        apps = [app for app in summary["apps"] if isinstance(app, dict)]
        running = sum(1 for app in apps if app.get("status") == "running")
        crashed = [str(app.get("name") or app.get("id")) for app in apps if app.get("status") == "crashed"]
        silent = [str(app.get("name") or app.get("id")) for app in apps if app.get("status") == "running" and app.get("healthy") is False]
        failed = [str(app.get("name") or app.get("id")) for app in apps if app.get("status") != "crashed" and app.get("lastError")]
        system = summary.get("system") or {}
        temp = system.get("temperature")
        memory = system.get("memory") or {}
        disk = system.get("disk") or {}
        secondary: list[dict[str, Any]] = [{"label": "Apps", "value": len(apps)}]
        if crashed:
            secondary.append({"label": "Crashed", "value": len(crashed)})
        if silent:
            secondary.append({"label": "Not responding", "value": len(silent)})
        if failed:
            secondary.append({"label": "Last job failed", "value": len(failed)})
        if isinstance(temp, (int, float)):
            secondary.append({"label": "CPU", "value": round(float(temp)), "unit": "°C", "metric": "temperature"})
        if memory.get("total"):
            secondary.append({"label": "Memory", "value": f"{human_bytes(memory['total'] - memory.get('free', 0))} / {human_bytes(memory['total'])}"})
        if disk.get("free") is not None:
            secondary.append({"label": "Disk free", "value": human_bytes(disk["free"])})
        notice = ""
        if crashed:
            notice = f"Crashed: {', '.join(crashed[:3])}{' …' if len(crashed) > 3 else ''}"
        elif silent:
            notice = f"Not responding: {', '.join(silent[:3])}{' …' if len(silent) > 3 else ''}"
        elif failed:
            notice = f"Last job failed: {', '.join(failed[:3])}{' …' if len(failed) > 3 else ''}"
        hot = isinstance(temp, (int, float)) and temp >= 75
        return WidgetData(
            status="bad" if crashed else "warn" if silent or failed or hot else "ok",
            primary={"label": "Running", "value": f"{running}/{len(apps)}", "metric": "running"},
            secondary=secondary,
            metrics={"running": float(running), **({"temperature": float(temp)} if isinstance(temp, (int, float)) else {})},
            meta={"notice": notice},
        )

    @staticmethod
    def _list(summary: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        rows = []
        apps = sorted((app for app in summary["apps"] if isinstance(app, dict)),
                      key=lambda app: (order[app_state(app)], str(app.get("name") or "")))
        for app in apps:
            state = app_state(app)
            if options.get("only_troubled") and state not in ("bad", "warn"):
                continue
            status = str(app.get("status") or "?")
            words = "not responding" if status == "running" and app.get("healthy") is False else WORDS.get(status, status)
            memory = human_bytes(app["memory"]) if isinstance(app.get("memory"), (int, float)) else ""
            actions = []
            if options.get("restarting") and status in ("running", "crashed"):
                actions = [Action(id="restart", label="Restart", icon="rotate-cw", confirm=True, params={"app": str(app.get("id"))}).model_dump()]
            rows.append({
                "id": str(app.get("id")),
                "title": str(app.get("name") or app.get("id")),
                "subtitle": " · ".join(part for part in (words, f"port {app['port']}" if app.get("port") else "", memory,
                                                         str(app.get("lastError") or "")) if part),
                "status": state,
                "url": app.get("url") or None,
                "actions": actions,
            })
        limit = max(1, int(options.get("limit") or 12))
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "warn" if any(row["status"] == "warn" for row in rows) else "ok",
                          items=rows[:limit], meta={"empty": "No troubled app" if options.get("only_troubled") else "No app deployed yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        crashing = fake.flicker("pipaas-crash", tick, 0.25)
        apps = [
            {"id": "photo-blog", "name": "Photo blog", "status": "running", "port": 3001, "url": "http://photo-blog.local/", "memory": 48_000_000, "healthy": True},
            {"id": "home-api", "name": "Home API", "status": "running", "port": 3002, "url": "http://home-api.local/", "memory": 71_000_000, "healthy": True},
            {"id": "weather-bot", "name": "Weather bot", "status": "crashed" if crashing else "running", "port": 3003, "url": "http://weather-bot.local/",
             "memory": None if crashing else 39_000_000, "healthy": None},
            {"id": "link-hub", "name": "Link hub", "status": "stopped", "port": 3004, "url": "http://link-hub.local/", "memory": None, "healthy": None},
        ]
        summary = {"version": "1.2.0", "apps": apps, "system": {"temperature": round(fake.walk("pipaas-temp", tick, 46, 58), 1),
                                                                 "memory": {"total": 4_294_967_296, "free": 2_900_000_000},
                                                                 "disk": {"free": 21_000_000_000}}}
        if widget_kind == "apps":
            return self._list(summary, options)
        return self._overview(summary)


ADAPTER = PiPaasAdapter()
