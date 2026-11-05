"""Dahua, against answers shaped as the HACS integration's client and tests have them.

No device was at hand. The bodies are written anew in the shape documented by
rroller/dahua (client.py and tests/dahua), whose notes come from real devices.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.dahua import parse_disks, parse_lines

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dahua_cgi.json").read_text())
URL = "http://192.168.1.20"
CONFIG = {"url": URL, "username": "admin", "password": "secret-1"}
SYSTEM = f"{URL}/cgi-bin/magicBox.cgi?action=getSystemInfo"
VERSION = f"{URL}/cgi-bin/magicBox.cgi?action=getSoftwareVersion"
STORAGE = f"{URL}/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo"
MOTION = f"{URL}/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoMotion"
CHALLENGE = {"WWW-Authenticate": 'Digest realm="Login to device", qop="auth", nonce="abc123", opaque="def456"'}


def _ctx(cache: dict | None = None) -> Context:
    return Context(httpx.AsyncClient(), integration_id=1, widget_id=1, cache={} if cache is None else cache)


def _device(router: respx.Router, **answers: httpx.Response) -> None:
    router.get(SYSTEM).mock(return_value=answers.get("system", httpx.Response(200, text=FIXTURE["system_info"])))
    router.get(VERSION).mock(return_value=answers.get("version", httpx.Response(200, text=FIXTURE["software_version"])))
    router.get(STORAGE).mock(return_value=answers.get("storage", httpx.Response(200, text=FIXTURE["storage"])))
    router.get(MOTION).mock(return_value=answers.get("motion", httpx.Response(200, text=FIXTURE["motion"])))


def test_lines_and_disks_add_partitions_up() -> None:
    disks = parse_disks(parse_lines(FIXTURE["storage"]))
    assert [disk["name"] for disk in disks] == ["/dev/sda", "/dev/sdb"]
    assert disks[0]["total"] == 2e12 and disks[0]["used"] == 1e12 and disks[0]["error"] is False
    assert disks[1]["error"] is True


@pytest.mark.asyncio
async def test_device_card() -> None:
    with respx.mock(assert_all_called=True) as router:
        _device(router)
        data = await get_adapter("dahua").fetch("device", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Motion now", "value": 2}
    assert data.metrics == {"motion": 2.0}
    shown = {one["label"]: one["value"] for one in data.secondary}
    assert shown == {"Model": "DHI-NVR5464-16P-EI", "Firmware": "2.800.0000016.0.R", "Channels": "1, 6", "Disks": "1 / 2"}
    assert data.status == "bad" and "/dev/sdb" in data.meta["status_reason"]


@pytest.mark.asyncio
async def test_no_motion_is_nought_but_no_answer_is_not() -> None:
    with respx.mock() as router:
        _device(router, motion=httpx.Response(200, text=""), storage=httpx.Response(404))
        quiet = await get_adapter("dahua").fetch("device", CONFIG, {}, _ctx())
    assert quiet.primary["value"] == 0 and quiet.metrics == {"motion": 0.0} and quiet.status == "ok"
    with respx.mock() as router:
        _device(router, motion=httpx.Response(404), storage=httpx.Response(404))
        unknown = await get_adapter("dahua").fetch("device", CONFIG, {}, _ctx())
    assert unknown.primary == {"label": "Model", "value": "DHI-NVR5464-16P-EI"} and unknown.metrics == {}


@pytest.mark.asyncio
async def test_disks_card() -> None:
    with respx.mock(assert_all_called=False) as router:
        _device(router)
        data = await get_adapter("dahua").fetch("disks", CONFIG, {}, _ctx())
    assert data.status == "bad"
    assert [(item["title"], item["status"], item["value"]) for item in data.items] == [("/dev/sda", "ok", "50%"), ("/dev/sdb", "bad", "Error")]
    with respx.mock(assert_all_called=False) as router:
        _device(router, storage=httpx.Response(501))
        none = await get_adapter("dahua").fetch("disks", CONFIG, {}, _ctx())
    assert none.items == [] and none.status == "ok"


@pytest.mark.asyncio
async def test_answers_the_digest_challenge() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        given = request.headers.get("authorization", "")
        seen.append(given)
        if not given.startswith("Digest "):
            return httpx.Response(401, headers=CHALLENGE)
        return httpx.Response(200, text=FIXTURE["system_info"])

    with respx.mock() as router:
        router.get(SYSTEM).mock(side_effect=answer)
        message = await get_adapter("dahua").test(CONFIG, _ctx())
    assert message == "DHI-NVR5464-16P-EI answers."
    assert seen[0] == "" and 'username="admin"' in seen[1] and "secret-1" not in seen[1]


@pytest.mark.asyncio
async def test_a_refused_login_leaves_the_device_alone_except_for_the_test_button() -> None:
    cache: dict = {}
    with respx.mock() as router:
        route = router.get(SYSTEM).mock(return_value=httpx.Response(401, headers=CHALLENGE))
        with pytest.raises(AuthFailed):
            await get_adapter("dahua").fetch("device", CONFIG, {}, _ctx(cache))
        asked = route.call_count
        with pytest.raises(AuthFailed):
            await get_adapter("dahua").fetch("device", CONFIG, {}, _ctx(cache))
        assert route.call_count == asked
        with pytest.raises(AuthFailed):
            await get_adapter("dahua").test(CONFIG, _ctx(cache))
        assert route.call_count > asked


@pytest.mark.asyncio
async def test_a_bad_moment_is_not_remembered() -> None:
    cache: dict = {}
    with respx.mock() as router:
        route = router.get(SYSTEM).mock(side_effect=[httpx.Response(500), httpx.Response(200, text=FIXTURE["system_info"])])
        router.get(VERSION).mock(return_value=httpx.Response(200, text=FIXTURE["software_version"]))
        router.get(STORAGE).mock(return_value=httpx.Response(200, text=FIXTURE["storage"]))
        router.get(MOTION).mock(return_value=httpx.Response(200, text=FIXTURE["motion"]))
        with pytest.raises(AdapterError):
            await get_adapter("dahua").fetch("device", CONFIG, {}, _ctx(cache))
        data = await get_adapter("dahua").fetch("device", CONFIG, {}, _ctx(cache))
    assert route.call_count == 2 and data.primary["value"] == 2


@pytest.mark.asyncio
async def test_refusal_and_wrong_address() -> None:
    with respx.mock() as router:
        router.get(SYSTEM).mock(return_value=httpx.Response(403, text="Authority:check failure."))
        with pytest.raises(AuthFailed, match="may not read"):
            await get_adapter("dahua").test(CONFIG, _ctx())
    with respx.mock() as router:
        router.get(SYSTEM).mock(return_value=httpx.Response(200, text="<html>hello</html>"))
        with pytest.raises(AdapterError) as error:
            await get_adapter("dahua").test(CONFIG, _ctx())
    assert error.value.code == "not_dahua"
    with respx.mock() as router:
        router.get(SYSTEM).mock(return_value=httpx.Response(404))
        with pytest.raises(AdapterError) as error:
            await get_adapter("dahua").test(CONFIG, _ctx())
    assert error.value.code == "http_error"


def test_demo_cards() -> None:
    for kind in ("device", "disks"):
        assert get_adapter("dahua").demo(kind, {}, 10).status == "ok"
