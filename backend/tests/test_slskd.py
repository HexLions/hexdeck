"""slskd: whether it is on Soulseek, its shares, and its transfers.

``slskd_application.json`` is ``/api/v0/application`` as slskd 0.26.0 answered
it locally on 2026-10-10, without Soulseek credentials. The transfers follow
the shapes in slskd's source at 0.26.0 (TransfersController, UserResponse,
DirectoryResponse, Transfer), with the state flags written the way .NET writes
a flags enum: "Completed, Succeeded".
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context
from app.adapters.slskd import flatten, states

BASE = "http://slskd.local:5030"
CONFIG = {"url": BASE, "api_key": "made-up-key"}
APPLICATION = json.loads((Path(__file__).parent / "fixtures" / "slskd_application.json").read_text())


def _transfer(name: str, state: str, **more: object) -> dict:
    return {"id": name, "username": "vinylhead", "direction": "Download", "filename": f"Music\\Album (1977)\\{name}",
            "size": 31_457_280, "state": state, "bytesTransferred": 0, "averageSpeed": 0, **more}


DOWNLOADS = [{"username": "vinylhead", "directories": [{"directory": "Music\\Album (1977)", "fileCount": 4, "files": [
    _transfer("01.flac", "Completed, Succeeded", percentComplete=100),
    _transfer("02.flac", "InProgress", percentComplete=42.37, averageSpeed=812_000),
    _transfer("03.flac", "Completed, Errored", exception="Transfer rejected: File not shared."),
    _transfer("04.flac", "Queued, Remotely", percentComplete=0),
]}]}]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _slskd(application: dict = APPLICATION) -> None:
    respx.get(f"{BASE}/api/v0/application").mock(return_value=httpx.Response(200, json=application))
    respx.get(f"{BASE}/api/v0/transfers/downloads").mock(return_value=httpx.Response(200, json=DOWNLOADS))
    respx.get(f"{BASE}/api/v0/transfers/uploads").mock(return_value=httpx.Response(200, json=[]))


def test_the_state_flags_are_split() -> None:
    assert states({"state": "Completed, Succeeded"}) == {"Completed", "Succeeded"}
    assert states({"state": None}) == set()


def test_the_transfers_come_out_of_their_users_and_directories() -> None:
    assert [one["id"] for one in flatten(DOWNLOADS)] == ["01.flac", "02.flac", "03.flac", "04.flac"]
    assert flatten(None) == [] and flatten([{"username": "x"}]) == []


@respx.mock
async def test_the_key_goes_in_as_x_api_key() -> None:
    _slskd()
    await get_adapter("slskd").test(CONFIG, _ctx())
    assert respx.calls[0].request.headers["x-api-key"] == "made-up-key"
    assert "authorization" not in respx.calls[0].request.headers


@respx.mock
async def test_without_soulseek_the_status_is_red() -> None:
    _slskd()
    data = await get_adapter("slskd").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert data.primary == {"label": "Soulseek", "value": "offline"}
    assert {"label": "Downloading", "value": 1, "metric": "downloading"} in data.secondary


@respx.mock
async def test_logged_in_with_an_update_waiting() -> None:
    application = {**APPLICATION, "server": {**APPLICATION["server"], "isConnected": True, "isLoggedIn": True},
                   "version": {**APPLICATION["version"], "latest": "0.27.0.0", "isUpdateAvailable": True}}
    _slskd(application)
    data = await get_adapter("slskd").fetch("status", CONFIG, {}, _ctx())
    assert data.status == "ok"
    assert {"label": "Update", "value": "0.27.0.0"} in data.secondary


@respx.mock
async def test_the_running_download_comes_first_then_the_failed_one() -> None:
    _slskd()
    data = await get_adapter("slskd").fetch("downloads", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["02.flac", "03.flac", "04.flac", "01.flac"]
    running, failed, queued, done = data.items
    assert running["progress"] == 42.4 and running["status"] == "ok"
    assert "Transfer rejected" in failed["subtitle"] and failed["status"] == "bad"
    assert queued["value"] == "Queued, Remotely" and queued["status"] == "unknown"
    assert done["value"] == "Succeeded" and "progress" not in done
    assert data.status == "warn"


@respx.mock
async def test_uploads_instead() -> None:
    _slskd()
    data = await get_adapter("slskd").fetch("downloads", CONFIG, {"uploads": True}, _ctx())
    assert data.items == [] and data.meta["empty"] == "No upload yet"


@respx.mock
async def test_a_wrong_key_is_refused() -> None:
    respx.get(f"{BASE}/api/v0/application").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthFailed):
        await get_adapter("slskd").test(CONFIG, _ctx())
