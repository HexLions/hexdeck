"""GitLab: a project's pipelines, its open merge requests and how it stands.

REST API v4, read-only, on GitLab.com or on a self-managed instance at its own
address: ``/projects/:id`` for the project, ``/projects/:id/pipelines`` for the
pipelines and ``/projects/:id/merge_requests?state=opened`` for the merge
requests. ``/version`` names the instance in the connection test.

⚠️ A project is named by its path, ``group/subgroup/name``, and the path goes
into the address URL-encoded: ``/projects/group%2Fname``. Sent with the slashes
as they are, GitLab answers 404, which reads exactly like a project that does
not exist.

⚠️ The token goes in as ``PRIVATE-TOKEN``. A personal, project or group access
token with the ``read_api`` scope is enough. A public project on GitLab.com
answers without one, so the token is optional; ``/version`` does not.

⚠️ The number of open merge requests comes from the ``x-total`` header of a
one-row page. GitLab.com may leave that header out, and then the card counts
the page of up to a hundred it fetched instead.

Read from GitLab's own API documentation (doc/api/pipelines.md,
merge_requests.md, projects.md, metadata.md and rest/_index.md) on
2026-10-07, GitLab 19.x.
"""

from __future__ import annotations

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
    measured,
)

#: A pipeline's status, as a colour and, where it is not plain success, a word.
#: Still going is neither good nor bad yet, like a run on the GitHub card.
PIPELINE = {
    "success": ("ok", "passed"), "failed": ("bad", "failed"), "canceled": ("warn", "canceled"),
    "canceling": ("warn", "canceling"), "skipped": ("unknown", "skipped"), "manual": ("warn", "manual"),
    "running": ("unknown", "running"), "pending": ("unknown", "pending"), "created": ("unknown", "created"),
    "preparing": ("unknown", "preparing"), "scheduled": ("unknown", "scheduled"),
    "waiting_for_resource": ("unknown", "waiting"), "waiting_for_callback": ("unknown", "waiting"),
}
#: Why a merge request cannot go in yet, for the statuses worth a colour.
BLOCKED = {
    "conflict": ("bad", "conflict"), "need_rebase": ("warn", "needs rebase"),
    "ci_must_pass": ("warn", "pipeline must pass"), "ci_still_running": ("unknown", "pipeline running"),
    "discussions_not_resolved": ("warn", "open threads"), "not_approved": ("warn", "needs approval"),
    "requested_changes": ("warn", "changes requested"), "draft_status": ("unknown", "draft"),
    "mergeable": ("ok", "ready"),
}
PROJECT_SECONDS = 300
PIPELINES_SECONDS = 60
MERGE_REQUESTS_SECONDS = 300
PROJECT_OPTION = Field("project", "Project", placeholder="group/name", help="Empty takes the connection's project.")


def project_path(written: Any) -> str:
    """What was typed, as GitLab wants it in an address.

    Takes ``group/name``, a numeric id, or the project's whole address as it
    was copied from the browser, and answers the URL-encoded path or the id.
    """
    text = str(written or "").strip().strip("/")
    if "://" in text:
        # The host goes; what follows it is the project.
        text = text.split("://", 1)[1].partition("/")[2]
    # A copied address may carry the page after the project: /-/merge_requests.
    text = text.split("/-/", 1)[0].removesuffix(".git").strip("/")
    return text if text.isdigit() else quote(text, safe="")


