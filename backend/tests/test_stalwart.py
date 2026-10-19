"""Stalwart, against the answers of Stalwart v0.16.27 run locally on 19.10.2026.

The fixture is what that server answered through JMAP to a key with every
needed permission and to a key with the queue permissions only, while one
message waited for an address whose domain does not resolve. The session is
cut down to what the card reads.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

ST = "http://stalwart.local:8080"
CONFIG = {"url": ST, "api_key": "API_test-key"}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "stalwart_queue.json").read_text())


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _server(answers: dict[str, Any] | None = None, session: dict[str, Any] | None = None) -> respx.Route:
    respx.get(f"{ST}/jmap/session").mock(return_value=httpx.Response(200, json=session or FIXTURE["session"]))
    return respx.post(f"{ST}/jmap/").mock(return_value=httpx.Response(200, json=answers or FIXTURE["answers_full_key"]))


@respx.mock
async def test_the_summary(ctx: Context) -> None:
    route = _server()
    data = await get_adapter("stalwart").fetch("summary", CONFIG, {}, ctx)
    assert data.status == "warn"
    assert data.primary == {"label": "Queued", "value": 1}
    assert data.secondary == [{"label": "Failing", "value": 1}, {"label": "Accounts", "value": 1}, {"label": "Domains", "value": 1}]
    sent = json.loads(route.calls[0].request.content)
    assert sent["using"] == ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]
    assert {call[1]["accountId"] for call in sent["methodCalls"]} == {"b"}, "the account from the session"
    assert route.calls[0].request.headers["authorization"] == "Bearer API_test-key"


@respx.mock
async def test_a_key_that_may_only_read_the_queue(ctx: Context) -> None:
    """Accounts and domains answer forbidden inside a 200; the card leaves them out."""
    _server(answers=FIXTURE["answers_queue_only_key"])
    data = await get_adapter("stalwart").fetch("summary", CONFIG, {}, ctx)
    assert [one["label"] for one in data.secondary] == ["Failing"]


@respx.mock
async def test_the_queue(ctx: Context) -> None:
    _server()
    data = await get_adapter("stalwart").fetch("queue", CONFIG, {}, ctx)
    [row] = data.items
    assert row["title"] == "someone@mail-nowhere.example.net"
    assert row["subtitle"].startswith("No IP addresses found for") and row["subtitle"].endswith("93 B")
    assert row["status"] == "warn" and row["value"].startswith("Retry ")


def test_waiting_and_several_recipients() -> None:
    adapter = get_adapter("stalwart")
    now = datetime(2026, 10, 19, 16, 40, tzinfo=UTC)
    answers = {"q": {"total": 1}, "g": {"list": [{
        "nextRetry": "2026-10-19T18:41:00Z", "size": 2048,
        "recipients": {"a@example.com": {"status": {"@type": "Scheduled"}}, "b@example.com": {"status": {"@type": "Scheduled"}}},
    }]}}
    [row] = adapter._list(answers, {}, now).items
    assert row["title"] == "a@example.com and 1 more"
    assert row["subtitle"] == "Waiting for the first try · 2.0 KB"
    assert row["value"] == "Retry in 2 h" and row["status"] == "unknown"


@respx.mock
async def test_a_key_that_may_not_read_the_queue(ctx: Context) -> None:
    forbidden = ["error", {"type": "forbidden", "description": "You are not authorized to perform this action"}]
    _server(answers={"methodResponses": [[*forbidden, "q"], [*forbidden, "g"]]})
    with pytest.raises(AuthFailed):
        await get_adapter("stalwart").fetch("queue", CONFIG, {}, ctx)


@respx.mock
async def test_a_wrong_key(ctx: Context) -> None:
    respx.get(f"{ST}/jmap/session").mock(return_value=httpx.Response(401, json=FIXTURE["unauthorized"]))
    with pytest.raises(AuthFailed):
        await get_adapter("stalwart").fetch("summary", CONFIG, {}, ctx)


@respx.mock
async def test_an_older_stalwart_or_something_else(ctx: Context) -> None:
    respx.get(f"{ST}/jmap/session").mock(return_value=httpx.Response(200, json={"capabilities": {}, "primaryAccounts": {}}))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("stalwart").fetch("summary", CONFIG, {}, ctx)
    assert failure.value.code == "not_stalwart"


@respx.mock
async def test_the_connection_test(ctx: Context) -> None:
    _server()
    assert await get_adapter("stalwart").test(CONFIG, ctx) == "Stalwart answers with 1 message in the queue."


def test_the_demo() -> None:
    adapter = get_adapter("stalwart")
    for tick in range(6):
        assert adapter.demo("summary", {}, tick).primary["label"] == "Queued"
        assert adapter.demo("queue", {}, tick).items
