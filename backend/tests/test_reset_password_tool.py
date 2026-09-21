"""The password reset from the shell: a new password, every session ended, the second factor untouched."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.tools.reset_password import main

from .conftest import ADMIN, CSRF, setup_admin


def test_the_tool_sets_a_password_and_signs_the_account_out_everywhere(client: TestClient, capsys) -> None:
    setup_admin(client)
    assert client.get("/api/v1/auth/me").status_code == 200
    assert main(["admin", "--password", "a-brand-new-password"]) == 0
    assert "was set" in capsys.readouterr().out
    assert client.get("/api/v1/auth/me").status_code == 401, "the old session is over"
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": ADMIN["password"]}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "a-brand-new-password"}).status_code == 200


def test_the_tool_makes_a_password_up_when_none_is_given(client: TestClient, capsys) -> None:
    setup_admin(client)
    assert main(["admin"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if "is:" in line)
    password = line.split("is:", 1)[1].strip()
    assert len(password) >= 12
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": password}, headers=CSRF).status_code == 200


def test_the_tool_names_the_accounts_when_the_name_is_wrong(client: TestClient, capsys) -> None:
    setup_admin(client)
    assert main(["root"]) == 1
    assert "Accounts: admin" in capsys.readouterr().err
    assert main(["admin", "--password", "short"]) == 1