class GitLabAdapter(Adapter):
    kind = "gitlab"
    label = "GitLab"
    category = "hosts"
    description = "The pipelines, the open merge requests and the open issues of a project on GitLab.com or your own GitLab."
    icon = "gitlab"
    docs_url = "https://docs.gitlab.com/api/rest/"
    fields = (
        Field("url", "URL", type="url", required=True, default="https://gitlab.com", placeholder="https://gitlab.com",
              help="GitLab.com, or the address of your own GitLab without /api."),
        Field("token", "Access token", type="password", secret=True,
              help="A personal, project or group access token with the read_api scope. A public project on GitLab.com answers without one."),
        Field("project", "Project", required=True, placeholder="group/name",
              help="The project's path as in its address, group/name, or its number. A card can name another one."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False,
              help="For a GitLab of your own behind a certificate of its own."),
    )
    widgets = (
        WidgetType(kind="project", label="Project", description="The last pipeline on the default branch, the open merge requests and the open issues.",
                   renderer="stats", default_size=(3, 2), refresh_seconds=300, metrics=("merge_requests", "issues"),
                   options=(PROJECT_OPTION,)),
        WidgetType(kind="pipelines", label="Pipelines", description="The latest pipelines with their branch and how they ended, a red one marked.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120, metrics=("failing",),
                   options=(PROJECT_OPTION,
                            Field("ref", "Branch or tag", placeholder="main", help="Only the pipelines of this branch or tag. Empty means all of them."),
                            Field("limit", "Entries", type="number", default=8))),
        WidgetType(kind="merge_requests", label="Merge requests", description="The open merge requests, with what still stands between each and the target branch.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300, metrics=("open",),
                   options=(PROJECT_OPTION,
                            Field("hide_drafts", "Hide drafts", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=8))),
    )

    # -- talking to GitLab -----------------------------------------------------

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None,
                   cache: float = 0) -> Any:
        token = str(config.get("token") or "").strip()
        response = await ctx.request(
            "GET", f"{base_url(config)}/api/v4{path}",
            headers={"PRIVATE-TOKEN": token, "Accept": "application/json"} if token else {"Accept": "application/json"},
            params=params, verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False,
        )
        if response.status_code in (401, 403):
            raise AuthFailed(
                "GitLab refused the access token." if token else "GitLab wants an access token for this.",
                hint="A personal, project or group access token with the read_api scope. One that has expired or was revoked is refused the same way.",
            )
        if response.status_code == 404:
            # ⚠️ GitLab answers 404 for a project the token may not see as well
            # as for one that does not exist, so the sentence names both.
            raise AdapterError("GitLab knows no such project, or the token may not see it.", code="not_found",
                               hint="The project is its path as in its address, group/name, or its number under Settings > General.")
        if response.status_code >= 400:
            raise AdapterError(f"GitLab answered with HTTP {response.status_code}.", code="http_error")
        return response

    async def _json(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None,
                    cache: float = 0) -> Any:
        response = await self._get(config, ctx, path, params, cache)
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("GitLab did not answer with JSON.", code="not_json",
                               hint="The address is GitLab itself, such as https://gitlab.com, without /api.") from failure

    @staticmethod
    def _project(config: dict[str, Any], options: dict[str, Any]) -> str:
        path = project_path(options.get("project") or config.get("project"))
        if not path:
            raise AdapterError("No project is named.", code="no_project",
                               hint="Name one on the connection, as group/name, or on the card.")
        return path

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        project = await self._json(config, ctx, f"/projects/{self._project(config, {})}")
        name = str((project or {}).get("path_with_namespace") or (project or {}).get("name") or "the project")
        said = ""
        if str(config.get("token") or "").strip():
            version = await self._json(config, ctx, "/version")
            if isinstance(version, dict) and version.get("version"):
                said = f", GitLab {version['version']}"
        return f"GitLab answers with {name}{said}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        path = self._project(config, options)
        if widget_kind == "pipelines":
            params: dict[str, Any] = {"per_page": max(1, min(100, int(options.get("limit") or 8)))}
            if str(options.get("ref") or "").strip():
                params["ref"] = str(options["ref"]).strip()
            answer = await self._json(config, ctx, f"/projects/{path}/pipelines", params, PIPELINES_SECONDS)
            return self._pipelines(answer if isinstance(answer, list) else [], options)
        if widget_kind == "merge_requests":
            response = await self._get(config, ctx, f"/projects/{path}/merge_requests",
                                       {"state": "opened", "order_by": "updated_at", "per_page": 100}, MERGE_REQUESTS_SECONDS)
            try:
                answer = response.json()
            except ValueError:
                answer = []
            written = response.headers.get("x-total")
            # ⚠️ A page holds a hundred at most; a project with more said 100.
            return self._merge_requests(answer if isinstance(answer, list) else [], options,
                                        int(written) if written and str(written).isdigit() else None)
        project = await self._json(config, ctx, f"/projects/{path}", None, PROJECT_SECONDS)
        project = project if isinstance(project, dict) else {}
        branch = str(project.get("default_branch") or "")
        latest = await self._json(config, ctx, f"/projects/{path}/pipelines",
                                  {"per_page": 1, **({"ref": branch} if branch else {})}, PIPELINES_SECONDS)
        counted = await self._get(config, ctx, f"/projects/{path}/merge_requests", {"state": "opened", "per_page": 1}, MERGE_REQUESTS_SECONDS)
        return self._summary(project, latest[0] if isinstance(latest, list) and latest else None, self._total(counted))

    @staticmethod
    def _total(response: Any) -> int | None:
        """The number of rows GitLab says there are, or what the page holds when it does not say."""
        written = response.headers.get("x-total")
        if written and str(written).isdigit():
            return int(written)
        try:
            rows = response.json()
        except ValueError:
            return None
        # ⚠️ One row was asked for, so without the header one row says only "at least one".
        return len(rows) if isinstance(rows, list) and not rows else None

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _pipeline_state(pipeline: dict[str, Any]) -> tuple[str, str]:
        status = str(pipeline.get("status") or "")
        return PIPELINE.get(status, ("unknown", status or "?"))

    @classmethod
    def _summary(cls, project: dict[str, Any], latest: dict[str, Any] | None, merge_requests: int | None) -> WidgetData:
        colour, word = cls._pipeline_state(latest) if latest else ("unknown", "")
        issues = project.get("open_issues_count")
        secondary: list[dict[str, Any]] = []
        if merge_requests is not None:
            secondary.append({"label": "Merge requests", "value": merge_requests, "metric": "merge_requests"})
        # ⚠️ Absent, not nought, when the project has issues switched off.
        if isinstance(issues, int):
            secondary.append({"label": "Open issues", "value": issues, "metric": "issues"})
        if project.get("default_branch"):
            secondary.append({"label": "Branch", "value": str(project["default_branch"])})
        return WidgetData(
            status="bad" if colour == "bad" else "ok",
            primary={"label": "Pipeline", "value": word or "none yet"},
            secondary=secondary,
            metrics=measured({"merge_requests": float(merge_requests) if merge_requests is not None else None,
                              "issues": float(issues) if isinstance(issues, int) else None}),
            link=str(project.get("web_url") or "") or None,
        )

    @classmethod
    def _pipelines(cls, pipelines: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        rows = []
        for pipeline in pipelines:
            if not isinstance(pipeline, dict):
                continue
            colour, word = cls._pipeline_state(pipeline)
            parts = [str(pipeline.get("ref") or ""), str(pipeline.get("sha") or "")[:8], str(pipeline.get("source") or "")]
            when = ago(pipeline.get("updated_at") or pipeline.get("created_at"))
            rows.append({
                "id": pipeline.get("id"),
                "title": str(pipeline.get("name") or f"#{pipeline.get('iid') or pipeline.get('id') or '?'}"),
                "subtitle": " · ".join(part for part in (*parts, when) if part),
                "value": word,
                "status": colour,
                "url": pipeline.get("web_url"),
            })
        rows = rows[: max(1, int(options.get("limit") or 8))]
        failing = sum(1 for row in rows if row["status"] == "bad")
        return WidgetData(
            # The newest says how the project stands; an old red one that was fixed since does not.
            status="bad" if rows and rows[0]["status"] == "bad" else "ok",
            items=rows,
            metrics=measured({"failing": float(failing)}),
            meta={"empty": "No pipeline yet" if not str(options.get("ref") or "").strip() else f"No pipeline on {options.get('ref')}"},
        )

    @classmethod
    def _merge_requests(cls, merge_requests: list[dict[str, Any]], options: dict[str, Any], total: int | None = None) -> WidgetData:
        rows = []
        for one in merge_requests:
            if not isinstance(one, dict):
                continue
            if options.get("hide_drafts") and one.get("draft"):
                continue
            status = str(one.get("detailed_merge_status") or "")
            colour, word = BLOCKED.get(status, ("unknown", status.replace("_", " ")))
            if one.get("has_conflicts"):
                colour, word = BLOCKED["conflict"]
            elif one.get("draft"):
                colour, word = BLOCKED["draft_status"]
            author = str((one.get("author") or {}).get("username") or "")
            parts = [f"!{one.get('iid')}" if one.get("iid") else "", author, str(one.get("source_branch") or ""),
                     ago(one.get("updated_at"))]
            rows.append({
                "id": one.get("id"),
                "title": str(one.get("title") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "value": word,
                "status": colour,
                "url": one.get("web_url"),
            })
        shown = rows[: max(1, int(options.get("limit") or 8))]
        # The header counts drafts too, so it stands only while drafts are shown.
        count = total if total is not None and not options.get("hide_drafts") else len(rows)
        return WidgetData(
            status="warn" if any(row["status"] == "bad" for row in rows) else "ok",
            items=shown,
            primary={"label": "Open", "value": count},
            metrics=measured({"open": float(count)}),
            meta={"empty": "No open merge request"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        branch = fake.pick("gl-branch", tick, ["main", "main", "feature/drawings", "fix/topology"], every=30)
        running = fake.flicker("gl-running", tick, 0.4)
        pipelines = [
            {"id": 812, "iid": 412, "status": "running" if running else "success", "ref": "main", "sha": "3f9c2e1a7b",
             "source": "push", "name": "Build and test", "updated_at": "", "web_url": ""},
            {"id": 811, "iid": 411, "status": "failed", "ref": branch if branch != "main" else "fix/topology", "sha": "a1d04b9e33",
             "source": "merge_request_event", "name": "Build and test", "updated_at": "", "web_url": ""},
            {"id": 810, "iid": 410, "status": "success", "ref": "main", "sha": "77e10c2f91", "source": "schedule",
             "name": "Nightly", "updated_at": "", "web_url": ""},
            {"id": 809, "iid": 409, "status": "manual", "ref": "v0.22.0", "sha": "c0ffee1234", "source": "push",
             "name": "Release", "updated_at": "", "web_url": ""},
        ]
        merge_requests = [
            {"id": 1, "iid": 57, "title": "Draw the energy flow on a phone", "author": {"username": "alex"}, "source_branch": "feature/flow",
             "detailed_merge_status": "mergeable", "draft": False, "has_conflicts": False, "updated_at": "", "web_url": ""},
            {"id": 2, "iid": 56, "title": "Cut long names in the map", "author": {"username": "sam"}, "source_branch": "fix/topology",
             "detailed_merge_status": "ci_must_pass", "draft": False, "has_conflicts": False, "updated_at": "", "web_url": ""},
            {"id": 3, "iid": 54, "title": "Spanish for the new cards", "author": {"username": "noa"}, "source_branch": "i18n/es",
             "detailed_merge_status": "need_rebase", "draft": False, "has_conflicts": True, "updated_at": "", "web_url": ""},
            {"id": 4, "iid": 51, "title": "A card for Keycloak", "author": {"username": "alex"}, "source_branch": "feature/keycloak",
             "detailed_merge_status": "draft_status", "draft": True, "has_conflicts": False, "updated_at": "", "web_url": ""},
        ]
        if widget_kind == "pipelines":
            return self._pipelines(pipelines, options)
        if widget_kind == "merge_requests":
            return self._merge_requests(merge_requests, options)
        project = {"path_with_namespace": "homelab/hexdeck", "default_branch": "main",
                   "open_issues_count": fake.counter("gl-issues", tick, 12, 0.0) % 20 + 4, "web_url": ""}
        return self._summary(project, pipelines[0], len(merge_requests))


ADAPTER = GitLabAdapter()
