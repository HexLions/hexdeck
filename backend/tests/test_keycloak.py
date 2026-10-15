"""Keycloak: sessions, users and failed sign-ins of one realm.

``keycloak_admin.json`` holds what Keycloak 26.8.1 answered locally on
2026-10-15 to a service account with the four view roles: four users, two
sessions through a public client, four failed sign-ins, the realm with
events on, and the refusal of a wrong secret. The address is written as
``192.168.1.20``; the token is made up.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

BASE = "https://sso.example.com"
CONFIG = {"url": BASE, "realm": "home", "client_id": "hexdeck", "client_secret": "made-up-client-secret"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "keycloak_admin.json").read_text())
ADMIN = f"{BASE}/admin/realms/home"


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _keycloak(events_on: bool = True) -> respx.Route:
    # The recorded failures are a few minutes old wherever this runs.
    now = time.time() * 1000
    errors = [{**event, "time": now - 60_000 * (index + 1)} for index, event in enumerate(ANSWERS["errors"])]
    token = respx.post(f"{BASE}/realms/home/protocol/openid-connect/token").mock(return_value=httpx.Response(200, json=ANSWERS["token"]))
    respx.get(ADMIN).mock(return_value=httpx.Response(200, json={**ANSWERS["realm"], "eventsEnabled": events_on}))
    respx.get(f"{ADMIN}/users/count").mock(return_value=httpx.Response(200, json=ANSWERS["count"]))
    respx.get(f"{ADMIN}/client-session-stats").mock(return_value=httpx.Response(200, json=ANSWERS["sessions"]))
    respx.get(f"{ADMIN}/events").mock(return_value=httpx.Response(200, json=errors))
    return token


@respx.mock
async def test_the_realm_card() -> None:
    token = _keycloak()
    data = await get_adapter("keycloak").fetch("realm", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Sessions", "value": 2, "metric": "sessions"}
    assert data.secondary == [{"label": "Users", "value": 4}, {"label": "Failed today", "value": 4, "metric": "failed"}]
    assert data.status == "ok"
    amber = await get_adapter("keycloak").fetch("realm", CONFIG, {"warn_at": 3}, _ctx())
    assert amber.status == "warn"
    sent = dict(httpx.QueryParams(token.calls[0].request.content.decode()))
    assert sent == {"grant_type": "client_credentials", "client_id": "hexdeck", "client_secret": "made-up-client-secret"}
    events = next(call.request for call in respx.calls if call.request.url.path.endswith("/events"))
    assert events.url.params["type"] == "LOGIN_ERROR"
    assert events.headers["authorization"] == "Bearer made-up.access.token"


@respx.mock
async def test_the_failed_sign_ins_say_who_and_why() -> None:
    _keycloak()
    data = await get_adapter("keycloak").fetch("failed", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["nobody", "carla", "carla", "anna"]
    assert data.items[0]["subtitle"].startswith("unknown user · 192.168.1.20 · photos")
    assert data.items[1]["subtitle"].startswith("wrong password")


@respx.mock
async def test_events_off_is_said_not_counted_as_zero() -> None:
    _keycloak(events_on=False)
    data = await get_adapter("keycloak").fetch("realm", CONFIG, {}, _ctx())
    assert not any(row["label"] == "Failed today" for row in data.secondary)
    assert "records no sign-in events" in data.meta["notice"]


@respx.mock
async def test_the_sessions_by_client() -> None:
    _keycloak()
    data = await get_adapter("keycloak").fetch("clients", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in data.items] == [("photos", 2)]


@respx.mock
async def test_a_wrong_secret_and_missing_roles() -> None:
    respx.post(f"{BASE}/realms/home/protocol/openid-connect/token").mock(return_value=httpx.Response(401, json=ANSWERS["wrong_secret"]))
    with pytest.raises(AuthFailed, match="Invalid client"):
        await get_adapter("keycloak").test(CONFIG, _ctx())
    respx.post(f"{BASE}/realms/home/protocol/openid-connect/token").mock(return_value=httpx.Response(200, json=ANSWERS["token"]))
    respx.get(f"{ADMIN}/users/count").mock(return_value=httpx.Response(403, json={"error": "HTTP 403 Forbidden"}))
    with pytest.raises(AuthFailed, match="may not read"):
        await get_adapter("keycloak").test(CONFIG, _ctx())


@respx.mock
async def test_the_token_is_kept_while_it_lasts() -> None:
    token = _keycloak()
    context = _ctx()
    await get_adapter("keycloak").test(CONFIG, context)
    await get_adapter("keycloak").test(CONFIG, context)
    assert token.call_count == 1
