"""/api/metrics: HexDeck's own state for Prometheus, behind HEXDECK_METRICS_TOKEN."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import config
from app.routers.metrics import _label

from .conftest import setup_admin

TOKEN = "a-long-token-for-prometheus-only"


def _token(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("HEXDECK_METRICS_TOKEN", raising=False)
    else:
        monkeypatch.setenv("HEXDECK_METRICS_TOKEN", value)
    config.reset_settings_cache()


def test_off_unless_a_token_is_set(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _token(monkeypatch, None)
    assert client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_only_with_the_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A signed-in browser is not enough either: the labels name the boards."""
    setup_admin(client)
    _token(monkeypatch, TOKEN)
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_the_cards_and_the_rest(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_admin(client, demo=True)
    _token(monkeypatch, TOKEN)
    deadline = time.monotonic() + 10
    text = ""
    while time.monotonic() < deadline:
        answer = client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert answer.status_code == 200, answer.text
        text = answer.text
        if "hexdeck_card_up{" in text:
            break
        time.sleep(0.2)
    assert answer.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'hexdeck_info{version="' in text
    assert "# TYPE hexdeck_card_read_seconds gauge" in text
    assert 'hexdeck_card_up{widget="' in text and 'board="' in text
    assert 'hexdeck_cards{status="ok"}' in text
    assert "hexdeck_stream_connections 0" in text
    assert "hexdeck_backup_copy_ok" not in text, "no copy folder, no copy metric"


def test_label_values_are_escaped() -> None:
    assert _label('a "b"\\c\nd') == 'a \\"b\\"\\\\c\\nd'


def test_the_backup_and_its_copy(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services import backup

    setup_admin(client)
    _token(monkeypatch, TOKEN)
    monkeypatch.setenv("HEXDECK_BACKUP_COPY_DIR", str(tmp_path))
    monkeypatch.setenv("HEXDECK_BACKUP_COPY_PASSWORD", "a-copy-password-for-the-nas")
    config.reset_settings_cache()
    backup.last_copy.update({"at": None, "name": "", "error": ""})
    text = client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).text
    assert "hexdeck_backup_copy_ok 0" in text, "a copy folder, but no copy yet"
    backup.write_one_if_due()
    text = client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"}).text
    assert "hexdeck_backup_copy_ok 1" in text and "hexdeck_backup_last_timestamp_seconds " in text
