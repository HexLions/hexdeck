"""GitHub: releases, issues, pull requests, workflow runs and milestones.

Sixty requests an hour per address without a token, five thousand with
one, and a card asks every few minutes. So every answer is kept with its
ETag and asked for again with ``If-None-Match``: the usual reply is a 304,
which GitHub does not count. When the limit is used up all the same, the
card says so, says when it comes back, and shows the last answer it has.

GitHub is read, never written. A repository can be named on the card or
taken from a HexDeck project that links it.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from .base import Adapter, AdapterError, Context, Field, WidgetData, WidgetType

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
#: Where the rate limit's state is kept between fetches of one connection.
LIMIT_KEY = "github:limit"
CACHE_KEY = "github:etag:"

#: Ready-made sets of the projects a home server usually runs.
PRESETS = {
    "media": "jellyfin/jellyfin\nRadarr/Radarr\nSonarr/Sonarr\nsabnzbd/sabnzbd",
    "infrastructure": "home-assistant/core\nNginxProxyManager/nginx-proxy-manager\npi-hole/pi-hole\nAdguardTeam/AdGuardHome",
    "nexapps": "DerKezorm/nexdeck\nDerKezorm/nexview\nDerKezorm/nexmail",
    "": "",
}
PRESET_OPTIONS = (
    ("media", "Media and downloads"),
    ("infrastructure", "Infrastructure"),
    ("nexapps", "nexapps"),
    ("", "Own list only"),
)

SOURCE_FIELDS = (
    Field("project", "Project", type="project", help="Takes the repositories linked to a HexDeck project. Empty uses the list below."),
    Field("repos", "Repositories", type="textarea", placeholder="owner/name", help="One per line, as owner/name."),
)
LIMIT_FIELD = Field("limit", "Entries", type="number", default=8)


class GithubAdapter(Adapter):
    kind = "github"
    label = "GitHub"
    category = "feeds"
    description = "Releases, open issues, pull requests, the last workflow run and the milestones of the repositories you watch."
    icon = "github"
    docs_url = "https://docs.github.com/en/rest"
    needs_integration = False
    #: Optional. Without a token GitHub allows sixty requests an hour per address.
    fields = (
        Field("token", "Personal access token", type="password", secret=True,
              help="Optional, and recommended: five thousand requests an hour instead of sixty, and private repositories. A fine-grained token with read access to issues, pull requests and Actions is enough."),
    )
    widgets = (
        WidgetType(
            kind="releases",
            label="Releases",
            description="One line per project with its newest version and when it came.",
            renderer="feed",
            default_size=(4, 4),
            refresh_seconds=1800,
            options=(
                Field("preset", "Ready-made set", type="select", default="media", options=PRESET_OPTIONS),
                Field("repos", "Own projects", type="textarea", placeholder="owner/name",
                      help="One per line, as owner/name. Replaces the ready-made set."),
                LIMIT_FIELD,
                Field("prereleases", "Include pre-releases", type="bool", default=False),
            ),
        ),
        WidgetType(
            kind="repo",
            label="Repository",
            description="One repository at a glance: open issues, pull requests, the last workflow run and the next milestone.",
            renderer="stats",
            default_size=(3, 2),
            min_size=(3, 2),
            refresh_seconds=600,
            options=(
                Field("repo", "Repository", placeholder="owner/name", help="Empty takes the first repository of the project below."),
                Field("project", "Project", type="project"),
            ),
        ),
        WidgetType(
            kind="issues",
            label="Open issues",
            description="The open issues of the repositories, newest activity first, with their labels.",
            renderer="list",
            default_size=(4, 3),
            refresh_seconds=600,
            options=(*SOURCE_FIELDS, LIMIT_FIELD),
        ),
        WidgetType(
            kind="pulls",
            label="Pull requests",
            description="The open pull requests, draft or ready, and how their review stands.",
            renderer="list",
            default_size=(4, 3),
            refresh_seconds=600,
            options=(*SOURCE_FIELDS, LIMIT_FIELD),
        ),
        WidgetType(
            kind="runs",
            label="Workflow runs",
            description="The latest Actions runs with how they ended.",
            renderer="list",
            default_size=(4, 3),
            refresh_seconds=300,
            options=(*SOURCE_FIELDS, LIMIT_FIELD),
        ),
        WidgetType(
            kind="milestones",
            label="Milestones",
            description="The open milestones of the repositories, with their due date and how far along they are.",
            renderer="list",
            default_size=(4, 3),
            refresh_seconds=1800,
            options=(*SOURCE_FIELDS, LIMIT_FIELD),
        ),
    )

    def default_link(self, config: dict[str, Any]) -> str:
        return "https://github.com/"

    # -- the source of repositories ------------------------------------------

    @staticmethod
    def _repos(options: dict[str, Any], *, presets: bool) -> list[str]:
        own = [line.strip().strip("/") for line in str(options.get("repos") or "").splitlines() if line.strip()]
        project = str(options.get("project") or "")
        if project.isdigit():
            from ..db import db_session
            from ..models import Project

            with db_session() as db:
                found = db.get(Project, int(project))
                linked = [r.repo for r in found.repos] if found else []
            if linked:
                return linked[:10]
        if own:
            return own[:10]
        if presets:
            preset = PRESETS.get(str(options.get("preset") or "media"), "")
            return [line for line in preset.splitlines() if line][:10]
        return []

    # -- one request, with its ETag and the rate limit ---------------------------

    async def _get(self, ctx: Context, token: str, path: str, params: dict[str, Any] | None = None) -> Any:
        """A GitHub answer, or the one remembered when GitHub says it has not changed.

        Raises ``rate_limited`` when the hourly limit is used up and nothing
        is remembered for this address; with something remembered, the
        caller gets it back marked stale through :meth:`_stale`.
        """
        key = CACHE_KEY + path + ("?" + "&".join(f"{k}={v}" for k, v in sorted((params or {}).items())) if params else "")
        remembered = ctx.cache.get(key)
        limit = ctx.cache.get(LIMIT_KEY)
        if limit and limit[0] == 0 and limit[1] > time.time():
            if remembered:
                raise _Stale(remembered["body"])
            raise self._limit_error(limit[1])
        headers = dict(HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if remembered and remembered.get("etag"):
            headers["If-None-Match"] = remembered["etag"]
        response = await ctx.request("GET", f"{API}{path}", headers=headers, params=params, timeout=20, auth_errors=False)
        self._note_limit(ctx, response)
        if response.status_code == 304 and remembered:
            return remembered["body"]
        if response.status_code in (403, 429) and response.headers.get("X-RateLimit-Remaining") == "0" or (
            response.status_code == 403 and "rate limit" in response.text.lower()
        ):
            reset = float(response.headers.get("X-RateLimit-Reset") or time.time() + 3600)
            ctx.cache[LIMIT_KEY] = (0, reset)
            if remembered:
                raise _Stale(remembered["body"])
            raise self._limit_error(reset)
        if response.status_code == 404:
            raise AdapterError("GitHub knows no such repository, or the token may not see it.", code="not_found")
        if response.status_code in (401, 403):
            raise AdapterError("GitHub refused the token.", code="auth_failed")
        if response.status_code >= 400:
            raise AdapterError(f"GitHub answered with HTTP {response.status_code}.", code="http_error")
        body = response.json()
        ctx.cache[key] = {"etag": response.headers.get("ETag", ""), "body": body, "at": time.time()}
        return body

    @staticmethod
    def _note_limit(ctx: Context, response: Any) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None and reset is not None:
            try:
                ctx.cache[LIMIT_KEY] = (int(remaining), float(reset))
            except ValueError:
                pass

    @staticmethod
    def _limit_error(reset: float) -> AdapterError:
        when = datetime.fromtimestamp(reset, UTC).strftime("%H:%M UTC")
        return AdapterError(
            f"GitHub's hourly limit is used up; it comes back at {when}.",
            code="rate_limited",
            hint="Sixty requests an hour without a token. Add a personal access token to the GitHub connection for five thousand, or watch fewer repositories.",
        )

    @staticmethod
    def _stamp(text: str) -> int | None:
        try:
            return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None

    # -- the cards ---------------------------------------------------------------

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        token = str(config.get("token") or "")
        if token:
            me = await self._get(ctx, token, "/user")
            limit = ctx.cache.get(LIMIT_KEY)
            left = f", {limit[0]} requests left this hour" if limit else ""
            return f"GitHub answers as {me.get('login')}{left}."
        await self._get(ctx, "", "/repos/DerKezorm/nexdeck")
        return "GitHub answers, without a token: sixty requests an hour."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        token = str(config.get("token") or "")
        try:
            if widget_kind == "releases":
                return await self._releases_card(ctx, token, options)
            if widget_kind == "repo":
                return await self._repo_card(ctx, token, options)
            if widget_kind == "issues":
                return await self._issues_card(ctx, token, options)
            if widget_kind == "pulls":
                return await self._pulls_card(ctx, token, options)
            if widget_kind == "runs":
                return await self._runs_card(ctx, token, options)
            if widget_kind == "milestones":
                return await self._milestones_card(ctx, token, options)
        except _Stale as stale:
            # The limit is used up and the last answer is what there is. The
            # caller sees the rows it saw before, and a warning that says why.
            return self._from_stale(widget_kind, options, stale)
        raise AdapterError("No such card.", code="no_such_widget")

    def _from_stale(self, widget_kind: str, options: dict[str, Any], stale: _Stale) -> WidgetData:
        limit = stale.reset
        rows = self._rows_for(widget_kind, options, stale.body)
        when = datetime.fromtimestamp(limit, UTC).strftime("%H:%M UTC") if limit else "later"
        return WidgetData(status="warn", items=rows, primary={"label": "Open issues", "value": len(rows)} if widget_kind == "issues" else None,
                          meta={"stale": True, "empty": "Nothing open"},
                          error=f"GitHub's hourly limit is used up; showing the last answer until {when}.")

    def _rows_for(self, widget_kind: str, options: dict[str, Any], body: Any) -> list[dict[str, Any]]:
        repo = "?"
        if widget_kind == "issues":
            return self._issue_rows(repo, body)[: self._limit(options)]
        if widget_kind == "pulls":
            return [self._pull_row(repo, pr, None) for pr in body][: self._limit(options)]
        if widget_kind == "runs":
            return self._run_rows(repo, body)[: self._limit(options)]
        if widget_kind == "milestones":
            return self._milestone_rows(repo, body)[: self._limit(options)]
        return []

    @staticmethod
    def _limit(options: dict[str, Any]) -> int:
        return max(1, min(20, int(options.get("limit") or 8)))

    async def _releases(self, ctx: Context, token: str, repo: str, prereleases: bool) -> dict[str, Any] | None:
        try:
            if prereleases:
                payload = await self._get(ctx, token, f"/repos/{repo}/releases", {"per_page": 1})
                return payload[0] if isinstance(payload, list) and payload else None
            payload = await self._get(ctx, token, f"/repos/{repo}/releases/latest")
            return payload if isinstance(payload, dict) else None
        except AdapterError as error:
            if error.code == "not_found":
                # A project without a release is not a fault; it simply has none.
                return None
            raise

    async def _releases_card(self, ctx: Context, token: str, options: dict[str, Any]) -> WidgetData:
        repos = self._repos(options, presets=True)
        if not repos:
            raise AdapterError("No project is set.", code="missing_repo")
        prereleases = bool(options.get("prereleases"))
        entries: list[dict[str, Any]] = []
        failures: list[str] = []
        for repo in repos:
            try:
                release = await self._releases(ctx, token, repo, prereleases)
            except AdapterError as error:
                failures.append(f"{repo}: {error.message}")
                continue
            if not release:
                continue
            name = str(release.get("tag_name") or release.get("name") or "?")
            notes = str(release.get("body") or "").strip().splitlines()
            entries.append({
                "title": f"{repo.split('/')[-1]} {name}",
                "url": release.get("html_url") or f"https://github.com/{repo}/releases",
                "source": repo + (" · pre-release" if release.get("prerelease") else ""),
                "published": self._stamp(release.get("published_at") or release.get("created_at") or ""),
                "summary": (notes[0][:280] if notes else ""),
                "image": "",
            })
        entries.sort(key=lambda entry: entry["published"] or 0, reverse=True)
        return WidgetData(
            status="ok" if entries else ("bad" if failures else "warn"),
            items=entries[: self._limit(options)],
            meta={"style": "list", "failures": failures},
            error=("; ".join(failures) if failures and not entries else None),
        )

    def _source(self, options: dict[str, Any]) -> list[str]:
        repos = self._repos(options, presets=False)
        if not repos:
            raise AdapterError("No repository is set.", code="missing_repo",
                               hint="Name one as owner/name, or pick a HexDeck project that links one.")
        return repos

    @staticmethod
    def _issue_rows(repo: str, issues: Any) -> list[dict[str, Any]]:
        rows = []
        for issue in issues if isinstance(issues, list) else []:
            if issue.get("pull_request"):
                # GitHub lists pull requests among the issues; the pulls card has them.
                continue
            labels = ", ".join(str(label.get("name")) for label in issue.get("labels") or [])
            who = str((issue.get("user") or {}).get("login") or "")
            rows.append({
                "id": f"{repo}#{issue.get('number')}",
                "title": f"#{issue.get('number')} {issue.get('title')}",
                "subtitle": " · ".join(part for part in (repo if repo != "?" else "", who, labels) if part),
                "url": issue.get("html_url"),
                "status": "warn" if labels and "bug" in labels.lower() else "unknown",
                "value": issue.get("comments") or "",
                "updated": issue.get("updated_at"),
            })
        return rows

    async def _issues_card(self, ctx: Context, token: str, options: dict[str, Any]) -> WidgetData:
        rows: list[dict[str, Any]] = []
        for repo in self._source(options):
            issues = await self._get(ctx, token, f"/repos/{repo}/issues", {"state": "open", "per_page": 30, "sort": "updated"})
            rows.extend(self._issue_rows(repo, issues))
        rows.sort(key=lambda row: str(row.get("updated") or ""), reverse=True)
        return WidgetData(status="ok", items=rows[: self._limit(options)], primary={"label": "Open issues", "value": len(rows)},
                          meta={"empty": "No open issues"}, link=f"https://github.com/{self._source(options)[0]}/issues")

    @staticmethod
    def _pull_row(repo: str, pr: dict[str, Any], reviews: list[dict[str, Any]] | None) -> dict[str, Any]:
        draft = bool(pr.get("draft"))
        states = {str(r.get("state") or "").upper() for r in reviews or []}
        if draft:
            review, status = "draft", "unknown"
        elif "CHANGES_REQUESTED" in states:
            review, status = "changes requested", "warn"
        elif "APPROVED" in states:
            review, status = "approved", "ok"
        elif pr.get("requested_reviewers"):
            review, status = "review requested", "unknown"
        else:
            review, status = "ready", "unknown"
        who = str((pr.get("user") or {}).get("login") or "")
        return {
            "id": f"{repo}#{pr.get('number')}",
            "title": f"#{pr.get('number')} {pr.get('title')}",
            "subtitle": " · ".join(part for part in (repo if repo != "?" else "", who, review) if part),
            "url": pr.get("html_url"),
            "status": status,
            "value": str((pr.get("head") or {}).get("ref") or ""),
            "updated": pr.get("updated_at"),
        }

    async def _pulls_card(self, ctx: Context, token: str, options: dict[str, Any]) -> WidgetData:
        rows: list[dict[str, Any]] = []
        for repo in self._source(options):
            pulls = await self._get(ctx, token, f"/repos/{repo}/pulls", {"state": "open", "per_page": 20, "sort": "updated", "direction": "desc"})
            for pr in pulls if isinstance(pulls, list) else []:
                reviews = None
                # One more request per pull request; only with a token, where it is cheap.
                if token and not pr.get("draft"):
                    try:
                        reviews = await self._get(ctx, token, f"/repos/{repo}/pulls/{pr.get('number')}/reviews", {"per_page": 30})
                    except _Stale:
                        reviews = None
                rows.append(self._pull_row(repo, pr, reviews if isinstance(reviews, list) else None))
        rows.sort(key=lambda row: str(row.get("updated") or ""), reverse=True)
        return WidgetData(status="ok", items=rows[: self._limit(options)], primary={"label": "Pull requests", "value": len(rows)},
                          meta={"empty": "No open pull requests"}, link=f"https://github.com/{self._source(options)[0]}/pulls")

    @staticmethod
    def _run_rows(repo: str, payload: Any) -> list[dict[str, Any]]:
        rows = []
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else []
        for run in runs or []:
            conclusion = str(run.get("conclusion") or "")
            running = run.get("status") != "completed"
            status = "unknown" if running else "ok" if conclusion == "success" else "bad" if conclusion in ("failure", "timed_out") else "warn"
            rows.append({
                "id": f"{repo}:{run.get('id') or run.get('html_url')}",
                "title": str(run.get("name") or "Workflow"),
                "subtitle": " · ".join(part for part in (repo if repo != "?" else "", str(run.get("head_branch") or "")) if part),
                "url": run.get("html_url"),
                "status": status,
                "value": "running" if running else conclusion or "?",
                "updated": run.get("updated_at"),
            })
        return rows

    async def _runs_card(self, ctx: Context, token: str, options: dict[str, Any]) -> WidgetData:
        rows: list[dict[str, Any]] = []
        for repo in self._source(options):
            payload = await self._get(ctx, token, f"/repos/{repo}/actions/runs", {"per_page": 10})
            rows.extend(self._run_rows(repo, payload))
        rows.sort(key=lambda row: str(row.get("updated") or ""), reverse=True)
        worst = "bad" if any(r["status"] == "bad" for r in rows[:1]) else "ok"
        return WidgetData(status=worst, items=rows[: self._limit(options)], meta={"empty": "No workflow run yet"},
                          link=f"https://github.com/{self._source(options)[0]}/actions")

    @staticmethod
    def _milestone_rows(repo: str, milestones: Any) -> list[dict[str, Any]]:
        rows = []
        for m in milestones if isinstance(milestones, list) else []:
            open_count = int(m.get("open_issues") or 0)
            closed = int(m.get("closed_issues") or 0)
            total = open_count + closed
            due = str(m.get("due_on") or "")[:10]
            rows.append({
                "id": f"{repo}:{m.get('title')}",
                "title": str(m.get("title") or ""),
                "subtitle": " · ".join(part for part in (repo if repo != "?" else "", f"due {due}" if due else "") if part),
                "url": m.get("html_url"),
                "status": "ok" if total and closed == total else "unknown",
                "value": f"{closed} / {total}",
                "progress": round(100 * closed / total) if total else 0,
                "due": due,
            })
        rows.sort(key=lambda row: (row["due"] == "", row["due"]))
        return rows

    async def _milestones_card(self, ctx: Context, token: str, options: dict[str, Any]) -> WidgetData:
        rows: list[dict[str, Any]] = []
        for repo in self._source(options):
            milestones = await self._get(ctx, token, f"/repos/{repo}/milestones", {"state": "open", "per_page": 20})
            rows.extend(self._milestone_rows(repo, milestones))
        rows.sort(key=lambda row: (row["due"] == "", row["due"]))
        return WidgetData(status="ok", items=rows[: self._limit(options)], meta={"empty": "No open milestones"},
                          link=f"https://github.com/{self._source(options)[0]}/milestones")

    async def _repo_card(self, ctx: Context, token: str, options: dict[str, Any]) -> WidgetData:
        repo = str(options.get("repo") or "").strip().strip("/")
        if not repo:
            linked = self._repos({"project": options.get("project")}, presets=False)
            repo = linked[0] if linked else ""
        if not repo:
            raise AdapterError("No repository is set.", code="missing_repo",
                               hint="Name one as owner/name, or pick a HexDeck project that links one.")
        info = await self._get(ctx, token, f"/repos/{repo}")
        pulls = await self._get(ctx, token, f"/repos/{repo}/pulls", {"state": "open", "per_page": 100})
        runs = await self._get(ctx, token, f"/repos/{repo}/actions/runs", {"per_page": 1})
        milestones = await self._get(ctx, token, f"/repos/{repo}/milestones", {"state": "open", "per_page": 20})
        open_pulls = len(pulls) if isinstance(pulls, list) else 0
        # GitHub's open_issues_count counts pull requests as issues.
        open_issues = max(0, int(info.get("open_issues_count") or 0) - open_pulls)
        run_rows = self._run_rows(repo, runs)
        last = run_rows[0] if run_rows else None
        milestone_rows = self._milestone_rows(repo, milestones)
        nxt = milestone_rows[0] if milestone_rows else None
        secondary = [{"label": "Pull requests", "value": open_pulls}]
        if last:
            secondary.append({"label": "Last run", "value": f"{last['title']}: {last['value']}"})
        if nxt:
            open_left = int(str(nxt["value"]).split("/")[1]) - int(str(nxt["value"]).split("/")[0])
            secondary.append({"label": "Next milestone", "value": f"{nxt['title']} · {open_left} open"})
        secondary.append({"label": "Stars", "value": int(info.get("stargazers_count") or 0)})
        return WidgetData(
            status="bad" if last and last["status"] == "bad" else "ok",
            primary={"label": "Open issues", "value": open_issues},
            secondary=secondary,
            link=str(info.get("html_url") or f"https://github.com/{repo}"),
            meta={"repo": repo},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "releases":
            releases = [
                ("jellyfin 10.11.12", "jellyfin/jellyfin", "Fixes trickplay on ARM devices."),
                ("Radarr 6.3.1", "Radarr/Radarr", "Improved import matching for collections."),
                ("core 2026.9.1", "home-assistant/core", "New energy dashboard cards."),
                ("Sonarr 4.0.20", "Sonarr/Sonarr", "Season pack handling reworked."),
                ("pi-hole 6.4.4", "pi-hole/pi-hole", "Faster gravity rebuilds."),
                ("nexdeck 0.1.0", "DerKezorm/nexdeck", "First release."),
            ]
            start = tick // 150
            items = []
            for index in range(min(len(releases), self._limit(options))):
                title, repo, note = releases[(start + index) % len(releases)]
                items.append({"title": title, "url": "https://github.com/", "source": repo,
                              "published": 1788600000 - index * 86400 - tick, "summary": note, "image": ""})
            return WidgetData(items=items, meta={"style": "list", "failures": []})
        repo = "HexLions/hexdeck"
        if widget_kind == "repo":
            return WidgetData(primary={"label": "Open issues", "value": 4},
                              secondary=[{"label": "Pull requests", "value": 1}, {"label": "Last run", "value": "CI: success"},
                                         {"label": "Next milestone", "value": "0.17.0 · 3 open"}, {"label": "Stars", "value": 12}],
                              link=f"https://github.com/{repo}")
        if widget_kind == "issues":
            return WidgetData(items=self._issue_rows(repo, [
                {"number": 7, "title": "Roadmap card overlaps at 36 columns", "html_url": "", "labels": [{"name": "bug"}], "user": {"login": "kim"}, "comments": 2, "updated_at": ""},
                {"number": 5, "title": "TrueNAS: move to JSON-RPC", "html_url": "", "labels": [{"name": "adapter"}], "user": {"login": "lee"}, "comments": 4, "updated_at": ""},
                {"number": 3, "title": "Italian translation review", "html_url": "", "labels": [], "user": {"login": "kim"}, "comments": 0, "updated_at": ""},
            ]), primary={"label": "Open issues", "value": 3})
        if widget_kind == "pulls":
            return WidgetData(items=[
                self._pull_row(repo, {"number": 9, "title": "Add ETag cache to GitHub", "html_url": "", "draft": False, "user": {"login": "kim"}, "requested_reviewers": [], "head": {"ref": "etag"}}, [{"state": "APPROVED"}]),
                self._pull_row(repo, {"number": 10, "title": "Notepad card", "html_url": "", "draft": True, "user": {"login": "lee"}, "requested_reviewers": [], "head": {"ref": "notepad"}}, None),
            ], primary={"label": "Pull requests", "value": 2})
        if widget_kind == "runs":
            return WidgetData(items=self._run_rows(repo, {"workflow_runs": [
                {"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "html_url": "", "head_branch": "main", "updated_at": ""},
                {"id": 2, "name": "CI", "status": "in_progress", "conclusion": None, "html_url": "", "head_branch": "projects", "updated_at": ""},
                {"id": 3, "name": "CI", "status": "completed", "conclusion": "failure", "html_url": "", "head_branch": "restyle", "updated_at": ""},
            ]}))
        return WidgetData(items=self._milestone_rows(repo, [
            {"title": "0.17.0", "due_on": "2026-10-01T00:00:00Z", "open_issues": 3, "closed_issues": 5, "html_url": ""},
            {"title": "0.18.0", "due_on": None, "open_issues": 8, "closed_issues": 0, "html_url": ""},
        ]))


class _Stale(Exception):
    """The limit is used up; here is the last answer, to be shown as such."""

    def __init__(self, body: Any) -> None:
        super().__init__("stale")
        self.body = body
        self.reset: float | None = None


ADAPTER = GithubAdapter()
