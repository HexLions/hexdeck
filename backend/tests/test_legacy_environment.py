"""An installation configured for nexdeck starts as HexDeck without a change.

The prefix moved from ``NEXDECK_`` to ``HEXDECK_``. Every old name is still
read, the new name wins when both are set, and the start-up log says which
old names are in use so the operator can rename them at leisure.
"""

from __future__ import annotations

from app.config import Settings, adopt_legacy_environment


def test_an_old_name_is_read_when_the_new_one_is_missing() -> None:
    env = {"NEXDECK_LOG_LEVEL": "DEBUG", "NEXDECK_PUBLIC_URL": "https://deck.example.com"}
    adopted = adopt_legacy_environment(env)
    assert adopted == ["NEXDECK_LOG_LEVEL", "NEXDECK_PUBLIC_URL"]
    assert env["HEXDECK_LOG_LEVEL"] == "DEBUG"
    assert env["HEXDECK_PUBLIC_URL"] == "https://deck.example.com"


def test_the_new_name_wins_over_the_old_one() -> None:
    env = {"NEXDECK_LOG_LEVEL": "DEBUG", "HEXDECK_LOG_LEVEL": "WARNING"}
    assert adopt_legacy_environment(env) == []
    assert env["HEXDECK_LOG_LEVEL"] == "WARNING"


def test_the_old_names_of_integrations_are_carried_across_too() -> None:
    env = {"NEXDECK_RADARR_3_API_KEY": "abc"}
    adopt_legacy_environment(env)
    assert env["HEXDECK_RADARR_3_API_KEY"] == "abc"


def test_settings_read_the_new_prefix(monkeypatch) -> None:
    monkeypatch.setenv("HEXDECK_LOG_LEVEL", "WARNING")
    monkeypatch.delenv("NEXDECK_LOG_LEVEL", raising=False)
    assert Settings().log_level == "WARNING"
