"""HEXDECK_BACKUP_COPY_DIR: every scheduled snapshot also lands off the machine.

⚠️ The snapshots sit on the same volume as the database and die with it; the
only copy elsewhere was the one somebody remembered to download.
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest
import pyzipper
from fastapi.testclient import TestClient

from app import config
from app.services import backup

from .conftest import CSRF, setup_admin

PASSWORD = "a-copy-password-for-the-nas"


def _copies_to(monkeypatch: pytest.MonkeyPatch, folder: Path, password: str = PASSWORD, keep: int = 14) -> None:
    monkeypatch.setenv("HEXDECK_BACKUP_COPY_DIR", str(folder))
    monkeypatch.setenv("HEXDECK_BACKUP_COPY_KEEP", str(keep))
    if password:
        monkeypatch.setenv("HEXDECK_BACKUP_COPY_PASSWORD", password)
    else:
        monkeypatch.delenv("HEXDECK_BACKUP_COPY_PASSWORD", raising=False)
    config.reset_settings_cache()
    backup.last_copy.update({"at": None, "name": "", "error": ""})


def _names(blob: bytes) -> list[str]:
    with pyzipper.AESZipFile(io.BytesIO(blob)) as zip_file:
        zip_file.setpassword(PASSWORD.encode("utf-8"))
        return sorted(zip_file.namelist())


def test_a_scheduled_snapshot_is_copied_encrypted(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_admin(client)
    share = tmp_path / "nas"
    share.mkdir()
    _copies_to(monkeypatch, share)
    written = backup.write_one_if_due()
    assert written is not None
    [copy] = list(share.iterdir())
    assert copy.name == f"{written.stem}.zip"
    assert "nexdeck.db" in _names(copy.read_bytes())
    with pyzipper.AESZipFile(copy) as zip_file, pytest.raises(RuntimeError):
        zip_file.read("nexdeck.db")  # no password, nothing opens
    listing = client.get("/api/v1/backups").json()["copies"]
    assert listing == {"folder": str(share), "keep": 14, "password_set": True,
                       "last": {"at": listing["last"]["at"], "name": copy.name, "error": ""}}


def test_a_backup_made_by_hand_is_copied_too(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_admin(client)
    _copies_to(monkeypatch, tmp_path)
    made = client.post("/api/v1/backups", json={"note": "before the upgrade"}, headers=CSRF)
    assert made.status_code == 201, made.text
    assert (tmp_path / f"{made.json()['name'].removesuffix('.db')}.zip").is_file()


def test_old_copies_go_and_foreign_files_stay(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_admin(client)
    _copies_to(monkeypatch, tmp_path, keep=2)
    for age in range(3):
        old = tmp_path / f"hexdeck-automatic-0.29.0-old-{age}.zip"
        old.write_bytes(b"x")
        os.utime(old, (time.time() - 86400 * (age + 1),) * 2)
    mine = tmp_path / "photos.zip"
    mine.write_bytes(b"not ours")
    backup.copy_off(backup.create(kind=backup.MANUAL))
    left = sorted(path.name for path in tmp_path.iterdir())
    assert "photos.zip" in left
    assert "hexdeck-automatic-0.29.0-old-0.zip" in left and len([name for name in left if name.startswith("hexdeck-")]) == 2
    assert not any(name.endswith(".part") for name in left)


@pytest.mark.parametrize("problem", ["no_password", "no_folder"])
def test_a_copy_that_cannot_be_made_is_said_and_the_snapshot_stays(client: TestClient, tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch, problem: str) -> None:
    setup_admin(client)
    if problem == "no_password":
        _copies_to(monkeypatch, tmp_path, password="")
    else:
        _copies_to(monkeypatch, tmp_path / "not-mounted")
    written = backup.write_one_if_due()
    assert written is not None and written.is_file(), "the snapshot itself is written anyway"
    last = client.get("/api/v1/backups").json()["copies"]["last"]
    assert last["name"] == "" and last["error"]
    assert ("PASSWORD" in last["error"]) if problem == "no_password" else ("mounted" in last["error"])


def test_no_folder_no_copies(client: TestClient) -> None:
    setup_admin(client)
    assert backup.copy_off(backup.create(kind=backup.MANUAL)) is None
    assert client.get("/api/v1/backups").json()["copies"]["folder"] == ""
