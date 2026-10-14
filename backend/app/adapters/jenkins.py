"""Jenkins: which jobs fail, what runs, what waits, and which agents are gone.

Jenkins describes everything as JSON under ``/api/json`` and gives exactly
the fields named in ``tree``. Three reads: the jobs (``/api/json``), the
build queue (``/queue/api/json``) and the agents with their executors
(``/computer/api/json``).

⚠️ A job's ``color`` is its state: ``blue`` passed, ``red`` failed,
``yellow`` unstable, ``aborted``, ``disabled``, ``notbuilt``, and any of
them with ``_anime`` while a build runs.

⚠️ Sign-in is a user name with an API token (the user's Security page),
sent as HTTP Basic. A request with an API token needs no CSRF crumb, so the
"Build now" button posts straight to ``/job/<name>/build``. Anonymous
reading is refused with 403 on most installations, a wrong token with 401.

⚠️ Jobs inside a folder are read one level deep. The button builds a job
by its full name, and only a job the last answer listed.

Checked against Jenkins 2.580.1 running locally on 2026-10-13, with five
freestyle jobs (passed, failed, running with a second build waiting,
disabled), an agent that never connected, an API token and a wrong one.
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
    ago,
    base_url,
    duration_short,
)

JOB_FIELDS = "name,fullName,url,color,buildable,lastBuild[number,result,timestamp,duration,building],healthReport[score]"
#: One level of folders, the same fields for what is inside.
JOBS_TREE = f"jobs[{JOB_FIELDS},jobs[{JOB_FIELDS}]]"
QUEUE_TREE = "items[id,why,stuck,blocked,task[name]]"
AGENTS_TREE = "busyExecutors,totalExecutors,computer[displayName,offline,temporarilyOffline,offlineCauseReason]"
READ_SECONDS = 30


def flatten(jobs: list[Any]) -> list[dict[str, Any]]:
    """The jobs, with the ones inside a folder in place of the folder."""
    found: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("jobs"), list) and "color" not in job:
            found.extend(one for one in job["jobs"] if isinstance(one, dict) and "color" in one)
        elif "color" in job:
            found.append(job)
    return found


def state_of(job: dict[str, Any]) -> tuple[str, bool]:
    """(passed, failed, unstable, aborted, disabled or never built; whether a build runs)."""
    color = str(job.get("color") or "notbuilt")
    running = color.endswith("_anime") or bool((job.get("lastBuild") or {}).get("building"))
    base = color.removesuffix("_anime")
    return {"blue": "passed", "red": "failed", "yellow": "unstable", "aborted": "aborted",
            "disabled": "disabled", "grey": "never built", "notbuilt": "never built"}.get(base, base), running


def job_path(full_name: str) -> str:
    return "".join(f"/job/{quote(part, safe='')}" for part in full_name.split("/") if part)


class JenkinsAdapter(Adapter):
    kind = "jenkins"
    label = "Jenkins"
    category = "hosts"
    description = "Jenkins: which jobs fail, what is building and waiting, the agents that are offline, and a button to build a job."
    icon = "jenkins"
    docs_url = "https://www.jenkins.io/doc/book/using/remote-access-api/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://jenkins:8080"),
        Field("username", "User name", help="The Jenkins user the API token belongs to."),
        Field("token", "API token", type="password", secret=True,
              help="Made under the user's Security page, Add new token. A user who may only read is enough without the build button."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many jobs fail, what is building and waiting, the executors in use and the agents that are offline.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("failing", "queue")),
        WidgetType(kind="jobs", label="Jobs", description="Every job with its last build, the failing ones first, and a button to build it.",
                   renderer="list", default_size=(3, 4), refresh_seconds=60,
                   options=(
                       Field("only_failing", "Only the failing ones", type="bool", default=False),
                       Field("building", "Offer the build button", type="bool", default=False,
                             help="Needs a user who may build. Asks before it starts a build."),
                       Field("limit", "Entries", type="number", default=12),
                   )),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, tree: str, cache: float = READ_SECONDS) -> dict[str, Any]:
        response = await ctx.request("GET", f"{base_url(config)}{path}", params={"tree": tree}, auth=self._auth(config),
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        self._refused(response.status_code, config)
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Jenkins did not answer with JSON.", code="not_json",
                               hint="The address you open Jenkins at, with its path if it runs under one.") from failure
        return answer if isinstance(answer, dict) else {}

    @staticmethod
    def _auth(config: dict[str, Any]) -> tuple[str, str] | None:
        if config.get("username") and config.get("token"):
            return (str(config["username"]), str(config["token"]))
        return None

    @staticmethod
    def _refused(status: int, config: dict[str, Any]) -> None:
        if status == 401:
            raise AuthFailed("Jenkins refused the user name or the API token.", hint="An API token from the user's Security page, not the password.")
        if status == 403:
            raise AuthFailed("Jenkins does not let this user read." if config.get("token") else "Jenkins does not let anonymous users read.",
                             hint="A user name with an API token from the user's Security page.")
        if status == 404:
            raise AdapterError("Jenkins has nothing at this address.", code="http_error",
                               hint="The address you open Jenkins at, with its path if it runs under one.")
        if status >= 400:
            raise AdapterError(f"Jenkins answered with HTTP {status}.", code="http_error")

    async def _jobs(self, config: dict[str, Any], ctx: Context, cache: float = READ_SECONDS) -> list[dict[str, Any]]:
        return flatten((await self._get(config, ctx, "/api/json", JOBS_TREE, cache)).get("jobs") or [])

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        jobs = await self._jobs(config, ctx, cache=0)
        return f"Jenkins answers with {len(jobs)} job{'s' if len(jobs) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        jobs = await self._jobs(config, ctx)
        if widget_kind == "jobs":
            return self._list(jobs, options)
        queue = (await self._get(config, ctx, "/queue/api/json", QUEUE_TREE)).get("items") or []
        agents = await self._get(config, ctx, "/computer/api/json", AGENTS_TREE)
        return self._overview(jobs, queue, agents)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id != "build":
            raise AdapterError("Unknown action.", code="no_such_action")
        name = str(params.get("job") or "")
        # Only a job Jenkins itself lists, never an address the browser made up.
        known = {str(job.get("fullName") or job.get("name")) for job in await self._jobs(config, ctx, cache=0)}
        if name not in known:
            raise AdapterError("Jenkins has no such job.", code="no_such_job")
        response = await ctx.request("POST", f"{base_url(config)}{job_path(name)}/build", auth=self._auth(config),
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.status_code == 403:
            raise AuthFailed("This Jenkins user may not build.", hint="A user with the Build permission on the job.")
        self._refused(response.status_code, config)
        ctx.cache.clear()
        return f"Build of {name} started."

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(jobs: list[dict[str, Any]], queue: list[Any], agents: dict[str, Any]) -> WidgetData:
        states = [state_of(job) for job in jobs]
        failing = sum(1 for state, _ in states if state == "failed")
        unstable = sum(1 for state, _ in states if state == "unstable")
        running = sum(1 for _, building in states if building)
        computers = [one for one in agents.get("computer") or [] if isinstance(one, dict)]
        offline = [str(one.get("displayName") or "?") for one in computers if one.get("offline")]
        stuck = sum(1 for item in queue if isinstance(item, dict) and item.get("stuck"))
        busy, total = agents.get("busyExecutors"), agents.get("totalExecutors")
        secondary: list[dict[str, Any]] = [
            {"label": "Jobs", "value": len(jobs)},
            {"label": "Building", "value": running},
            {"label": "Queue", "value": len(queue), "metric": "queue"},
        ]
        if isinstance(busy, int) and isinstance(total, int):
            secondary.append({"label": "Executors", "value": f"{busy}/{total}"})
        if offline:
            secondary.append({"label": "Offline", "value": len(offline)})
        notice = ""
        if offline:
            notice = f"Offline: {', '.join(offline[:3])}{' …' if len(offline) > 3 else ''}"
        if stuck:
            notice = f"{stuck} build{'s are' if stuck != 1 else ' is'} stuck in the queue."
        return WidgetData(
            status="bad" if failing else "warn" if unstable or offline or stuck else "ok",
            primary={"label": "Failing", "value": failing, "metric": "failing"},
            secondary=secondary,
            metrics={"failing": float(failing), "queue": float(len(queue))},
            meta={"notice": notice},
        )

    @staticmethod
    def _list(jobs: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        order = {"failed": 0, "unstable": 1, "aborted": 2, "passed": 3, "never built": 4, "disabled": 5}
        rows = []
        for job in sorted(jobs, key=lambda one: (order.get(state_of(one)[0], 3), str(one.get("fullName") or one.get("name")))):
            state, running = state_of(job)
            if options.get("only_failing") and state not in ("failed", "unstable"):
                continue
            last = job.get("lastBuild") or {}
            name = str(job.get("fullName") or job.get("name") or "?")
            when = ago((last.get("timestamp") or 0) / 1000) if last.get("timestamp") else ""
            took = duration_short(last["duration"] / 1000) if (last.get("duration") or 0) >= 1000 and not running else ""
            actions = []
            if options.get("building") and job.get("buildable") and state != "disabled":
                actions = [Action(id="build", label="Build now", icon="play", confirm=True, params={"job": name}).model_dump()]
            rows.append({
                "id": name,
                "title": name,
                "subtitle": " · ".join(part for part in ("building" if running else state, f"#{last['number']}" if last.get("number") else "",
                                                         f"{when} ago" if when else "", took) if part),
                "status": {"failed": "bad", "unstable": "warn", "aborted": "warn", "passed": "ok"}.get(state, "unknown"),
                "actions": actions,
            })
        limit = max(1, int(options.get("limit") or 12))
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "ok", items=rows[:limit],
                          meta={"empty": "No failing job" if options.get("only_failing") else "No job"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = 1_790_000_000_000 + tick * 15_000
        failing = fake.flicker("jenkins-red", tick, 0.3)
        jobs = [
            {"name": "backup-nightly", "color": "blue", "buildable": True, "lastBuild": {"number": 412, "timestamp": now - 6 * 3_600_000, "duration": 184_000}},
            {"name": "deploy-website", "color": "red" if failing else "blue", "buildable": True, "lastBuild": {"number": 88, "timestamp": now - 2_400_000, "duration": 51_000}},
            {"name": "integration-tests", "color": "blue_anime", "buildable": True, "lastBuild": {"number": 1203, "timestamp": now - 300_000, "building": True}},
            {"name": "lint", "color": "blue", "buildable": True, "lastBuild": {"number": 1530, "timestamp": now - 900_000, "duration": 22_000}},
            {"name": "old-release", "color": "disabled", "buildable": False},
        ]
        if widget_kind == "jobs":
            return self._list(jobs, options)
        return self._overview(jobs, [{"id": 7, "why": "Waiting for next available executor"}],
                              {"busyExecutors": 1, "totalExecutors": 4, "computer": [{"displayName": "Built-In Node", "offline": False}]})


ADAPTER = JenkinsAdapter()
