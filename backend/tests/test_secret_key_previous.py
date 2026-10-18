"""HEXDECK_SECRET_KEY_PREVIOUS: a changed key without entering every API key again."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, crypto
from app.db import db_session, get_engine
from app.models import Integration, Setting

OLD = "test-secret-key-not-for-production"
NEW = "a-new-key-after-the-old-one-was-shown-somewhere"


def _use(monkeypatch: pytest.MonkeyPatch, key: str, previous: str | None) -> None:
    monkeypatch.setenv("HEXDECK_SECRET_KEY", key)
    if previous is None:
        monkeypatch.delenv("HEXDECK_SECRET_KEY_PREVIOUS", raising=False)
    else:
        monkeypatch.setenv("HEXDECK_SECRET_KEY_PREVIOUS", previous)
    config.reset_settings_cache()
    crypto.forget_key()


def _store_under_old_key() -> tuple[int, str]:
    """A connection and a nested setting, both written under the old key."""
    with db_session() as db:
        integration = Integration(kind="sonarr", name="Sonarr", config={"url": "http://sonarr.lan", "api_key": crypto.encrypt("the-api-key")})
        db.add(integration)
        db.merge(Setting(key="mail", value={"host": "mail.example.com", "password": crypto.encrypt("the-mail-password"), "nested": [crypto.encrypt("deep")]}))
        db.commit()
        return integration.id, integration.config["api_key"]


def test_the_previous_key_moves_every_secret_to_the_new_one(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    integration_id, before = _store_under_old_key()

    _use(monkeypatch, NEW, OLD)
    assert crypto.rewrap_previous(get_engine()) == {"rewritten": 3, "unreadable": 0}

    # Without the old key from here on: everything opens under the new one alone.
    _use(monkeypatch, NEW, None)
    with db_session() as db:
        stored = db.get(Integration, integration_id).config["api_key"]
        mail = db.get(Setting, "mail").value
    assert stored != before and crypto.is_encrypted(stored)
    assert crypto.decrypt(stored) == "the-api-key"
    assert crypto.decrypt(mail["password"]) == "the-mail-password"
    assert crypto.decrypt(mail["nested"][0]) == "deep"
    assert mail["host"] == "mail.example.com", "plain values are left alone"


def test_a_second_start_with_the_variable_still_set_rewrites_nothing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _store_under_old_key()
    _use(monkeypatch, NEW, OLD)
    assert crypto.rewrap_previous(get_engine())["rewritten"] == 3
    assert crypto.rewrap_previous(get_engine()) == {"rewritten": 0, "unreadable": 0}


def test_a_secret_neither_key_opens_is_left_and_counted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    integration_id, before = _store_under_old_key()
    _use(monkeypatch, NEW, "not-the-key-it-was-written-with")
    assert crypto.rewrap_previous(get_engine()) == {"rewritten": 0, "unreadable": 3}
    with db_session() as db:
        assert db.get(Integration, integration_id).config["api_key"] == before


def test_without_the_variable_nothing_runs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _store_under_old_key()
    _use(monkeypatch, OLD, None)
    assert crypto.rewrap_previous(get_engine()) is None
    # The same key twice is no previous key either.
    _use(monkeypatch, OLD, OLD)
    assert crypto.rewrap_previous(get_engine()) is None


def test_the_start_does_it_and_says_so(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import main
    from app.main import app

    with TestClient(app):
        integration_id, _before = _store_under_old_key()
    _use(monkeypatch, NEW, OLD)
    # The journal takes over the handlers on start, so the warning is caught at the logger.
    said: list[str] = []
    monkeypatch.setattr(main.logger, "warning", lambda message, *args: said.append(message % args))
    with TestClient(app):
        pass
    assert any(line.startswith("HEXDECK_SECRET_KEY_PREVIOUS is set: 3 stored secret(s)") for line in said), said
    _use(monkeypatch, NEW, None)
    with db_session() as db:
        assert crypto.decrypt(db.get(Integration, integration_id).config["api_key"]) == "the-api-key"


@pytest.mark.parametrize("value", ["generate-one-and-keep-it", "change-me", "  "])
def test_an_example_or_blank_key_stops_the_start(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """⚠️ The README's example value used to be taken as it was, and every
    session cookie is signed with a key made from it."""
    from app import main

    monkeypatch.setenv("HEXDECK_SECRET_KEY", value)
    config.reset_settings_cache()
    with pytest.raises(SystemExit):
        main._refuse_example_secret()
    monkeypatch.setenv("HEXDECK_SECRET_KEY", NEW)
    config.reset_settings_cache()
    main._refuse_example_secret()
    config.reset_settings_cache()
