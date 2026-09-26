"""UrBackup: the login exchange, the verdict per client, and the buttons.

The hashing below is checked against what the server computes, done here the
long way round so a change to the adapter cannot quietly redefine it.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, Context

URL = "http://urbackup:55414"
API = f"{URL}/x"
CONFIG = {"url": URL, "username": "admin", "password": "secret"}
SALT, RND, SESSION = "abcd1234", "ff00ff00", "session-1"

NOW = time.time()
CLIENTS = [
    {"id": 1, "name": "nas", "lastbackup": NOW - 3600, "lastbackup_image": NOW - 7200, "file_ok": True, "image_ok": True, "online": True},
    {"id": 2, "name": "laptop", "lastbackup": NOW - 900_000, "lastbackup_image": "-", "file_ok": True, "image_ok": False,
     "image_disabled": True, "online": False},
    {"id": 3, "name": "pi", "lastbackup": "-", "lastbackup_image": "-", "file_ok": False, "image_ok": False,
     "image_not_supported": True, "online": True},
]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def expected_password(rounds: int = 10000) -> str:
    """What the server's own interface would send, computed here independently."""
    digest = hashlib.md5((SALT + "secret").encode(), usedforsecurity=False).digest()
    folded = hashlib.pbkdf2_hmac("sha256", digest, SALT.encode(), rounds).hex() if rounds else digest.hex()
    return hashlib.md5((RND + folded).encode(), usedforsecurity=False).hexdigest()


def _server(clients: list[dict[str, Any]] | None = None, usage: list[dict[str, Any]] | None = None,
            progress: list[dict[str, Any]] | None = None, rounds: int = 10000, sessions: list[str] | None = None,
            anonymous: bool = False, start_ok: bool = True) -> dict[str, int]:
    """The whole server: login, status, usage, progress. Counts what was asked."""
    counted = {"login": 0, "salt": 0, "status": 0, "usage": 0, "progress": 0, "start_backup": 0}
    allowed = sessions

    def answer(request: httpx.Request) -> httpx.Response:
        action = str(request.url.params.get("a"))
        body = dict(pair.split("=", 1) for pair in request.content.decode().split("&") if "=" in pair) if request.content else {}
        counted[action] = counted.get(action, 0) + 1
        if action == "login" and not body:
            return httpx.Response(200, json={"success": True, "session": "anonymous"} if anonymous else {"success": False})
        if action == "salt":
            return httpx.Response(200, json={"ses": SESSION, "salt": SALT, "rnd": RND, "pbkdf2_rounds": rounds})
        if action == "login":
            return httpx.Response(200, json={"success": body.get("password") == expected_password(rounds)})
        if allowed is not None and body.get("ses") not in allowed:
            # What an expired session really looks like: the key is simply gone.
            return httpx.Response(200, json={})
        if action == "status":
            return httpx.Response(200, json={"status": clients if clients is not None else CLIENTS, "server_identity": "x"})
        if action == "usage":
            return httpx.Response(200, json={"usage": usage if usage is not None else [{"name": "nas", "used": 1_000_000_000}]})
        if action == "progress":
            return httpx.Response(200, json={"progress": progress or []})
        if action == "start_backup":
            return httpx.Response(200, json={"result": [{"start_ok": start_ok}]})
        return httpx.Response(404, text="not routed")

    respx.post(url__startswith=API).mock(side_effect=answer)
    return counted


@respx.mock
async def test_the_password_is_folded_the_way_the_server_folds_it() -> None:
    _server()
    data = await get_adapter("urbackup").fetch("clients", CONFIG, {}, _ctx())
    assert data.items, "a wrong hash would have been refused"


@respx.mock
async def test_a_server_without_pbkdf2_is_still_let_in() -> None:
    _server(rounds=0)
    data = await get_adapter("urbackup").fetch("clients", CONFIG, {}, _ctx())
    assert data.items


@respx.mock
async def test_a_wrong_password_is_named_as_such() -> None:
    _server()
    with pytest.raises(AdapterError) as failure:
        await get_adapter("urbackup").fetch("clients", {**CONFIG, "password": "wrong"}, {}, _ctx())
    assert failure.value.code == "auth_failed"


@respx.mock
async def test_a_fresh_server_lets_anybody_in() -> None:
    counted = _server(anonymous=True, sessions=["anonymous"])
    data = await get_adapter("urbackup").fetch("clients", {"url": URL}, {}, _ctx())
    assert data.items and counted["salt"] == 0, "no salt is asked for when the door is open"


@respx.mock
async def test_the_session_is_kept_and_taken_up_again_when_it_expires() -> None:
    counted = _server(sessions=[SESSION])
    ctx = _ctx()
    await get_adapter("urbackup").fetch("clients", CONFIG, {}, ctx)
    await get_adapter("urbackup").fetch("summary", CONFIG, {}, ctx)
    assert counted["salt"] == 1, "one login for both cards"

    # The server forgets the session; the next call has to log in again itself.
    counted = _server(sessions=["another"])
    with pytest.raises(AdapterError) as failure:
        await get_adapter("urbackup").fetch("clients", CONFIG, {}, ctx)
    assert counted["salt"] >= 1, "it logged in again before giving up"
    assert failure.value.code == "bad_answer"


