"""Pi-PaaS: apps running, crashed or silent, the machine, and the restart button.

``pipaas_api.json`` holds what Pi-PaaS 1.2.0 answered locally on 2026-11-15:
``/api/summary`` read with a viewer token (a Node app running, one parked
after crashing, a stopped static site), the answer to a restart with an
operator token, and the refusals for a wrong token, a viewer token
restarting and an unknown app. The machine is written as
``raspberrypi.local``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://raspberrypi.local:9000"
CONFIG = {"url": BASE, "token": "pp_0123456789ab_made-up-secret-made-up-secret"}
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "pipaas_api.json").read_text())


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _pipaas() -> respx.Route:
    return respx.get(f"{BASE}/api/summary").mock(return_value=httpx.Response(200, json=ANSWERS["summary"]))


@respx.mock
async def test_the_overview_counts_running_and_crashed_apps() -> None:
    route = _pipaas()
    data = await get_adapter("pipaas").fetch("overview", CONFIG, {}, _ctx())
    assert route.calls[0].request.headers["authorization"] == f"Bearer {CONFIG['token']}"
    assert data.status == "bad"
    assert data.primary == {"label": "Running", "value": "1/3", "metric": "running"}
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values["Apps"] == 3 and values["Crashed"] == 1 and values["CPU"] == 20
    assert data.meta["notice"] == "Crashed: Weather bot"


@respx.mock
async def test_the_list_puts_the_crashed_app_first() -> None:
    _pipaas()
    data = await get_adapter("pipaas").fetch("apps", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["Weather bot", "Photo blog", "Link hub"]
    assert [row["status"] for row in data.items] == ["bad", "ok", "unknown"]
    assert data.items[1]["subtitle"] == "running · port 3004 · 43.0 MB"
    assert data.items[1]["url"] == "http://raspberrypi.local:3004"
    assert all(row["actions"] == [] for row in data.items), "no button unless the card asks for it"
    only = await get_adapter("pipaas").fetch("apps", CONFIG, {"only_troubled": True}, _ctx())
    assert [row["title"] for row in only.items] == ["Weather bot"]


@respx.mock
async def test_an_app_that_stops_answering_turns_the_card_amber() -> None:
    summary = json.loads(json.dumps(ANSWERS["summary"]))
    summary["apps"] = [app for app in summary["apps"] if app["status"] != "crashed"]
    next(app for app in summary["apps"] if app["id"] == "photo-blog")["healthy"] = False
    respx.get(f"{BASE}/api/summary").mock(return_value=httpx.Response(200, json=summary))
    data = await get_adapter("pipaas").fetch("overview", CONFIG, {}, _ctx())
    assert data.status == "warn" and data.meta["notice"] == "Not responding: Photo blog"


@respx.mock
async def test_the_restart_button_restarts_only_a_listed_app() -> None:
    _pipaas()
    restart = respx.post(f"{BASE}/api/apps/photo-blog/restart").mock(return_value=httpx.Response(200, json=ANSWERS["restart"]))
    data = await get_adapter("pipaas").fetch("apps", CONFIG, {"restarting": True}, _ctx())
    blog = next(row for row in data.items if row["id"] == "photo-blog")
    assert blog["actions"][0]["params"] == {"app": "photo-blog"} and blog["actions"][0]["confirm"] is True
    assert next(row for row in data.items if row["id"] == "link-hub")["actions"] == [], "a stopped app has no restart"
    assert await get_adapter("pipaas").action("apps", "restart", {"app": "photo-blog"}, CONFIG, {}, _ctx()) == "Photo blog restarted."
    assert restart.called
    with pytest.raises(AdapterError, match="no such app"):
        await get_adapter("pipaas").action("apps", "restart", {"app": "../users"}, CONFIG, {}, _ctx())


@respx.mock
async def test_a_viewer_token_may_not_restart() -> None:
    _pipaas()
    respx.post(f"{BASE}/api/apps/photo-blog/restart").mock(return_value=httpx.Response(403, json=ANSWERS["not_allowed"]))
    with pytest.raises(AuthFailed, match="may not restart"):
        await get_adapter("pipaas").action("apps", "restart", {"app": "photo-blog"}, CONFIG, {}, _ctx())


@respx.mock
async def test_a_wrong_token_and_a_wrong_address() -> None:
    respx.get(f"{BASE}/api/summary").mock(return_value=httpx.Response(401, json=ANSWERS["refused"]))
    with pytest.raises(AuthFailed, match="refused"):
        await get_adapter("pipaas").test(CONFIG, _ctx())
    respx.get("http://elsewhere.local/api/summary").mock(return_value=httpx.Response(200, text="<html>router</html>"))
    with pytest.raises(AdapterError, match="JSON"):
        await get_adapter("pipaas").test({**CONFIG, "url": "http://elsewhere.local"}, _ctx())


@respx.mock
async def test_the_connection_test_names_the_version() -> None:
    _pipaas()
    assert await get_adapter("pipaas").test(CONFIG, _ctx()) == "Pi-PaaS 1.2.0 answers with 3 apps."


@respx.mock
async def test_a_failed_update_is_amber_on_both_cards() -> None:
    summary = json.loads(json.dumps(ANSWERS["summary"]))
    summary["apps"] = [app for app in summary["apps"] if app["status"] != "crashed"]
    next(app for app in summary["apps"] if app["id"] == "photo-blog")["lastError"] = "npm run build failed (exit code 1)"
    respx.get(f"{BASE}/api/summary").mock(return_value=httpx.Response(200, json=summary))
    overview = await get_adapter("pipaas").fetch("overview", CONFIG, {}, _ctx())
    assert overview.status == "warn" and overview.meta["notice"] == "Failed update: Photo blog"
    apps = await get_adapter("pipaas").fetch("apps", CONFIG, {}, _ctx())
    blog = next(row for row in apps.items if row["id"] == "photo-blog")
    assert blog["status"] == "warn" and blog["subtitle"].endswith("npm run build failed (exit code 1)")


@respx.mock
async def test_restarting_an_app_that_went_away_or_is_busy() -> None:
    _pipaas()
    route = respx.post(f"{BASE}/api/apps/photo-blog/restart")
    route.mock(return_value=httpx.Response(404, json=ANSWERS["unknown_app"]))
    with pytest.raises(AdapterError, match="no longer has this app"):
        await get_adapter("pipaas").action("apps", "restart", {"app": "photo-blog"}, CONFIG, {}, _ctx())
    route.mock(return_value=httpx.Response(409, json={"error": "App is busy (updating), try again when it has finished"}))
    with pytest.raises(AdapterError, match="busy"):
        await get_adapter("pipaas").action("apps", "restart", {"app": "photo-blog"}, CONFIG, {}, _ctx())
