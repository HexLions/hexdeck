"""rclone's remote control API.

``rclone_rc.json`` holds what rclone v1.75.2 answered locally on
2026-10-13 as ``rcd`` with a user and password: the version, the stats of
a throttled copy of two large files, and the stats after a copy into a
read-only folder failed three times. The paths are written as
``/data/photos``, ``nas:backup`` and ``offsite:archive``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://rclone.local:5572"
CONFIG = {"url": BASE, "username": "admin", "password": "made-up-password"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "rclone_rc.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _rclone(stats: str = "stats") -> respx.Route:
    respx.post(f"{BASE}/core/version").mock(return_value=httpx.Response(200, json=ANSWERS["version"]))
    return respx.post(f"{BASE}/core/stats").mock(return_value=httpx.Response(200, json=ANSWERS[stats]))


@respx.mock
async def test_a_copy_in_progress() -> None:
    route = _rclone()
    data = await get_adapter("rclone").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "ok"
    assert data.primary == {"label": "Speed", "value": "2.8 MB/s", "metric": "speed"}
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values == {"Copying": 2, "Done": "29.7 MB of 202.0 MB", "Left": "1m", "Errors": 0}
    assert route.calls[0].request.content == b"{}", "an empty body is refused with JSON's content type"


@respx.mock
async def test_errors_turn_it_amber_with_the_last_one() -> None:
    _rclone("stats_errors")
    data = await get_adapter("rclone").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "warn"
    assert data.meta["notice"] == "open offsite:archive/notes.txt.eae89ecb.partial: permission denied"


@respx.mock
async def test_each_file_being_copied() -> None:
    _rclone()
    data = await get_adapter("rclone").fetch("transfers", CONFIG, {}, _ctx())
    assert [(row["title"], row["value"]) for row in data.items] == [("documents.zip", "19%"), ("photos-2026.tar", "10%")]
    assert data.items[0]["subtitle"].endswith("backup-to-nas")


@respx.mock
async def test_a_wrong_password_and_a_400() -> None:
    respx.post(f"{BASE}/core/version").mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(AuthFailed):
        await get_adapter("rclone").test(CONFIG, _ctx())
    respx.post(f"{BASE}/core/version").mock(return_value=httpx.Response(400, json={"error": "failed to read input JSON: EOF", "status": 400}))
    with pytest.raises(AdapterError, match="failed to read input JSON"):
        await get_adapter("rclone").test(CONFIG, _ctx())


@respx.mock
async def test_the_connection_test() -> None:
    _rclone()
    assert await get_adapter("rclone").test(CONFIG, _ctx()) == "rclone v1.75.2 answers, 2 files being copied."
