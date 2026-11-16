"""A refused connection test names the host that was asked.

⚠️ "The service rejected the credentials. Check the API key or the password."
is true and can still point the wrong way: a second Proxmox entered with the
address of the first one is refused with a token that is correct, because the
token belongs to the other machine. The hint names the host, so the address
is checked along with the key.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Unreachable
from app.routers.integrations import _asked

from .conftest import CSRF, setup_admin

CONFIG = {"url": "https://192.0.2.199:8006", "api_key": "made-up"}


@pytest.fixture
def refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(self: object, config: dict, ctx: object) -> str:
        raise AuthFailed()

    monkeypatch.setattr(type(get_adapter("radarr")), "test", refused)


def test_a_refusal_names_the_host_on_both_routes(client: TestClient, refuses: None) -> None:
    setup_admin(client)
    made = client.post("/api/v1/integrations", json={"kind": "radarr", "name": "Movies", "config": CONFIG}, headers=CSRF)
    assert made.status_code == 201, made.text

    for answer in (
        client.post("/api/v1/integrations/test", json={"kind": "radarr", "config": CONFIG}, headers=CSRF),
        client.post(f"/api/v1/integrations/{made.json()['id']}/test", headers=CSRF),
    ):
        assert answer.json() == {
            "ok": False,
            "message": "The service rejected the credentials.",
            "hint": "Check the API key or the password. If the credentials are right, check the address: 192.0.2.199:8006 may be another machine.",
            "code": "auth_failed",
        }


def test_another_failure_keeps_its_own_hint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_admin(client)

    async def away(self: object, config: dict, ctx: object) -> str:
        raise Unreachable()

    monkeypatch.setattr(type(get_adapter("radarr")), "test", away)
    answer = client.post("/api/v1/integrations/test", json={"kind": "radarr", "config": CONFIG}, headers=CSRF)
    assert answer.json()["hint"] == "Check the URL and the network."


@pytest.mark.parametrize(("address", "host"), [
    ("https://192.0.2.199:8006", "192.0.2.199:8006"),
    ("http://Radarr.example.com/base/", "radarr.example.com"),
    ("192.0.2.199:8006", "192.0.2.199:8006"),
    ("https://[2001:db8::1]:8006", "[2001:db8::1]:8006"),
    # ⚠️ Whatever stands in front of the @ is a user name and a password.
    ("https://someone:made-up@nas.example.com:5001", "nas.example.com:5001"),
    ("", ""),
    ("https://nas.example.com:port", ""),
])
def test_only_the_host_and_the_port_are_named(address: str, host: str) -> None:
    assert _asked(get_adapter("radarr"), {"url": address}) == host


def test_an_address_kept_secret_is_never_named() -> None:
    """A private calendar address is a secret as a whole, host included."""
    assert _asked(get_adapter("ical"), {"url": "https://calendar.example.com/private-abc.ics"}) == ""
