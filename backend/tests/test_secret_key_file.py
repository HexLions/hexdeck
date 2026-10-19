"""HEXDECK_SECRET_KEY_FILE: the key from a Docker or Kubernetes secret."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_the_key_is_read_from_its_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEXDECK_SECRET_KEY", raising=False)
    secret = tmp_path / "hexdeck_key"
    secret.write_text("a-key-that-lives-in-a-docker-secret-file\n", encoding="utf-8")
    previous = tmp_path / "hexdeck_previous"
    previous.write_text("the-key-from-before-it-was-changed-somewhere", encoding="utf-8")
    monkeypatch.setenv("HEXDECK_SECRET_KEY_FILE", str(secret))
    monkeypatch.setenv("HEXDECK_SECRET_KEY_PREVIOUS_FILE", str(previous))
    settings = Settings()
    assert settings.secret_key == "a-key-that-lives-in-a-docker-secret-file", "the trailing newline is not part of it"
    assert settings.resolved_secret_key() == settings.secret_key
    assert settings.secret_key_previous == "the-key-from-before-it-was-changed-somewhere"


def test_both_forms_at_once_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "hexdeck_key"
    secret.write_text("a-key-that-lives-in-a-docker-secret-file", encoding="utf-8")
    monkeypatch.setenv("HEXDECK_SECRET_KEY", "another-key-in-the-compose-file-itself")
    monkeypatch.setenv("HEXDECK_SECRET_KEY_FILE", str(secret))
    with pytest.raises(ValidationError, match="both set"):
        Settings()


@pytest.mark.parametrize("content", [None, "  \n"])
def test_a_missing_or_empty_file_stops_the_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None) -> None:
    monkeypatch.delenv("HEXDECK_SECRET_KEY", raising=False)
    secret = tmp_path / "hexdeck_key"
    if content is not None:
        secret.write_text(content, encoding="utf-8")
    monkeypatch.setenv("HEXDECK_SECRET_KEY_FILE", str(secret))
    with pytest.raises(ValidationError):
        Settings()
