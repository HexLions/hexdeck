"""A start that migrates the database writes a snapshot of it first.

⚠️ An update by Watchtower or a re-pull took no backup, and the first start of
the new image migrated the only copy there was.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import migrations
from app.services import backup


def _steps(monkeypatch: pytest.MonkeyPatch, newest: int) -> None:
    monkeypatch.setattr(migrations, "MIGRATIONS", [(number, f"step {number}", lambda connection: None) for number in range(2, newest + 1)])


def test_a_database_with_steps_ahead_is_copied_first(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _steps(monkeypatch, 2)
    migrations.migrate()
    assert backup.listing() == [], "a fresh database has nothing to keep"

    _steps(monkeypatch, 3)
    migrations.migrate()
    entries = backup.listing()
    assert len(entries) == 1
    assert entries[0].kind == backup.AUTOMATIC
    assert "before-schema-3" in entries[0].name
    assert backup._read_profile(backup.file(entries[0].name)).schema == 2, "the copy is the schema before the step"
    assert migrations.schema_version() == 3

    migrations.migrate()
    assert len(backup.listing()) == 1, "nothing to migrate, nothing copied"


def test_a_snapshot_that_fails_does_not_stop_the_start(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _steps(monkeypatch, 2)
    migrations.migrate()
    _steps(monkeypatch, 3)

    def broken(**_kwargs: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(backup, "create", broken)
    migrations.migrate()
    assert migrations.schema_version() == 3
