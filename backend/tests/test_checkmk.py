"""Checkmk: the overview, the problems, the sign-in and the refusals.

``checkmk_api.json`` is written anew in the shape Checkmk 2.5.0p20's REST API source gives its answers; no site was
at hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.checkmk import REFUSED_KEY, CheckmkAdapter

BASE = "https://checkmk.local"
API = f"{BASE}/mysite/check_mk/api/1.0"
CONFIG = {"url": BASE, "site": "mysite", "username": "automation", "secret": "made-up-secret"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "checkmk_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _site() -> tuple[respx.Route, respx.Route]:
    respx.get(f"{API}/version").mock(return_value=httpx.Response(200, json=ANSWERS["version"]))
    hosts = respx.post(f"{API}/domain-types/host/collections/all").mock(return_value=httpx.Response(200, json=ANSWERS["hosts"]))

    def services(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("query"):
            return httpx.Response(200, json=ANSWERS["problem_services"])
        everything = {**ANSWERS["problem_services"], "value": ANSWERS["problem_services"]["value"] + [
            {"extensions": {"host_name": "nas", "description": f"Service {number}", "state": 0}} for number in range(40)]}
        return httpx.Response(200, json=everything)

    return hosts, respx.post(f"{API}/domain-types/service/collections/all").mock(side_effect=services)


@respx.mock
async def test_the_connection_test_and_the_bearer_token() -> None:
    _site()
    message = await get_adapter("checkmk").test(CONFIG, _ctx())
    assert message == "Checkmk 2.5.0p20.cre (community) answers for the site mysite."
    request = respx.calls[0].request
    assert request.headers["Authorization"] == "Bearer automation made-up-secret"
    assert "made-up-secret" not in str(request.url)


@respx.mock
async def test_the_overview() -> None:
    _site()
    data = await get_adapter("checkmk").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Hosts up", "value": "2 / 3"}
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values == {"Hosts down": 1, "Critical": 1, "Warning": 1, "Unknown": 1, "Services": 43}
    assert data.status == "bad"


@respx.mock
async def test_the_problems_ask_only_for_what_is_not_ok() -> None:
    _, services = _site()
    data = await get_adapter("checkmk").fetch("problems", CONFIG, {}, _ctx())
    body = json.loads(services.calls[0].request.content)
    assert body["query"] == {"op": "!=", "left": "state", "right": "0"}
    assert "plugin_output" in body["columns"]
    assert [(item["title"], item["status"]) for item in data.items] == [
        ("printer", "warn"),
        ("router: Interface 2", "bad"),
        ("nas: Check_MK Agent", "warn"),
        ("nas: Filesystem /mnt/tank", "warn"),
    ]
    assert data.status == "bad"


def test_what_is_acknowledged_or_in_downtime_can_stay_off() -> None:
    hosts = [one["extensions"] for one in ANSWERS["hosts"]["value"]]
    services = [one["extensions"] for one in ANSWERS["problem_services"]["value"]]
    data = CheckmkAdapter._problems(hosts, services, [], 10, acknowledged=False)
    assert [item["title"] for item in data.items] == ["router: Interface 2", "nas: Filesystem /mnt/tank"]


def test_a_site_that_does_not_answer_is_said() -> None:
    data = CheckmkAdapter._summary([{"name": "a", "state": 0}], [], ["remote_1"])
    assert data.status == "warn" and data.secondary[-1] == {"label": "Sites not answering", "value": "remote_1"}


@respx.mock
async def test_a_refusal_is_remembered_so_a_user_is_not_locked() -> None:
    hosts = respx.post(f"{API}/domain-types/host/collections/all").mock(return_value=httpx.Response(401, json=ANSWERS["unauthorized"]))
    ctx = _ctx()
    with pytest.raises(AuthFailed):
        await get_adapter("checkmk").fetch("summary", CONFIG, {}, ctx)
    assert REFUSED_KEY in ctx.cache
    with pytest.raises(AuthFailed):
        await get_adapter("checkmk").fetch("summary", CONFIG, {}, ctx)
    assert hosts.call_count == 1, "the second card did not ask again"
    respx.get(f"{API}/version").mock(return_value=httpx.Response(401, json=ANSWERS["unauthorized"]))
    with pytest.raises(AuthFailed):
        await get_adapter("checkmk").test(CONFIG, ctx)
    assert respx.calls[-1].request.url.path.endswith("/version"), "Test asks at once"


@respx.mock
@pytest.mark.parametrize(("status", "code"), [(404, "not_found"), (405, "too_old")])
async def test_a_wrong_site_and_an_old_checkmk(status: int, code: str) -> None:
    respx.post(f"{API}/domain-types/host/collections/all").mock(return_value=httpx.Response(status))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("checkmk").fetch("summary", CONFIG, {}, _ctx())
    assert refused.value.code == code


async def test_a_site_with_a_slash_is_refused() -> None:
    with pytest.raises(AdapterError):
        await get_adapter("checkmk").fetch("summary", {**CONFIG, "site": "mysite/../x"}, {}, _ctx())


@pytest.mark.parametrize("kind", ["summary", "problems"])
def test_every_card_has_a_demo(kind: str) -> None:
    data = get_adapter("checkmk").demo(kind, {}, 3)
    assert data.primary or data.items
