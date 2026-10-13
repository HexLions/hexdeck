"""Jenkins: failing jobs, the queue, offline agents and the build button.

``jenkins_api.json`` holds what Jenkins 2.580.1 answered locally on
2026-10-13: five freestyle jobs (passed twice, failed, running with a second
build blocked in the queue, disabled) and an agent that never connected.
The address is written as ``http://jenkins.local:8080``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://jenkins.local:8080"
CONFIG = {"url": BASE, "username": "admin", "token": "made-up-token"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "jenkins_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _jenkins() -> None:
    respx.get(f"{BASE}/api/json").mock(return_value=httpx.Response(200, json=ANSWERS["jobs"]))
    respx.get(f"{BASE}/queue/api/json").mock(return_value=httpx.Response(200, json=ANSWERS["queue"]))
    respx.get(f"{BASE}/computer/api/json").mock(return_value=httpx.Response(200, json=ANSWERS["computers"]))


@respx.mock
async def test_the_overview_counts_the_failing_job_and_the_offline_agent() -> None:
    _jenkins()
    data = await get_adapter("jenkins").fetch("overview", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.primary == {"label": "Failing", "value": 1, "metric": "failing"}
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values == {"Jobs": 5, "Building": 1, "Queue": 1, "Executors": "1/2", "Offline": 1}
    assert data.meta["notice"] == "Offline: build-arm64"


@respx.mock
async def test_the_list_puts_the_failing_job_first() -> None:
    _jenkins()
    data = await get_adapter("jenkins").fetch("jobs", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["deploy-website", "backup-nightly", "lint", "integration-tests", "old-release"]
    assert data.items[0]["status"] == "bad" and data.items[0]["subtitle"].startswith("failed · #1")
    assert data.items[3]["subtitle"].startswith("building"), "its first build, still running"
    assert all(row["actions"] == [] for row in data.items), "no button unless the card asks for it"
    only = await get_adapter("jenkins").fetch("jobs", CONFIG, {"only_failing": True}, _ctx())
    assert [row["title"] for row in only.items] == ["deploy-website"]


@respx.mock
async def test_the_build_button_builds_only_a_listed_job() -> None:
    _jenkins()
    build = respx.post(f"{BASE}/job/lint/build").mock(return_value=httpx.Response(201))
    data = await get_adapter("jenkins").fetch("jobs", CONFIG, {"building": True}, _ctx())
    lint = next(row for row in data.items if row["title"] == "lint")
    assert lint["actions"][0]["params"] == {"job": "lint"} and lint["actions"][0]["confirm"] is True
    assert next(row for row in data.items if row["title"] == "old-release")["actions"] == []
    assert await get_adapter("jenkins").action("jobs", "build", {"job": "lint"}, CONFIG, {}, _ctx()) == "Build of lint started."
    assert build.called and build.calls[0].request.headers["authorization"].startswith("Basic ")
    with pytest.raises(AdapterError):
        await get_adapter("jenkins").action("jobs", "build", {"job": "../../script"}, CONFIG, {}, _ctx())


@respx.mock
async def test_a_wrong_token_and_no_token() -> None:
    respx.get(f"{BASE}/api/json").mock(side_effect=lambda request: httpx.Response(401 if "authorization" in request.headers else 403))
    with pytest.raises(AuthFailed, match="refused"):
        await get_adapter("jenkins").test(CONFIG, _ctx())
    with pytest.raises(AuthFailed, match="anonymous"):
        await get_adapter("jenkins").test({"url": BASE}, _ctx())


@respx.mock
async def test_jobs_in_a_folder_are_read_one_level_deep() -> None:
    folder = {"jobs": [{"_class": "com.cloudbees.hudson.plugins.folder.Folder", "name": "infra", "fullName": "infra",
                        "jobs": [{"name": "terraform", "fullName": "infra/terraform", "color": "red", "buildable": True}]},
                       *ANSWERS["jobs"]["jobs"][:1]]}
    respx.get(f"{BASE}/api/json").mock(return_value=httpx.Response(200, json=folder))
    build = respx.post(f"{BASE}/job/infra/job/terraform/build").mock(return_value=httpx.Response(201))
    data = await get_adapter("jenkins").fetch("jobs", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["infra/terraform", "backup-nightly"]
    await get_adapter("jenkins").action("jobs", "build", {"job": "infra/terraform"}, CONFIG, {}, _ctx())
    assert build.called
