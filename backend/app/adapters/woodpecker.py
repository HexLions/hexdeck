"""Woodpecker CI: what is running and waiting, and which repositories fail.

Checked against Woodpecker 3.19.0 running locally on 21.10.2026 with
Forgejo 17.0.0 as its forge, signed in through Forgejo's OAuth, a personal
token from that session, an agent with the local backend, and two
repositories: one pipeline that passes and one that ends with exit 3.

⚠️ The token is a personal one (the user menu, then Access token in
Woodpecker's interface; ``POST /api/user/token`` from a session) and goes as
``Authorization: Bearer``. A wrong token and no token are both 401 with the
plain-text body ``User not authorized``.

⚠️ ``/api/agents`` hands every agent's **secret** back in ``token``. The card
reads only names and times from it, and the agent list, like
``/api/queue/info``, is for administrators: for anybody else those counts
are left out rather than the card failing.

⚠️ ``queue/info`` counts ``worker_count`` as the idle workers: it read 0 while
the one agent ran a pipeline and 1 again after.

⚠️ A pipeline's ``status`` is one of ``success``, ``failure``, ``error``,
``killed``, ``running``, ``pending``, ``blocked``, ``declined``, ``skipped``
and ``created``. Times are seconds since the epoch. ``/api/user/repos`` gives
every repository with its ``last_pipeline``; ``/api/user/feed`` the latest
pipelines across them, newest first.
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
    ago,
    base_url,
)

FAILED = {"failure", "error", "killed"}
BUSY = {"running", "pending", "blocked"}
#: An agent not heard from for longer than this counts as away.
AGENT_SECONDS = 300
WORDS = {"success": "Passed", "failure": "Failed", "error": "Error", "killed": "Cancelled", "running": "Running",
         "pending": "Waiting", "blocked": "Needs approval", "declined": "Declined", "skipped": "Skipped", "created": "Created"}


def _state(status: str) -> str:
    return "bad" if status in FAILED else "ok" if status in BUSY or status == "success" else "unknown"


class WoodpeckerAdapter(Adapter):
    kind = "woodpecker"
    label = "Woodpecker CI"
    category = "hosts"
    description = "Woodpecker CI: pipelines running and waiting, the repositories whose last pipeline failed, and the latest runs."
    icon = "woodpecker-ci"
    beta = True
    docs_url = "https://woodpecker-ci.org/api"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://ci.example.com"),
        Field("token", "Personal token", secret=True, required=True,
              help="In Woodpecker: your user menu, then Access token. An administrator's token also counts the queue and the agents."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="CI", description="Pipelines running and waiting, failing repositories, and agents.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("running", "failing")),
        WidgetType(kind="repos", label="Repositories", description="Every active repository with how its last pipeline ended, failing ones first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("limit", "Entries", type="number", default=10),)),
        WidgetType(kind="pipelines", label="Latest pipelines", description="The latest pipelines across the repositories, newest first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=30,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 15, optional: bool = False) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/api{path}",
                                     headers={"Authorization": f"Bearer {str(config.get('token') or '').strip()}"},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code == 401:
            raise AuthFailed("Woodpecker refused the token.", hint="Make a new one in Woodpecker: your user menu, then Access token.")
        if response.status_code == 403:
            if optional:
                return None
            raise AuthFailed("The token's user may not see this.", hint="Use the token of a user who can see the repositories.")
        if response.status_code >= 400:
            raise AdapterError(f"Woodpecker answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Woodpecker's own address, without /api.")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way Woodpecker does.", code="not_woodpecker",
                               hint="Check the URL; it is Woodpecker's own address, without /api.") from error

    async def _repos(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        repos = await self._get(config, ctx, "/user/repos", cache=30)
        if not isinstance(repos, list):
            raise AdapterError("This address answers, but not the way Woodpecker does.", code="not_woodpecker")
        return [one for one in repos if isinstance(one, dict) and one.get("active")]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        repos = await self._repos(config, ctx)
        return f"Woodpecker answers with {len(repos)} active repositor{'y' if len(repos) == 1 else 'ies'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "pipelines":
            feed = await self._get(config, ctx, "/user/feed")
            return self._feed(feed if isinstance(feed, list) else [], max(1, int(options.get("limit") or 8)))
        repos = await self._repos(config, ctx)
        if widget_kind == "repos":
            return self._list(repos, max(1, int(options.get("limit") or 10)))
        queue = await self._get(config, ctx, "/queue/info", optional=True)
        agents = await self._get(config, ctx, "/agents", cache=60, optional=True)
        return self._summary(repos, queue, agents)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(repos: list[dict[str, Any]], queue: Any, agents: Any) -> WidgetData:
        failing = sum(1 for one in repos if str((one.get("last_pipeline") or {}).get("status")) in FAILED)
        secondary: list[dict[str, Any]] = [{"label": "Failing", "value": failing}]
        running = None
        if isinstance(queue, dict) and isinstance(queue.get("stats"), dict):
            stats = queue["stats"]
            running = int(stats.get("running_count") or 0)
            secondary.append({"label": "Waiting", "value": int(stats.get("pending_count") or 0) + int(stats.get("waiting_on_deps_count") or 0)})
        else:
            running = sum(1 for one in repos if str((one.get("last_pipeline") or {}).get("status")) in BUSY)
        if isinstance(agents, list):
            now = time.time()
            # Names and times only: the list carries every agent's secret too.
            near = sum(1 for one in agents if isinstance(one, dict) and now - float(one.get("last_contact") or 0) < AGENT_SECONDS)
            secondary.append({"label": "Agents", "value": f"{near} / {len(agents)}"})
        return WidgetData(status="bad" if failing else "ok", primary={"label": "Running", "value": running},
                          secondary=secondary, metrics={"running": float(running), "failing": float(failing)})

    @staticmethod
    def _list(repos: list[dict[str, Any]], limit: int) -> WidgetData:
        def order(one: dict[str, Any]) -> tuple[int, str]:
            status = str((one.get("last_pipeline") or {}).get("status") or "")
            return (0 if status in FAILED else 1 if status in BUSY else 2, str(one.get("full_name") or "").lower())

        items = []
        for one in sorted(repos, key=order)[:limit]:
            last = one.get("last_pipeline") or {}
            status = str(last.get("status") or "")
            when = ago(last.get("finished") or last.get("started") or last.get("created"))
            items.append({
                "title": str(one.get("full_name") or one.get("name") or "?"),
                "subtitle": " · ".join(part for part in (str(last.get("branch") or ""), f"{when} ago" if when else "") if part) if last else "No pipeline yet",
                "status": _state(status) if last else "unknown",
                "value": WORDS.get(status, status) if last else "",
            })
        return WidgetData(status="bad" if any(item["status"] == "bad" for item in items) else "ok", items=items,
                          meta={"empty": "No repository is active in Woodpecker."})

    @staticmethod
    def _feed(feed: list[Any], limit: int) -> WidgetData:
        items = []
        for one in [entry for entry in feed if isinstance(entry, dict)][:limit]:
            status = str(one.get("status") or "")
            when = ago(one.get("finished") or one.get("started") or one.get("created"))
            items.append({
                "title": f"{one.get('full_name') or '?'} #{one.get('number')}",
                "subtitle": " · ".join(part for part in (str(one.get("branch") or ""), str(one.get("event") or ""), f"{when} ago" if when else "") if part),
                "status": _state(status),
                "value": WORDS.get(status, status),
            })
        return WidgetData(status="ok", items=items, meta={"empty": "No pipeline has run yet."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = int(time.time())
        running = tick % 3 == 0
        repos = [
            {"full_name": "lab/hexdeck", "active": True, "last_pipeline": {"status": "running" if running else "success", "branch": "main", "number": 412 + tick // 3, "event": "push", "started": now - 90, "finished": 0 if running else now - 30}},
            {"full_name": "lab/backup-scripts", "active": True, "last_pipeline": {"status": "failure", "branch": "main", "number": 37, "event": "cron", "finished": now - 7200}},
            {"full_name": "lab/website", "active": True, "last_pipeline": {"status": "success", "branch": "main", "number": 88, "event": "push", "finished": now - 86400}},
        ]
        if widget_kind == "repos":
            return self._list(repos, max(1, int(options.get("limit") or 10)))
        if widget_kind == "pipelines":
            feed = [{"full_name": repo["full_name"], **repo["last_pipeline"]} for repo in repos]
            return self._feed(feed, max(1, int(options.get("limit") or 8)))
        queue = {"stats": {"running_count": 1 if running else 0, "pending_count": int(fake.walk("wp-pending", tick, 0, 2.9)), "waiting_on_deps_count": 0}}
        return self._summary(repos, queue, [{"name": "lab-agent", "last_contact": now - 5}, {"name": "pi-agent", "last_contact": now - 12}])


ADAPTER = WoodpeckerAdapter()
