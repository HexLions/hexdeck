"""llama.cpp server, against the answers of llama-server b11734 run locally on 19.10.2026.

The fixture is what that server answered with a 19 MB test model, two slots,
``--metrics`` and ``--api-key``, captured while one slot was working; the
model path is shortened, the chat template and sampling settings are left out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.llamacpp import parse_metrics

LL = "http://llama.local:8080"
CONFIG = {"url": LL, "api_key": "sekrit-key"}
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "llamacpp_server.json").read_text())


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _server(props: dict[str, Any] | None = None, slots: Any = None) -> dict[str, respx.Route]:
    return {
        "health": respx.get(f"{LL}/health").mock(return_value=httpx.Response(200, json={"status": "ok"})),
        "props": respx.get(f"{LL}/props").mock(return_value=httpx.Response(200, json=props or FIXTURE["props"])),
        "slots": respx.get(f"{LL}/slots").mock(return_value=httpx.Response(200, json=slots if slots is not None else FIXTURE["slots_busy"])),
        "metrics": respx.get(f"{LL}/metrics").mock(return_value=httpx.Response(200, text=FIXTURE["metrics_busy"])),
    }


@respx.mock
async def test_the_server_card(ctx: Context) -> None:
    routes = _server()
    data = await get_adapter("llamacpp").fetch("server", CONFIG, {}, ctx)
    assert data.primary == {"label": "Busy slots", "value": "1 / 2"}
    values = {one["label"]: one["value"] for one in data.secondary}
    assert values["Model"] == "stories15M"
    assert values["Generation"] == "1492.4 tok/s" and values["Prompt"] == "477.5 tok/s", "the counters, not the gauges a read resets"
    assert "Waiting" not in values
    assert data.metrics == {"busy": 1.0, "tokens_per_second": 1492.4}
    assert routes["props"].calls[0].request.headers["authorization"] == "Bearer sekrit-key"


@respx.mock
async def test_switched_off_endpoints_are_not_asked(ctx: Context) -> None:
    """``/props`` says which are on; a server without them answers 501."""
    props = {**FIXTURE["props"], "endpoint_slots": False, "endpoint_metrics": False}
    routes = _server(props=props)
    data = await get_adapter("llamacpp").fetch("server", CONFIG, {}, ctx)
    assert data.primary == {"label": "Busy slots", "value": 2}
    assert [one["label"] for one in data.secondary] == ["Model"]
    assert not routes["slots"].called and not routes["metrics"].called
    with pytest.raises(AdapterError) as failure:
        await get_adapter("llamacpp").fetch("slots", CONFIG, {}, ctx)
    assert failure.value.code == "not_supported"


@respx.mock
async def test_the_slot_list(ctx: Context) -> None:
    _server()
    data = await get_adapter("llamacpp").fetch("slots", CONFIG, {}, ctx)
    assert [(one["title"], one["value"], one["status"]) for one in data.items] == [("Slot 0", "Working", "warn"), ("Slot 1", "Idle", "ok")]
    assert data.items[0]["subtitle"] == "Context 128"


@respx.mock
async def test_without_an_alias_only_the_file_name_shows(ctx: Context) -> None:
    _server(props={**FIXTURE["props"], "model_alias": FIXTURE["props_no_alias_model_alias"]})
    data = await get_adapter("llamacpp").fetch("server", CONFIG, {}, ctx)
    assert data.secondary[0] == {"label": "Model", "value": "stories15M-q4_0"}


@respx.mock
async def test_a_wrong_key(ctx: Context) -> None:
    respx.get(f"{LL}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx.get(f"{LL}/props").mock(return_value=httpx.Response(401, json=FIXTURE["unauthorized"]))
    with pytest.raises(AuthFailed):
        await get_adapter("llamacpp").fetch("server", CONFIG, {}, ctx)


@respx.mock
async def test_a_model_still_loading(ctx: Context) -> None:
    respx.get(f"{LL}/health").mock(return_value=httpx.Response(503, json=FIXTURE["loading"]))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("llamacpp").fetch("server", CONFIG, {}, ctx)
    assert failure.value.code == "loading"


@respx.mock
async def test_something_else_at_the_address(ctx: Context) -> None:
    respx.get(f"{LL}/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx.get(f"{LL}/props").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("llamacpp").fetch("server", CONFIG, {}, ctx)
    assert failure.value.code == "not_llamacpp"


@respx.mock
async def test_the_connection_test(ctx: Context) -> None:
    _server()
    assert await get_adapter("llamacpp").test(CONFIG, ctx) == "llama-server b11734 answers with stories15M on 2 slots."


def test_the_metrics_text() -> None:
    values = parse_metrics(FIXTURE["metrics_busy"])
    assert values["requests_processing"] == 1.0 and values["tokens_predicted_total"] > 0
    assert parse_metrics("# HELP x\nllamacpp:broken abc\n") == {}


def test_the_demo() -> None:
    adapter = get_adapter("llamacpp")
    for tick in range(0, 200, 7):
        assert adapter.demo("server", {}, tick).primary["value"].endswith("/ 4")
        assert len(adapter.demo("slots", {}, tick).items) == 4


@respx.mock
async def test_no_speed_before_the_first_request_ended(ctx: Context) -> None:
    """A fresh server: every counter is 0, so there is nothing to divide."""
    _server()
    respx.get(f"{LL}/metrics").mock(return_value=httpx.Response(200, text="llamacpp:tokens_predicted_total 0\nllamacpp:tokens_predicted_seconds_total 0\n"))
    data = await get_adapter("llamacpp").fetch("server", CONFIG, {}, ctx)
    assert [one["label"] for one in data.secondary] == ["Model"]
    assert "tokens_per_second" not in data.metrics
