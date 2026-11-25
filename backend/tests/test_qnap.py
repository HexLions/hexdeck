"""QNAP: the sign-in, a session that runs out, and the three cards.

The pages under ``fixtures/qnap`` are written anew in the shape qnapstats 0.6.2 reads them; no NAS was at hand.
Which words besides OK a disk's ``Health`` can hold is not known from that source, so anything else is shown as a
problem with the word itself.
"""

from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context
from app.adapters.qnap import SESSION_KEY, parse

BASE = "http://nas.local:8080"
CGI = f"{BASE}/cgi-bin"
CONFIG = {"url": BASE, "username": "dashboard", "password": "made-up pässword"}
PAGES = Path(__file__).parent / "fixtures" / "qnap"


def _page(name: str) -> httpx.Response:
    return httpx.Response(200, text=(PAGES / f"{name}.xml").read_text(), headers={"Content-Type": "text/xml"})


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


@respx.mock
async def test_the_sign_in_sends_the_password_in_base64_and_never_in_the_address() -> None:
    login = respx.post(f"{CGI}/authLogin.cgi").mock(return_value=_page("login"))
    system = respx.get(f"{CGI}/management/manaRequest.cgi").mock(return_value=_page("sysinfo"))
    assert await get_adapter("qnap").test(CONFIG, _ctx()) == "TS-464 with QTS 5.2.7 answers."
    form = parse_qs(login.calls[0].request.content.decode())
    assert form["user"] == ["dashboard"]
    assert base64.b64decode(form["pwd"][0]).decode() == "made-up pässword"
    asked = system.calls[0].request.url
    assert asked.params["sid"] == "made-up-session" and asked.params["subfunc"] == "sysinfo"
    assert "pässword" not in str(asked) and form["pwd"][0] not in str(asked)


@respx.mock
async def test_a_wrong_password() -> None:
    respx.post(f"{CGI}/authLogin.cgi").mock(return_value=_page("refused"))
    with pytest.raises(AuthFailed):
        await get_adapter("qnap").test(CONFIG, _ctx())


@respx.mock
async def test_a_session_that_ran_out_signs_in_once_more() -> None:
    login = respx.post(f"{CGI}/authLogin.cgi").mock(return_value=_page("login"))
    respx.get(f"{CGI}/management/manaRequest.cgi").mock(side_effect=[_page("refused"), _page("sysinfo")])
    ctx = _ctx()
    ctx.cache[SESSION_KEY] = "old-session"
    data = await get_adapter("qnap").fetch("system", CONFIG, {}, ctx)
    assert data.primary == {"label": "Processor", "value": 12, "unit": "%"}
    assert login.call_count == 1 and ctx.cache[SESSION_KEY] == "made-up-session"


@respx.mock
async def test_the_system_card() -> None:
    respx.post(f"{CGI}/authLogin.cgi").mock(return_value=_page("login"))
    respx.get(f"{CGI}/management/manaRequest.cgi").mock(return_value=_page("sysinfo"))
    data = await get_adapter("qnap").fetch("system", CONFIG, {}, _ctx())
    values = {row["label"]: row["value"] for row in data.secondary}
    assert values == {"Memory": 42, "Temperature": 48, "Uptime": "19d 4h", "Model": "TS-464"}
    assert data.status == "ok" and data.metrics["cpu"] == 12.4


@respx.mock
async def test_the_volumes_leave_the_reserved_one_out() -> None:
    respx.post(f"{CGI}/authLogin.cgi").mock(return_value=_page("login"))
    respx.get(f"{CGI}/management/chartReq.cgi").mock(return_value=_page("volumes"))
    data = await get_adapter("qnap").fetch("volumes", CONFIG, {}, _ctx())
    assert [(item["title"], item["value"], item["status"]) for item in data.items] == [("DataVol1", "65%", "ok"), ("Backups", "96%", "bad")]
    assert data.status == "bad"


@respx.mock
async def test_the_disks_and_an_empty_slot() -> None:
    respx.post(f"{CGI}/authLogin.cgi").mock(return_value=_page("login"))
    respx.get(f"{CGI}/disk/qsmart.cgi").mock(return_value=_page("disks"))
    data = await get_adapter("qnap").fetch("disks", CONFIG, {}, _ctx())
    assert [(item["title"], item["value"], item["status"], item["hint"]) for item in data.items] == [
        ("Disk 1", "38 °C", "ok", "OK"), ("Disk 2", "41 °C", "bad", "Abnormal")]
    assert "WD-0000000000" not in str(data.items), "no serial numbers on a card"


@pytest.mark.parametrize("text", [
    "<html><body>Login</body></html>",
    "not xml at all",
    '<?xml version="1.0"?><!DOCTYPE QDocRoot [<!ENTITY a "aaaa">]><QDocRoot>&a;</QDocRoot>',
])
def test_what_is_not_a_qts_page_is_refused(text: str) -> None:
    with pytest.raises(AdapterError) as refused:
        parse(text)
    assert refused.value.code == "not_qnap"


@pytest.mark.parametrize("kind", ["system", "volumes", "disks"])
def test_every_card_has_a_demo(kind: str) -> None:
    data = get_adapter("qnap").demo(kind, {}, 3)
    assert data.primary or data.items
