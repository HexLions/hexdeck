"""A built file with a hash in its name is kept by the browser, and the page that names it is not.

⚠️ The files under ``/assets`` went out with no word about caching, so a
browser guessed from their age and, right after an update, asked for all of
them again on every visit. Only a service worker hid it, and a browser grants
one to https and to localhost alone.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import HashedFiles


def _served(tmp_path: Path) -> TestClient:
    (tmp_path / "index-abc12345.js").write_text("console.log('made up')")
    app = FastAPI()
    app.mount("/assets", HashedFiles(directory=tmp_path), name="assets")
    return TestClient(app)


def test_a_hashed_file_is_kept_for_good(tmp_path: Path) -> None:
    answer = _served(tmp_path).get("/assets/index-abc12345.js")
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_asking_again_is_told_the_same(tmp_path: Path) -> None:
    client = _served(tmp_path)
    first = client.get("/assets/index-abc12345.js")
    again = client.get("/assets/index-abc12345.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_a_file_that_is_not_there_is_not_kept(tmp_path: Path) -> None:
    answer = _served(tmp_path).get("/assets/index-gone0000.js")
    assert answer.status_code == 404
    assert "cache-control" not in answer.headers