@respx.mock
async def test_a_machine_in_trouble_comes_first_and_says_why() -> None:
    _server()
    data = await get_adapter("urbackup").fetch("clients", CONFIG, {}, _ctx())
    titles = [item["title"] for item in data.items]
    assert titles == ["pi", "laptop", "nas"]
    assert data.items[0]["subtitle"].startswith("No file backup") and data.items[0]["value"] == "never"
    assert data.items[1]["status"] == "warn", "ten days is late"
    assert "offline" in data.items[1]["subtitle"]
    assert data.status == "bad" and data.primary["value"] == 2


@respx.mock
async def test_an_image_backup_that_is_switched_off_is_not_a_missing_one() -> None:
    _server(clients=[{"name": "laptop", "lastbackup": NOW - 600, "lastbackup_image": "-", "file_ok": True,
                      "image_ok": False, "image_disabled": True, "online": True}])
    data = await get_adapter("urbackup").fetch("clients", CONFIG, {}, _ctx())
    assert data.items[0]["status"] == "ok" and data.status == "ok"


@respx.mock
async def test_how_late_is_late_is_a_setting() -> None:
    _server(clients=[{"name": "nas", "lastbackup": NOW - 86400 * 2, "lastbackup_image": NOW - 86400 * 2,
                      "file_ok": True, "image_ok": True, "online": True}])
    patient = await get_adapter("urbackup").fetch("clients", CONFIG, {"stale_days": 7}, _ctx())
    assert patient.items[0]["status"] == "ok"
    strict = await get_adapter("urbackup").fetch("clients", CONFIG, {"stale_days": 1}, _ctx())
    assert strict.items[0]["status"] == "warn"


@respx.mock
async def test_the_summary_adds_the_storage_up() -> None:
    _server(usage=[{"name": "nas", "used": 1_000_000_000}, {"name": "laptop", "used": 2_000_000_000}])
    data = await get_adapter("urbackup").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary["value"] == 3
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Online"] == 2 and labels["Late"] == 2
    assert labels["Stored"] == "2.8 GB"
    assert data.metrics["used"] == 3_000_000_000.0


@respx.mock
async def test_what_is_running_carries_how_far_along_it_is() -> None:
    _server(progress=[{"name": "desk", "action": 3, "pcdone": 42.5, "details": "C:", "paused": False},
                      {"name": "nas", "action": 1, "pcdone": 0, "paused": True}])
    data = await get_adapter("urbackup").fetch("running", CONFIG, {}, _ctx())
    assert data.items[0]["subtitle"].startswith("Image backup") and data.items[0]["progress"] == 42.5
    assert data.items[1]["status"] == "warn" and "paused" in data.items[1]["subtitle"]
    assert data.primary["value"] == 2


@respx.mock
async def test_nothing_running_is_not_an_empty_card_without_a_word() -> None:
    _server()
    data = await get_adapter("urbackup").fetch("running", CONFIG, {}, _ctx())
    assert data.items == [] and data.meta["empty"] == "Nothing is running."


@respx.mock
async def test_the_backup_button_is_offered_only_when_it_is_asked_for() -> None:
    _server()
    plain = await get_adapter("urbackup").fetch("clients", CONFIG, {}, _ctx())
    assert all("actions" not in item for item in plain.items)
    offered = await get_adapter("urbackup").fetch("clients", CONFIG, {"starting": True}, _ctx())
    assert offered.items[0]["actions"][0]["id"] == "incr_file"
    said = await get_adapter("urbackup").action("clients", "incr_file", {"client": "nas"}, CONFIG, {}, _ctx())
    assert "nas" in said


@respx.mock
async def test_a_backup_the_server_will_not_start_is_reported() -> None:
    _server(start_ok=False)
    with pytest.raises(AdapterError) as failure:
        await get_adapter("urbackup").action("clients", "incr_file", {"client": "laptop"}, CONFIG, {}, _ctx())
    assert failure.value.code == "not_started"


@respx.mock
async def test_the_api_path_is_added_to_the_address_of_the_interface() -> None:
    _server()
    await get_adapter("urbackup").fetch("clients", {**CONFIG, "url": f"{URL}/x"}, {}, _ctx())
    assert all("/x/x" not in str(call.request.url) for call in respx.calls)


@respx.mock
async def test_something_that_is_not_urbackup_says_so() -> None:
    respx.post(url__startswith=API).mock(return_value=httpx.Response(200, text="<html>router</html>"))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("urbackup").fetch("clients", CONFIG, {}, _ctx())
    assert failure.value.code == "not_json"


@respx.mock
async def test_the_test_button_counts_the_clients() -> None:
    _server()
    assert "3 clients" in await get_adapter("urbackup").test(CONFIG, _ctx())


def test_dates_the_server_gives_in_its_own_way() -> None:
    from app.adapters.urbackup import when

    assert when(0) is None and when("-") is None and when("") is None and when(None) is None
    assert when(1_788_600_000) == 1_788_600_000.0
    assert when("1788600000") == 1_788_600_000.0
    assert when("16.09.26 03:00") is None, "a formatted date is shown as it came, not guessed at"


def test_the_demo_draws() -> None:
    for kind in ("clients", "summary", "running"):
        assert get_adapter("urbackup").demo(kind, {}, 3).primary
