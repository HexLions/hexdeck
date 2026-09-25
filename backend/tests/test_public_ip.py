"""The address the house shows to the world, and who it belongs to."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

IPAPI = "https://ipapi.co/json/"


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={})


@respx.mock
async def test_the_address_and_who_it_belongs_to(ctx: Context) -> None:
    respx.get(IPAPI).mock(return_value=httpx.Response(200, json={
        "ip": "203.0.113.9", "city": "Rome", "country_name": "Italy", "country_code": "IT",
        "org": "AS1234 Example Telecom", "asn": "AS1234",
    }))
    data = await get_adapter("publicip").fetch("address", {}, {}, ctx)
    assert data.primary == {"label": "Public address", "value": "203.0.113.9"}
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Provider"] == "AS1234 Example Telecom" and labels["Place"] == "Rome, Italy"
    assert data.status == "ok"


@respx.mock
async def test_a_changed_address_is_said_so(ctx: Context) -> None:
    """A house address that moves is worth knowing about: it is what a dynamic
    DNS entry and half the port forwards hang off."""
    route = respx.get(IPAPI).mock(side_effect=[
        httpx.Response(200, json={"ip": "203.0.113.9", "org": "Example"}),
        httpx.Response(200, json={"ip": "203.0.113.10", "org": "Example"}),
    ])
    adapter = get_adapter("publicip")
    await adapter.fetch("address", {}, {}, ctx)
    second = await adapter.fetch("address", {}, {"cache_minutes": 0}, ctx)
    assert route.call_count == 2
    assert second.meta["changed_from"] == "203.0.113.9"
    assert second.status == "warn", "it is not a fault, but it is news"


@respx.mock
async def test_the_address_is_asked_for_rarely(ctx: Context) -> None:
    route = respx.get(IPAPI).mock(return_value=httpx.Response(200, json={"ip": "203.0.113.9"}))
    adapter = get_adapter("publicip")
    await adapter.fetch("address", {}, {}, ctx)
    await adapter.fetch("address", {}, {}, ctx)
    assert route.call_count == 1, "the answer is kept; the address does not move every minute"


@respx.mock
async def test_a_service_that_will_not_answer_says_so(ctx: Context) -> None:
    respx.get(IPAPI).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("publicip").fetch("address", {}, {}, ctx)
    assert refused.value.code == "http_error"


def test_the_demo_draws() -> None:
    assert get_adapter("publicip").demo("address", {}, 0).primary
