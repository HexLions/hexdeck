"""GitLab: the pipelines, the merge requests, and the path that has to be encoded.

The answers below follow GitLab's own API documentation (doc/api/pipelines.md,
merge_requests.md, projects.md and metadata.md) as of GitLab 19.x, and were
checked against a public project on GitLab.com on 2026-10-07.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.gitlab import project_path

GL = "https://gitlab.com/api/v4"
PROJECT = f"{GL}/projects/homelab%2Fhexdeck"
CONFIG = {"url": "https://gitlab.com", "token": "glpat-made-up", "project": "homelab/hexdeck"}

PIPELINES = [
    {"id": 812, "iid": 412, "project_id": 7, "status": "failed", "source": "push", "ref": "main",
     "sha": "3f9c2e1a7b0d", "name": "Build and test", "web_url": "https://gitlab.com/homelab/hexdeck/-/pipelines/812",
     "created_at": "2026-10-07T08:00:00Z", "updated_at": "2026-10-07T08:10:00Z"},
    {"id": 811, "iid": 411, "project_id": 7, "status": "success", "source": "schedule", "ref": "main",
     "sha": "a1d04b9e33aa", "name": None, "web_url": "https://gitlab.com/homelab/hexdeck/-/pipelines/811",
     "created_at": "2026-10-06T08:00:00Z", "updated_at": "2026-10-06T08:10:00Z"},
    {"id": 810, "iid": 410, "project_id": 7, "status": "waiting_for_resource", "source": "push", "ref": "v1.0",
     "sha": "77e10c2f91bb", "name": "Release", "web_url": "", "created_at": "", "updated_at": ""},
]
MERGE_REQUESTS = [
    {"id": 1, "iid": 57, "title": "Ready one", "author": {"username": "alex"}, "source_branch": "feature/a",
     "detailed_merge_status": "mergeable", "draft": False, "has_conflicts": False, "updated_at": "", "web_url": ""},
    {"id": 2, "iid": 56, "title": "Clashing one", "author": {"username": "sam"}, "source_branch": "feature/b",
     "detailed_merge_status": "need_rebase", "draft": False, "has_conflicts": True, "updated_at": "", "web_url": ""},
    {"id": 3, "iid": 55, "title": "Draft: not yet", "author": {"username": "noa"}, "source_branch": "feature/c",
     "detailed_merge_status": "draft_status", "draft": True, "has_conflicts": False, "updated_at": "", "web_url": ""},
]
PROJECT_ANSWER = {"id": 7, "path_with_namespace": "homelab/hexdeck", "default_branch": "main",
                  "open_issues_count": 12, "web_url": "https://gitlab.com/homelab/hexdeck"}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _gitlab(total: str | None = "3") -> None:
    respx.get(PROJECT).mock(return_value=httpx.Response(200, json=PROJECT_ANSWER))
    respx.get(f"{PROJECT}/pipelines").mock(return_value=httpx.Response(200, json=PIPELINES))
    headers = {"x-total": total} if total is not None else {}
    respx.get(f"{PROJECT}/merge_requests").mock(return_value=httpx.Response(200, json=MERGE_REQUESTS, headers=headers))
    respx.get(f"{GL}/version").mock(return_value=httpx.Response(200, json={"version": "19.4.1-ee", "revision": "abc"}))


def test_the_project_path_is_encoded_whatever_was_typed() -> None:
    """⚠️ Sent with its slashes, the path makes GitLab answer 404, which reads
    like a project that does not exist."""
    assert project_path("group/name") == "group%2Fname"
    assert project_path("https://gitlab.com/group/sub/name/-/merge_requests") == "group%2Fsub%2Fname"
    assert project_path("https://git.home.lan/team/app.git") == "team%2Fapp"
    assert project_path("42") == "42"


@respx.mock
async def test_the_token_goes_in_as_private_token_and_the_path_encoded() -> None:
    _gitlab()
    await get_adapter("gitlab").fetch("pipelines", CONFIG, {}, _ctx())
    request = respx.calls[0].request
    assert request.headers["private-token"] == "glpat-made-up"
    assert "/projects/homelab%2Fhexdeck/pipelines" in str(request.url)


@respx.mock
async def test_a_public_project_is_asked_without_a_token() -> None:
    _gitlab()
    await get_adapter("gitlab").fetch("pipelines", {**CONFIG, "token": ""}, {}, _ctx())
    assert "private-token" not in respx.calls[0].request.headers


@respx.mock
async def test_the_pipelines_card_marks_the_red_one_and_says_how_each_ended() -> None:
    _gitlab()
    data = await get_adapter("gitlab").fetch("pipelines", CONFIG, {}, _ctx())
    assert [row["value"] for row in data.items] == ["failed", "passed", "waiting"]
    assert [row["status"] for row in data.items] == ["bad", "ok", "unknown"]
    assert data.items[1]["title"] == "#411", "a pipeline without a name is called by its number"
    assert data.status == "bad", "the newest pipeline is red"
    assert data.metrics == {"failing": 1.0}


@respx.mock
async def test_a_branch_is_passed_on_as_the_ref() -> None:
    _gitlab()
    await get_adapter("gitlab").fetch("pipelines", CONFIG, {"ref": "main"}, _ctx())
    assert respx.calls[0].request.url.params["ref"] == "main"


@respx.mock
async def test_the_merge_requests_say_what_stands_in_their_way() -> None:
    _gitlab()
    data = await get_adapter("gitlab").fetch("merge_requests", CONFIG, {}, _ctx())
    assert [(row["value"], row["status"]) for row in data.items] == [("ready", "ok"), ("conflict", "bad"), ("draft", "unknown")]
    assert data.status == "warn"
    assert data.primary["value"] == 3


@respx.mock
async def test_more_than_a_page_of_merge_requests_is_counted_from_the_header() -> None:
    """⚠️ A page holds a hundred at most; a project with 101 said 100."""
    _gitlab(total="101")
    data = await get_adapter("gitlab").fetch("merge_requests", CONFIG, {}, _ctx())
    assert data.primary["value"] == 101


@respx.mock
async def test_hidden_drafts_are_not_counted() -> None:
    _gitlab(total="3")
    data = await get_adapter("gitlab").fetch("merge_requests", CONFIG, {"hide_drafts": True}, _ctx())
    assert [row["title"] for row in data.items] == ["Ready one", "Clashing one"]
    assert data.primary["value"] == 2


@respx.mock
async def test_the_project_card_reads_the_default_branch_and_the_counts() -> None:
    _gitlab(total="3")
    data = await get_adapter("gitlab").fetch("project", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Pipeline", "value": "failed"}
    assert data.status == "bad"
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels == {"Merge requests": 3, "Open issues": 12, "Branch": "main"}
    pipelines = next(call for call in respx.calls if call.request.url.path.endswith("/pipelines"))
    assert pipelines.request.url.params["ref"] == "main"


@respx.mock
async def test_a_project_with_issues_switched_off_shows_none_rather_than_nought() -> None:
    _gitlab()
    respx.get(PROJECT).mock(return_value=httpx.Response(200, json={**PROJECT_ANSWER, "open_issues_count": None}))
    data = await get_adapter("gitlab").fetch("project", CONFIG, {}, _ctx())
    assert "Open issues" not in {row["label"] for row in data.secondary}
    assert "issues" not in data.metrics


@respx.mock
async def test_a_card_can_name_another_project() -> None:
    respx.get(f"{GL}/projects/other%2Fthing/pipelines").mock(return_value=httpx.Response(200, json=[]))
    data = await get_adapter("gitlab").fetch("pipelines", CONFIG, {"project": "other/thing"}, _ctx())
    assert data.items == [] and data.meta["empty"] == "No pipeline yet"


@respx.mock
async def test_a_refused_token_is_said_plainly() -> None:
    respx.get(f"{PROJECT}/pipelines").mock(return_value=httpx.Response(401, json={"message": "401 Unauthorized"}))
    with pytest.raises(AuthFailed):
        await get_adapter("gitlab").fetch("pipelines", CONFIG, {}, _ctx())


@respx.mock
async def test_an_unknown_project_names_both_reasons_gitlab_hides_behind_404() -> None:
    respx.get(f"{PROJECT}/pipelines").mock(return_value=httpx.Response(404, json={"message": "404 Project Not Found"}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("gitlab").fetch("pipelines", CONFIG, {}, _ctx())
    assert failure.value.code == "not_found"


async def test_a_card_without_any_project_says_where_to_name_one() -> None:
    with pytest.raises(AdapterError) as failure:
        await get_adapter("gitlab").fetch("pipelines", {**CONFIG, "project": ""}, {}, _ctx())
    assert failure.value.code == "no_project"


@respx.mock
async def test_the_connection_test_names_the_project_and_the_version() -> None:
    _gitlab()
    said = await get_adapter("gitlab").test(CONFIG, _ctx())
    assert said == "GitLab answers with homelab/hexdeck, GitLab 19.4.1-ee."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("gitlab")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
