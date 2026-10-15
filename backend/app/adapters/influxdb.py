"""InfluxDB 2: whether it is healthy, which tasks failed on their last run, the buckets.

InfluxDB 2's API takes a token (Load Data, API Tokens) as ``Authorization:
Token``; one that may read buckets and tasks of the organisation is
enough. ``/health`` answers without one and names the version,
``/api/v2/tasks`` lists the tasks with ``lastRunStatus`` (``success``,
``failed``, ``canceled``) and ``lastRunError``, ``/api/v2/buckets`` the
buckets with their retention.

⚠️ A task that never ran has no ``lastRunStatus``; an ``inactive`` one keeps
the result of its last run.

⚠️ Buckets of type ``system`` (``_monitoring``, ``_tasks``) are InfluxDB's
own and left out. A retention of 0 seconds means forever.

⚠️ This is the v2 API. InfluxDB 1 and 3 answer differently.

Checked against InfluxDB v2.9.1 running locally on 2026-10-15, with two
buckets, a task that succeeds, one that fails on a missing bucket, one
switched off, a read-only token and a wrong one.
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
    ago,
    base_url,
    duration_short,
)

READ_SECONDS = 60


def _failed(task: dict[str, Any]) -> bool:
    return task.get("status") == "active" and task.get("lastRunStatus") == "failed"


class InfluxDBAdapter(Adapter):
    kind = "influxdb"
    label = "InfluxDB"
    category = "monitoring"
    description = "InfluxDB 2: whether it is healthy, which tasks failed on their last run and why, and the buckets with their retention."
    icon = "influxdb"
    docs_url = "https://docs.influxdata.com/influxdb/v2/api/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://influxdb:8086"),
        Field("org", "Organisation", required=True, placeholder="home", help="The organisation's name, as InfluxDB shows it."),
        Field("token", "API token", type="password", secret=True, required=True,
              help="Under Load Data, API Tokens. One that may read the organisation's buckets and tasks is enough."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="Whether InfluxDB is healthy, how many tasks failed on their last run, and the buckets.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("failing",)),
        WidgetType(kind="tasks", label="Tasks", description="Each task with its last run, the failing ones first with InfluxDB's error.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("only_failing", "Only the failing ones", type="bool", default=False),)),
        WidgetType(kind="buckets", label="Buckets", description="The organisation's buckets with how long each keeps its data.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None, cache: float = READ_SECONDS) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}{path}", params=params,
                                     headers={"Accept": "application/json", "Authorization": f"Token {config.get('token') or ''}"},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("InfluxDB refused the token.", hint="A token from Load Data, API Tokens that may read buckets and tasks.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError(f"InfluxDB answered with HTTP {response.status_code} and no JSON.", code="not_json",
                               hint="InfluxDB 2's address, usually port 8086.") from failure
        message = str(answer.get("message") or "") if isinstance(answer, dict) else ""
        # ⚠️ An unknown organisation is 404 for buckets but 400 for tasks; both say so in words.
        if "organization name" in message and "not found" in message:
            raise AdapterError("InfluxDB has no organisation of that name.", code="no_such_org", hint="The organisation's name, as InfluxDB shows it.")
        if response.status_code >= 400 and response.status_code != 503:
            raise AdapterError(f"InfluxDB answered: {message or f'HTTP {response.status_code}'}.", code="http_error",
                               hint="InfluxDB 2's address; InfluxDB 1 and 3 answer differently.")
        return answer if isinstance(answer, dict) else {}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        health = await self._get(config, ctx, "/health", cache=0)
        tasks = (await self._get(config, ctx, "/api/v2/tasks", {"org": config.get("org")}, cache=0)).get("tasks") or []
        return f"InfluxDB {health.get('version') or ''} answers, {len(tasks)} task{'s' if len(tasks) != 1 else ''}.".replace("InfluxDB  ", "InfluxDB ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        org = {"org": config.get("org")}
        if widget_kind == "buckets":
            return self._buckets((await self._get(config, ctx, "/api/v2/buckets", org, 600)).get("buckets") or [])
        tasks = [task for task in (await self._get(config, ctx, "/api/v2/tasks", org)).get("tasks") or [] if isinstance(task, dict)]
        if widget_kind == "tasks":
            return self._tasks(tasks, options)
        health = await self._get(config, ctx, "/health")
        buckets = (await self._get(config, ctx, "/api/v2/buckets", org, 600)).get("buckets") or []
        return self._status(health, tasks, buckets)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(health: dict[str, Any], tasks: list[dict[str, Any]], buckets: list[Any]) -> WidgetData:
        failing = [task for task in tasks if _failed(task)]
        healthy = health.get("status") == "pass"
        secondary: list[dict[str, Any]] = [
            {"label": "Tasks", "value": f"{sum(1 for task in tasks if task.get('status') == 'active')}/{len(tasks)}"},
            {"label": "Buckets", "value": sum(1 for bucket in buckets if isinstance(bucket, dict) and bucket.get("type") != "system")},
        ]
        if health.get("version"):
            secondary.append({"label": "Version", "value": str(health["version"])})
        notice = "" if healthy else str(health.get("message") or "InfluxDB is not healthy.")
        if healthy and failing:
            notice = f"Failed last time: {', '.join(sorted(str(task.get('name')) for task in failing)[:4])}"
        return WidgetData(
            status="bad" if not healthy or failing else "ok",
            primary={"label": "Failing tasks", "value": len(failing), "metric": "failing"},
            secondary=secondary,
            metrics={"failing": float(len(failing))},
            meta={"notice": notice},
        )

    @staticmethod
    def _tasks(tasks: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        rows = []
        for task in tasks:
            failed = _failed(task)
            if options.get("only_failing") and not failed:
                continue
            when = ago(task.get("latestCompleted")) if task.get("lastRunStatus") else ""
            state = "inactive" if task.get("status") != "active" else str(task.get("lastRunStatus") or "not run yet")
            every = f"every {task['every']}" if task.get("every") else str(task.get("cron") or "")
            rows.append({
                "id": str(task.get("id") or task.get("name")),
                "title": str(task.get("name") or "?"),
                "subtitle": " · ".join(part for part in (state, f"{when} ago" if when else "", every,
                                                         str(task.get("lastRunError") or "")[:160] if failed else "") if part),
                "status": "bad" if failed else "unknown" if task.get("status") != "active" else "ok",
            })
        rows.sort(key=lambda row: ({"bad": 0, "ok": 1}.get(row["status"], 2), row["title"]))
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "ok", items=rows,
                          meta={"empty": "No failing task" if options.get("only_failing") else "No task"})

    @staticmethod
    def _buckets(buckets: list[Any]) -> WidgetData:
        rows = []
        for bucket in buckets:
            if not isinstance(bucket, dict) or bucket.get("type") == "system":
                continue
            seconds = min((int(rule.get("everySeconds") or 0) for rule in bucket.get("retentionRules") or [] if isinstance(rule, dict)), default=0)
            rows.append({"id": str(bucket.get("id") or bucket.get("name")), "title": str(bucket.get("name") or "?"),
                         "value": f"keeps {duration_short(seconds)}" if seconds else "keeps forever", "status": "ok"})
        return WidgetData(status="ok", items=sorted(rows, key=lambda row: row["title"]), meta={"empty": "No bucket"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        failing = fake.flicker("influx-red", tick, 0.3)
        tasks = [{"id": "1", "name": "downsample cpu", "status": "active", "every": "1m", "lastRunStatus": "success", "latestCompleted": ""},
                 {"id": "2", "name": "copy to archive", "status": "active", "every": "1h", "lastRunStatus": "failed" if failing else "success",
                  "lastRunError": 'could not find bucket "archive"', "latestCompleted": ""},
                 {"id": "3", "name": "old export", "status": "inactive", "every": "1d"}]
        buckets = [{"name": "homelab", "retentionRules": [{"everySeconds": 2_592_000}]}, {"name": "downsampled", "retentionRules": [{"everySeconds": 0}]}]
        if widget_kind == "tasks":
            return self._tasks(tasks, options)
        if widget_kind == "buckets":
            return self._buckets(buckets)
        return self._status({"status": "pass", "version": "v2.9.1"}, tasks, buckets)


ADAPTER = InfluxDBAdapter()
