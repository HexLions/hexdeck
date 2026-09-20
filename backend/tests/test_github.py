"""GitHub beyond releases: issues, pull requests, workflow runs and milestones,
under sixty requests an hour without a token. Every answer is remembered
with its ETag and asked for again with If-None-Match, so the usual answer is
a 304 that costs nothing against the limit.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context
from app.db import db_session
from app.models import Project, ProjectRepo

API = "https://api.github.com"
GH = {"X-RateLimit-Remaining": "42", "X-RateLimit-Reset": str(int(time.time()) + 1800)}


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={})


def _issues() -> list[dict]:
    return [
        {"number": 7, "title": "Roadmap card overlaps", "html_url": "https://github.com/o/r/issues/7", "state": "open",
         "labels": [{"name": "bug"}], "user": {"login": "kim"}, "updated_at": "2026-09-19T10:00:00Z", "comments": 2},
        {"number": 8, "title": "A pull request lands here too", "html_url": "https://github.com/o/r/pull/8", "state": "open",
         "labels": [], "user": {"login": "kim"}, "updated_at": "2026-09-18T10:00:00Z", "comments": 0, "pull_request": {"url": "x"}},
    ]


@respx.mock
async def test_issues_are_listed_without_the_pull_requests_github_mixes_in(ctx: Context) -> None:
    respx.get(f"{API}/repos/o/r/issues").mock(return_value=httpx.Response(200, json=_issues(), headers={**GH, "ETag": '"abc"'}))
    data = await get_adapter("github").fetch("issues", {}, {"repos": "o/r"}, ctx)
    assert [i["title"] for i in data.items] == ["#7 Roadmap card overlaps"]
    assert data.items[0]["url"] == "https://github.com/o/r/issues/7" and "bug" in data.items[0]["subtitle"]
    assert data.primary["value"] == 1


@respx.mock
async def test_an_answer_is_asked_for_again_with_its_etag_and_a_304_reuses_it(ctx: Context) -> None:
    route = respx.get(f"{API}/repos/o/r/issues").mock(side_effect=[
        httpx.Response(200, json=_issues(), headers={**GH, "ETag": '"abc"'}),
        httpx.Response(304, headers=GH),
    ])
    adapter = get_adapter("github")
    first = await adapter.fetch("issues", {}, {"repos": "o/r"}, ctx)
    second = await adapter.fetch("issues", {}, {"repos": "o/r"}, ctx)
    assert route.calls[1].request.headers.get("If-None-Match") == '"abc"'
    assert [i["title"] for i in second.items] == [i["title"] for i in first.items]


@respx.mock
async def test_the_token_of_the_connection_goes_with_every_request(ctx: Context) -> None:
    route = respx.get(f"{API}/repos/o/r/issues").mock(return_value=httpx.Response(200, json=[], headers=GH))
    await get_adapter("github").fetch("issues", {"token": "ghp_secret"}, {"repos": "o/r"}, ctx)
    assert route.calls[0].request.headers["Authorization"] == "Bearer ghp_secret"


@respx.mock
async def test_a_used_up_limit_is_a_clear_message_with_the_reset_time(ctx: Context) -> None:
    reset = int(time.time()) + 600
    respx.get(f"{API}/repos/o/r/issues").mock(return_value=httpx.Response(
        403, json={"message": "API rate limit exceeded"}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)},
    ))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("github").fetch("issues", {}, {"repos": "o/r"}, ctx)
    assert failure.value.code == "rate_limited"
    assert "token" in failure.value.hint.lower()


@respx.mock
async def test_a_used_up_limit_is_remembered_and_the_last_answer_served_meanwhile(ctx: Context) -> None:
    route = respx.get(f"{API}/repos/o/r/issues").mock(side_effect=[
        httpx.Response(200, json=_issues(), headers={**GH, "ETag": '"abc"'}),
        httpx.Response(403, json={"message": "rate limit"}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 600)}),
    ])
    adapter = get_adapter("github")
    await adapter.fetch("issues", {}, {"repos": "o/r"}, ctx)
    stale = await adapter.fetch("issues", {}, {"repos": "o/r"}, ctx)
    assert stale.status == "warn" and stale.meta.get("stale") and [i["title"] for i in stale.items] == ["#7 Roadmap card overlaps"]
    # Nothing goes out while the limit is known to be used up.
    again = await adapter.fetch("issues", {}, {"repos": "o/r"}, ctx)
    assert again.meta.get("stale") and route.call_count == 2


@respx.mock
async def test_pull_requests_show_draft_and_review_state(ctx: Context) -> None:
    respx.get(f"{API}/repos/o/r/pulls").mock(return_value=httpx.Response(200, json=[
        {"number": 9, "title": "Add ETag cache", "html_url": "https://github.com/o/r/pull/9", "draft": False, "user": {"login": "kim"},
         "requested_reviewers": [{"login": "lee"}], "updated_at": "2026-09-19T10:00:00Z", "head": {"ref": "etag"}},
        {"number": 10, "title": "WIP", "html_url": "https://github.com/o/r/pull/10", "draft": True, "user": {"login": "kim"},
         "requested_reviewers": [], "updated_at": "2026-09-18T10:00:00Z", "head": {"ref": "wip"}},
    ], headers=GH))
    respx.get(f"{API}/repos/o/r/pulls/9/reviews").mock(return_value=httpx.Response(200, json=[{"state": "APPROVED"}], headers=GH))
    respx.get(f"{API}/repos/o/r/pulls/10/reviews").mock(return_value=httpx.Response(200, json=[], headers=GH))
    data = await get_adapter("github").fetch("pulls", {"token": "t"}, {"repos": "o/r"}, ctx)
    assert data.items[0]["status"] == "ok" and "approved" in data.items[0]["subtitle"]
    assert data.items[1]["status"] == "unknown" and "draft" in data.items[1]["subtitle"]


@respx.mock
async def test_the_repo_card_sums_it_up(ctx: Context) -> None:
    respx.get(f"{API}/repos/o/r").mock(return_value=httpx.Response(200, json={"open_issues_count": 5, "stargazers_count": 12, "html_url": "https://github.com/o/r", "default_branch": "main"}, headers=GH))
    respx.get(f"{API}/repos/o/r/pulls").mock(return_value=httpx.Response(200, json=[{"number": 1, "title": "x", "html_url": "", "draft": False, "user": {"login": "k"}, "requested_reviewers": [], "updated_at": "2026-09-19T10:00:00Z", "head": {"ref": "a"}}], headers=GH))
    respx.get(f"{API}/repos/o/r/actions/runs").mock(return_value=httpx.Response(200, json={"workflow_runs": [
        {"name": "CI", "status": "completed", "conclusion": "failure", "html_url": "https://github.com/o/r/actions/runs/1", "updated_at": "2026-09-19T10:00:00Z", "head_branch": "main"},
    ]}, headers=GH))
    respx.get(f"{API}/repos/o/r/milestones").mock(return_value=httpx.Response(200, json=[
        {"title": "0.17.0", "due_on": "2026-10-01T00:00:00Z", "open_issues": 3, "closed_issues": 5, "html_url": "https://github.com/o/r/milestone/1"},
    ], headers=GH))
    data = await get_adapter("github").fetch("repo", {}, {"repo": "o/r"}, ctx)
    assert data.primary == {"label": "Open issues", "value": 4}, "GitHub counts pull requests as issues; the card does not"
    labels = {s["label"]: s["value"] for s in data.secondary}
    assert labels["Pull requests"] == 1 and labels["Last run"] == "CI: failure" and labels["Next milestone"] == "0.17.0 · 3 open"
    assert data.status == "bad", "a failed run turns the card red"
    assert data.link == "https://github.com/o/r"


@respx.mock
async def test_the_repositories_can_come_from_a_hexdeck_project(client, ctx: Context) -> None:
    with db_session() as db:
        project = Project(name="HexDeck", slug="hexdeck", status="active", colour="", position=0)
        db.add(project)
        db.flush()
        db.add(ProjectRepo(project_id=project.id, repo="o/r", position=0))
        db.commit()
        pid = project.id
    respx.get(f"{API}/repos/o/r/issues").mock(return_value=httpx.Response(200, json=_issues(), headers=GH))
    data = await get_adapter("github").fetch("issues", {}, {"project": str(pid)}, ctx)
    assert [i["title"] for i in data.items] == ["#7 Roadmap card overlaps"]


@respx.mock
async def test_runs_and_milestones_are_lists(ctx: Context) -> None:
    respx.get(f"{API}/repos/o/r/actions/runs").mock(return_value=httpx.Response(200, json={"workflow_runs": [
        {"name": "CI", "status": "completed", "conclusion": "success", "html_url": "u1", "updated_at": "2026-09-19T10:00:00Z", "head_branch": "main"},
        {"name": "CI", "status": "in_progress", "conclusion": None, "html_url": "u2", "updated_at": "2026-09-19T11:00:00Z", "head_branch": "feature"},
    ]}, headers=GH))
    respx.get(f"{API}/repos/o/r/milestones").mock(return_value=httpx.Response(200, json=[
        {"title": "0.17.0", "due_on": None, "open_issues": 3, "closed_issues": 1, "html_url": "m1"},
    ], headers=GH))
    adapter = get_adapter("github")
    runs = await adapter.fetch("runs", {}, {"repos": "o/r"}, ctx)
    assert [(r["status"], r["value"]) for r in runs.items] == [("unknown", "running"), ("ok", "success")], "newest first"
    milestones = await adapter.fetch("milestones", {}, {"repos": "o/r"}, ctx)
    assert milestones.items[0]["value"] == "1 / 4" and milestones.items[0]["progress"] == 25
