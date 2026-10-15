"""Cronicle: which scheduled jobs failed last time, what runs now, and a button to run one.

Cronicle's REST API lives under ``/api/app/<call>/v1`` and takes an API key
(Admin, API Keys) in an ``X-API-Key`` header. ``get_schedule`` lists the
events, ``get_active_jobs`` the jobs running now, ``get_history`` the jobs
that finished, newest first; ``run_event`` starts one.

⚠️ Every answer is HTTP 200; success is ``"code": 0`` in the body, and an
error is a ``code`` such as ``"api"`` or ``"session"`` with a
``description``. A wrong key reads ``"Invalid API Key: ..."``.

⚠️ Only the master server answers the API. A worker, or a cluster that has
not chosen a master yet, answers ``"code": "master"``.

⚠️ An event's last result is its newest job in the history; ``code`` 0 is
success, anything else a failure with the reason in ``description``.

Checked against Cronicle 0.9.136 running locally on 2026-10-15, with four
shell events (one succeeding, one failing with exit code 3, one running,
one disabled), an API key and a wrong one.
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
    ago,
    base_url,
)

READ_SECONDS = 30
#: Finished jobs read to find each event's last result.
HISTORY = 100


class CronicleAdapter(Adapter):
    kind = "cronicle"
    label = "Cronicle"
    category = "hosts"
    description = "Cronicle's scheduled jobs: which failed last time and why, what runs now, and a button to run an event."
    icon = "lucide:clock"
    docs_url = "https://github.com/jhuckaby/Cronicle/blob/master/docs/APIReference.md"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://cronicle:3012",
              help="The address of the master server; a worker does not answer the API."),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="Under Admin, API Keys. Reading needs no privilege; the run button needs Run Events."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many events failed on their last run, what runs now, and the events switched on.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("failing",)),
        WidgetType(kind="events", label="Events", description="Each event with its last run and result, the failing ones first, and a button to run it.",
                   renderer="list", default_size=(3, 4), refresh_seconds=60,
                   options=(
                       Field("only_failing", "Only the failing ones", type="bool", default=False),
                       Field("running", "Offer the run button", type="bool", default=False,
                             help="Needs a key with Run Events. Asks before it starts a job."),
                   )),
    )

    async def _call(self, config: dict[str, Any], ctx: Context, call: str, cache: float, params: dict[str, Any] | None = None,
                    body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{base_url(config)}/api/app/{call}/v1"
        headers = {"Accept": "application/json", "X-API-Key": str(config.get("api_key") or "")}
        if body is None:
            response = await ctx.request("GET", url, params=params, headers=headers, verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        else:
            response = await ctx.request("POST", url, json_body=body, headers=headers, verify=not config.get("insecure"), auth_errors=False)
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError(f"Cronicle answered with HTTP {response.status_code} and no JSON.", code="not_json",
                               hint="The address of Cronicle's master server, usually port 3012.") from failure
        if not isinstance(answer, dict):
            raise AdapterError("Cronicle answered with something unexpected.", code="bad_answer")
        code, description = answer.get("code"), str(answer.get("description") or "")
        if code == 0:
            return answer
        if description.startswith("Invalid API Key") or code == "session":
            raise AuthFailed("Cronicle refused the API key.", hint="A key from Admin, API Keys, switched on.")
        if code == "master":
            raise AdapterError("This Cronicle is not the master server.", code="not_master", hint="The address of the master; only it answers the API.")
        raise AdapterError(f"Cronicle answered: {description or code}.", code="api_error")

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        events = (await self._call(config, ctx, "get_schedule", 0)).get("rows") or []
        return f"Cronicle answers with {len(events)} event{'s' if len(events) != 1 else ''}."

    async def _state(self, config: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        events = [one for one in (await self._call(config, ctx, "get_schedule", READ_SECONDS)).get("rows") or [] if isinstance(one, dict)]
        active = [one for one in ((await self._call(config, ctx, "get_active_jobs", READ_SECONDS)).get("jobs") or {}).values() if isinstance(one, dict)]
        history = [one for one in (await self._call(config, ctx, "get_history", READ_SECONDS, {"offset": 0, "limit": HISTORY})).get("rows") or []
                   if isinstance(one, dict)]
        return events, active, history

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        events, active, history = await self._state(config, ctx)
        if widget_kind == "events":
            return self._events(events, active, history, options)
        return self._overview(events, active, history)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id != "run":
            raise AdapterError("Unknown action.", code="no_such_action")
        wanted = str(params.get("event") or "")
        # Only an event Cronicle itself lists, never an id the browser made up.
        events = {str(one.get("id")): one for one in (await self._call(config, ctx, "get_schedule", 0)).get("rows") or [] if isinstance(one, dict)}
        if wanted not in events:
            raise AdapterError("Cronicle has no such event.", code="no_such_event")
        await self._call(config, ctx, "run_event", 0, body={"id": wanted})
        ctx.cache.clear()
        return f"{events[wanted].get('title') or wanted} started."

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _last(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        last: dict[str, dict[str, Any]] = {}
        for job in history:
            last.setdefault(str(job.get("event")), job)
        return last

    @classmethod
    def _overview(cls, events: list[dict[str, Any]], active: list[dict[str, Any]], history: list[dict[str, Any]]) -> WidgetData:
        last = cls._last(history)
        failing = [str(event.get("title")) for event in events if last.get(str(event.get("id")), {}).get("code") not in (None, 0)]
        enabled = sum(1 for event in events if event.get("enabled"))
        return WidgetData(
            status="bad" if failing else "ok",
            primary={"label": "Failing", "value": len(failing), "metric": "failing"},
            secondary=[{"label": "Running", "value": len(active)}, {"label": "Events", "value": f"{enabled}/{len(events)}"}],
            metrics={"failing": float(len(failing))},
            meta={"notice": f"Failed last time: {', '.join(sorted(failing)[:4])}" if failing else ""},
        )

    @classmethod
    def _events(cls, events: list[dict[str, Any]], active: list[dict[str, Any]], history: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        last = cls._last(history)
        running = {str(job.get("event")): job for job in active}
        rows = []
        for event in events:
            identifier = str(event.get("id"))
            job = last.get(identifier) or {}
            now = running.get(identifier)
            failed = job.get("code") not in (None, 0)
            if options.get("only_failing") and not failed:
                continue
            when = ago(job.get("time_start")) if job else ""
            if now is not None:
                started = ago(now.get("time_start"))
                parts = ["running", f"for {started}" if started else ""]
            elif not event.get("enabled"):
                parts = ["disabled"]
            elif job:
                parts = [f"failed: {job.get('description') or job.get('code')}" if failed else "succeeded", f"{when} ago" if when else ""]
            else:
                parts = ["not run yet"]
            actions = []
            if options.get("running") and event.get("enabled") and now is None:
                actions = [Action(id="run", label="Run now", icon="play", confirm=True, params={"event": identifier}).model_dump()]
            rows.append({
                "id": identifier,
                "title": str(event.get("title") or identifier),
                "subtitle": " · ".join(part for part in parts if part),
                "status": "bad" if failed else "warn" if now is not None else "unknown" if not event.get("enabled") or not job else "ok",
                "actions": actions,
            })
        rows.sort(key=lambda row: ({"bad": 0, "warn": 1, "ok": 2}.get(row["status"], 3), row["title"]))
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "ok", items=rows,
                          meta={"empty": "No failing event" if options.get("only_failing") else "No event"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        failing = fake.flicker("cronicle-red", tick, 0.3)
        events = [{"id": "e1", "title": "Nightly backup", "enabled": 1}, {"id": "e2", "title": "Renew certificates", "enabled": 1},
                  {"id": "e3", "title": "Media scan", "enabled": 1}, {"id": "e4", "title": "Old cleanup", "enabled": 0}]
        history = [{"event": "e2", "code": 3 if failing else 0, "description": "Script exited with code: 3: cannot reach ACME", "time_start": 0},
                   {"event": "e1", "code": 0, "time_start": 0}]
        active = [{"event": "e3", "time_start": 0}]
        if widget_kind == "events":
            return self._events(events, active, history, options)
        return self._overview(events, active, history)


ADAPTER = CronicleAdapter()
