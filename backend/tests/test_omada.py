"""TP-Link Omada, against answers shaped as the HACS integration's client and tests have them.

No controller was at hand. The bodies are written anew in the shape of
tests/conftest.py and api.py of ha-omada-open-api 1.11.1, whose refusal codes
(-44112, -44106, -1600) come from reports of real controllers.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.omada import uptime_seconds

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "omada_openapi.json").read_text())
URL = "https://omada.example.com:8043"
CONFIG = {"url": URL, "client_id": "client-1", "client_secret": "secret-1"}
OMADA_ID = FIXTURE["info"]["result"]["omadacId"]
BASE = f"{URL}/openapi/v1/{OMADA_ID}"
V2 = f"{URL}/openapi/v2/{OMADA_ID}"


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={})


def _controller(router: respx.Router) -> respx.Route:
    router.get(f"{URL}/api/info").mock(return_value=httpx.Response(200, json=FIXTURE["info"]))
    token = router.post(f"{URL}/openapi/authorize/token").mock(return_value=httpx.Response(200, json=FIXTURE["token"]))
    router.get(f"{BASE}/sites").mock(return_value=httpx.Response(200, json=FIXTURE["sites"]))
    router.get(f"{BASE}/sites/site-home/devices").mock(return_value=httpx.Response(200, json=FIXTURE["devices_home"]))
    router.get(f"{BASE}/sites/site-garage/devices").mock(return_value=httpx.Response(200, json=FIXTURE["devices_garage"]))
    return token


@pytest.mark.parametrize(("value", "seconds"), [("4day(s) 17h 26m 57s", 408417), ("5h 3m", 18180), (90000, 90000), ("", None), (None, None), ("soon", None), (True, None)])
def test_the_uptime_in_either_form(value: object, seconds: int | None) -> None:
    assert uptime_seconds(value) == seconds


@respx.mock
async def test_the_summary_counts_devices_and_clients_of_every_site() -> None:
    token = _controller(respx.mock)
    clients = respx.post(url__regex=rf"{V2}/sites/[^/]+/clients").mock(return_value=httpx.Response(200, json=FIXTURE["clients"]))
    data = await get_adapter("omada").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Connected", "value": "3 / 4"} and data.status == "bad"
    assert data.secondary == [{"label": "Clients", "value": 30}, {"label": "Sites", "value": 2}]
    assert data.metrics == {"offline": 1.0, "clients": 30.0} and data.meta["status_reason"] == "1 device(s) not answering"
    request = token.calls[0].request
    assert request.url.params["grant_type"] == "client_credentials"
    assert json.loads(request.content) == {"omadacId": OMADA_ID, "client_id": "client-1", "client_secret": "secret-1"}
    assert token.call_count == 1, "the token is kept"
    assert clients.calls[0].request.headers["Authorization"] == "AccessToken=AT-example"
    assert json.loads(clients.calls[0].request.content) == {"page": 1, "pageSize": 1, "scope": 1, "filters": {}}


@respx.mock
async def test_the_devices_in_trouble_first_then_by_kind() -> None:
    _controller(respx.mock)
    data = await get_adapter("omada").fetch("devices", CONFIG, {}, _ctx())
    assert [(item["title"], item["value"], item["status"]) for item in data.items] == [
        ("AP Garage", "Heartbeat missed", "bad"), ("Gateway", "Connected", "ok"), ("Core switch", "Connected", "ok"), ("AP Living room", "Connected", "ok")]
    assert data.items[3]["subtitle"] == "Home · EAP650 · 12 clients · CPU 15% · up 2d 5h"
    assert data.items[0]["subtitle"] == "Garage · EAP225-Outdoor · 3 clients", "no figures from a device that is gone"
    assert data.status == "bad"


@respx.mock
async def test_one_site_by_name() -> None:
    _controller(respx.mock)
    data = await get_adapter("omada").fetch("devices", {**CONFIG, "site": "home"}, {}, _ctx())
    assert [item["title"] for item in data.items] == ["Gateway", "Core switch", "AP Living room"]
    assert data.items[0]["subtitle"].startswith("ER605"), "with one site, its name is not repeated"
    with pytest.raises(AdapterError) as refused:
        await get_adapter("omada").fetch("devices", {**CONFIG, "site": "Attic"}, {}, _ctx())
    assert refused.value.code == "unknown_site"


@respx.mock
async def test_an_older_controller_is_asked_the_first_version() -> None:
    _controller(respx.mock)
    respx.post(url__regex=rf"{V2}/sites/[^/]+/clients").mock(return_value=httpx.Response(200, json=FIXTURE["unsupported"]))
    older = respx.get(url__regex=rf"{BASE}/sites/[^/]+/clients").mock(return_value=httpx.Response(200, json=FIXTURE["clients"]))
    data = await get_adapter("omada").fetch("summary", {**CONFIG, "site": "Home"}, {}, _ctx())
    assert older.called and data.secondary == [{"label": "Clients", "value": 15}]


@respx.mock
async def test_a_token_that_ran_out_earns_one_new_one() -> None:
    token = _controller(respx.mock)
    ctx = _ctx()
    respx.get(f"{BASE}/sites").mock(side_effect=[httpx.Response(200, json=FIXTURE["expired"]), httpx.Response(200, json=FIXTURE["sites"])])
    assert await get_adapter("omada").test(CONFIG, ctx) == "The controller answers with 2 sites."
    assert token.call_count == 2


@respx.mock
async def test_a_token_refused_twice_is_a_refusal() -> None:
    _controller(respx.mock)
    respx.get(f"{BASE}/sites").mock(return_value=httpx.Response(200, json=FIXTURE["expired"]))
    with pytest.raises(AuthFailed):
        await get_adapter("omada").test(CONFIG, _ctx())


@respx.mock
async def test_a_wrong_client_says_where_to_look() -> None:
    respx.get(f"{URL}/api/info").mock(return_value=httpx.Response(200, json=FIXTURE["info"]))
    respx.post(f"{URL}/openapi/authorize/token").mock(return_value=httpx.Response(200, json=FIXTURE["bad_client"]))
    with pytest.raises(AuthFailed) as refused:
        await get_adapter("omada").test(CONFIG, _ctx())
    assert "client id or client secret" in str(refused.value) and "Platform Integration" in refused.value.hint
    assert "secret-1" not in str(refused.value)


@respx.mock
async def test_a_given_id_is_not_asked_and_no_open_api_is_said() -> None:
    info = respx.get(f"{URL}/api/info").mock(return_value=httpx.Response(200, json=FIXTURE["info"]))
    respx.post(f"{URL}/openapi/authorize/token").mock(return_value=httpx.Response(404, text="Not found"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("omada").test({**CONFIG, "omada_id": "given"}, _ctx())
    assert refused.value.code == "no_open_api" and not info.called


@respx.mock
async def test_something_else_at_the_address_is_said() -> None:
    respx.get(f"{URL}/api/info").mock(return_value=httpx.Response(200, text="<html></html>"))
    with pytest.raises(AdapterError) as refused:
        await get_adapter("omada").test(CONFIG, _ctx())
    assert refused.value.code == "not_omada"
