"""Signing in by itself on a trusted network: a browser from one of the named
networks is the chosen account without a password, until somebody signs out
on purpose.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import ADMIN, CSRF, create_user, login, setup_admin


def _from(client: TestClient, address: str) -> TestClient:
    """A browser at another address, with no cookies of its own."""
    return TestClient(client.app, client=(address, 40000))


def _configure(client: TestClient, user_id: int | None, networks: list[str], enabled: bool = True) -> dict:
    answer = client.put("/api/v1/settings/auto-login", json={"enabled": enabled, "user_id": user_id, "networks": networks}, headers=CSRF)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_a_browser_on_a_trusted_network_is_signed_in_without_a_password(client: TestClient) -> None:
    setup_admin(client)
    kim = create_user(client, "kim")
    _configure(client, kim["id"], ["192.168.1.0/24", "10.0.0.0/8"])
    inside = _from(client, "192.168.1.23")
    me = inside.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["username"] == "kim" and me.json()["auth_kind"] == "auto"
    assert "nexdeck_session" in inside.cookies, "a real session, so everything after works as usual"
    assert inside.get("/api/v1/boards").status_code == 200
    outside = _from(client, "203.0.113.9")
    assert outside.get("/api/v1/auth/me").status_code == 401


def test_signing_out_on_purpose_holds_until_sign_in(client: TestClient) -> None:
    setup_admin(client)
    kim = create_user(client, "kim")
    _configure(client, kim["id"], ["192.168.0.0/16"])
    inside = _from(client, "192.168.7.7")
    assert inside.get("/api/v1/auth/me").status_code == 200
    assert inside.post("/api/v1/auth/logout", headers=CSRF).status_code == 204
    assert inside.get("/api/v1/auth/me").status_code == 401, "signed out means signed out"
    offer = inside.get("/api/v1/auth/auto").json()
    assert offer == {"available": True, "name": "kim"}, "the sign-in page may offer the way back in"
    back = inside.post("/api/v1/auth/auto", headers=CSRF)
    assert back.status_code == 200 and back.json()["username"] == "kim"
    assert inside.get("/api/v1/auth/me").status_code == 200
    # A password sign-in as somebody else wins, and stays.
    inside.post("/api/v1/auth/logout", headers=CSRF)
    login(inside, ADMIN["username"], ADMIN["password"])
    assert inside.get("/api/v1/auth/me").json()["username"] == "admin"


def test_the_address_behind_a_trusted_proxy_is_the_forwarded_one(client: TestClient, monkeypatch) -> None:
    from app.config import get_settings

    setup_admin(client)
    kim = create_user(client, "kim")
    _configure(client, kim["id"], ["192.168.1.0/24"])
    monkeypatch.setattr(get_settings(), "trusted_proxies", "172.16.0.0/12")
    proxy = _from(client, "172.18.0.2")
    assert proxy.get("/api/v1/auth/me").status_code == 401, "the proxy itself is not on the trusted network"
    assert proxy.get("/api/v1/auth/me", headers={"X-Forwarded-For": "192.168.1.5, 172.18.0.2"}).status_code == 200
    assert _from(client, "172.18.0.2").get("/api/v1/auth/me", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401
    # Without a trusted proxy the header is ignored: anybody could send it.
    monkeypatch.setattr(get_settings(), "trusted_proxies", "")
    assert _from(client, "203.0.113.9").get("/api/v1/auth/me", headers={"X-Forwarded-For": "192.168.1.5"}).status_code == 401


def test_the_setting_is_checked_and_read_back(client: TestClient) -> None:
    setup_admin(client)
    kim = create_user(client, "kim")
    assert client.get("/api/v1/settings/auto-login").json() == {"enabled": False, "user_id": None, "networks": []}
    bad = client.put("/api/v1/settings/auto-login", json={"enabled": True, "user_id": kim["id"], "networks": ["0.0.0.0/0"]}, headers=CSRF)
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bad_network"
    assert client.put("/api/v1/settings/auto-login", json={"enabled": True, "user_id": kim["id"], "networks": ["not a network"]}, headers=CSRF).status_code == 400
    assert client.put("/api/v1/settings/auto-login", json={"enabled": True, "user_id": 999, "networks": ["192.168.1.0/24"]}, headers=CSRF).status_code == 400
    assert client.put("/api/v1/settings/auto-login", json={"enabled": True, "user_id": kim["id"], "networks": []}, headers=CSRF).status_code == 400
    stored = _configure(client, kim["id"], ["192.168.1.5", "fd00::/8"])
    assert stored == {"enabled": True, "user_id": kim["id"], "networks": ["192.168.1.5/32", "fd00::/8"]}
    off = _configure(client, kim["id"], ["192.168.1.0/24"], enabled=False)
    assert off["enabled"] is False
    assert _from(client, "192.168.1.5").get("/api/v1/auth/me").status_code == 401


def test_a_disabled_or_missing_account_signs_nobody_in(client: TestClient) -> None:
    setup_admin(client)
    kim = create_user(client, "kim")
    _configure(client, kim["id"], ["192.168.1.0/24"])
    client.patch(f"/api/v1/users/{kim['id']}", json={"disabled": True}, headers=CSRF)
    assert _from(client, "192.168.1.5").get("/api/v1/auth/me").status_code == 401


def test_only_an_administrator_changes_it(client: TestClient) -> None:
    setup_admin(client)
    create_user(client, "kim")
    login(client, "kim", "another-long-password")
    assert client.get("/api/v1/settings/auto-login").status_code == 403
    assert client.put("/api/v1/settings/auto-login", json={"enabled": False, "user_id": None, "networks": []}, headers=CSRF).status_code == 403
