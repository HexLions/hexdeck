"""Alertmanager: what is firing, what is silenced, and the configuration that must not leak.

The answers below follow Alertmanager's own OpenAPI spec (api/v2/openapi.yaml)
at v0.34.1, and were checked against that release running locally on
2026-10-07 with four alerts and a silence posted to it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

AM = "http://alertmanager:9093/api/v2"
CONFIG = {"url": "http://alertmanager:9093"}


def _stamp(minutes: float) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def _alert(fingerprint: str, name: str, severity: str | None, state: str = "active", minutes: float = -10, **labels: str) -> dict:
    tags = {"alertname": name, **({"severity": severity} if severity else {}), **labels}
    return {"fingerprint": fingerprint, "labels": tags, "annotations": {"summary": f"{name} said so"},
            "receivers": [{"name": "ops"}], "startsAt": _stamp(minutes), "updatedAt": _stamp(0), "endsAt": _stamp(60),
            "generatorURL": "http://prometheus:9090/graph", "status": {"state": state, "silencedBy": [], "inhibitedBy": [], "mutedBy": []}}


FIRING = [
    _alert("a", "HostHighCpuLoad", "warning", instance="pve:9100"),
    _alert("b", "HostOutOfDiskSpace", "critical", instance="nas:9100", minutes=-40),
    _alert("c", "NoSeverity", None, minutes=-30),
]
SILENCED = [_alert("d", "ContainerRestarting", "warning", state="suppressed")]
SILENCES = [
    {"id": "s1", "status": {"state": "active"}, "updatedAt": _stamp(0), "createdBy": "alex", "comment": "migration",
     "startsAt": _stamp(-10), "endsAt": _stamp(170), "matchers": [{"name": "alertname", "value": "ContainerRestarting", "isRegex": False, "isEqual": True}]},
    {"id": "s2", "status": {"state": "pending"}, "updatedAt": _stamp(0), "createdBy": "sam", "comment": "move",
     "startsAt": _stamp(600), "endsAt": _stamp(720), "matchers": [{"name": "instance", "value": "pi.*", "isRegex": True, "isEqual": False}]},
    {"id": "s3", "status": {"state": "expired"}, "updatedAt": _stamp(0), "createdBy": "noa", "comment": "old",
     "startsAt": _stamp(-900), "endsAt": _stamp(-800), "matchers": [{"name": "job", "value": "x", "isRegex": False, "isEqual": True}]},
]
STATUS = {"cluster": {"status": "disabled", "peers": []},
          "versionInfo": {"version": "0.34.1", "revision": "x", "branch": "HEAD", "buildUser": "x", "buildDate": "x", "goVersion": "go1.26"},
          "config": {"original": "receivers:\n- name: ops\n  webhook_configs:\n  - url: http://hook?token=FAKE-SECRET\n"},
          "uptime": _stamp(-60)}


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _alertmanager() -> None:
    def alerts(request: httpx.Request) -> httpx.Response:
        silenced = request.url.params.get("silenced") == "true"
        return httpx.Response(200, json=FIRING + (SILENCED if silenced else []))

    respx.get(f"{AM}/alerts").mock(side_effect=alerts)
    respx.get(f"{AM}/silences").mock(return_value=httpx.Response(200, json=SILENCES))
    respx.get(f"{AM}/status").mock(return_value=httpx.Response(200, json=STATUS))


@respx.mock
async def test_the_configuration_with_its_secrets_never_reaches_a_card() -> None:
    """⚠️ /status carries the whole configuration, webhook tokens and all."""
    _alertmanager()
    adapter = get_adapter("alertmanager")
    data = await adapter.fetch("summary", CONFIG, {}, _ctx())
    said = await adapter.test(CONFIG, _ctx())
    assert "FAKE-SECRET" not in json.dumps(data.__dict__, default=str) + said


@respx.mock
async def test_the_list_puts_the_critical_alert_first_and_ranks_an_unlabelled_one_as_a_warning() -> None:
    _alertmanager()
    data = await get_adapter("alertmanager").fetch("alerts", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["HostOutOfDiskSpace", "HostHighCpuLoad", "NoSeverity"]
    assert [row["status"] for row in data.items] == ["bad", "warn", "warn"]
    assert data.items[0]["subtitle"].startswith("nas:9100 · HostOutOfDiskSpace said so")
    assert data.status == "bad" and data.primary["value"] == 3


@respx.mock
async def test_silenced_alerts_are_left_out_unless_asked_for() -> None:
    _alertmanager()
    adapter = get_adapter("alertmanager")
    await adapter.fetch("alerts", CONFIG, {}, _ctx())
    asked = respx.calls[-1].request.url.params
    assert asked["silenced"] == "false" and asked["inhibited"] == "false"
    data = await adapter.fetch("alerts", CONFIG, {"show_silenced": True}, _ctx())
    silenced = next(row for row in data.items if row["title"] == "ContainerRestarting")
    assert silenced["value"] == "silenced" and silenced["status"] == "unknown"
    assert data.primary["value"] == 3, "a silenced alert is shown, not counted as firing"


@respx.mock
async def test_a_receiver_is_passed_on() -> None:
    _alertmanager()
    await get_adapter("alertmanager").fetch("alerts", CONFIG, {"receiver": "team-ops"}, _ctx())
    assert respx.calls[-1].request.url.params["receiver"] == "team-ops"


@respx.mock
async def test_the_summary_counts_firing_critical_and_silenced_apart() -> None:
    _alertmanager()
    data = await get_adapter("alertmanager").fetch("summary", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Firing", "value": 3}
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels == {"Critical": 1, "Silenced": 1, "Version": "0.34.1"}
    assert data.status == "bad"
    assert data.metrics == {"firing": 3.0, "silenced": 1.0}


@respx.mock
async def test_nothing_firing_is_green() -> None:
    respx.get(f"{AM}/alerts").mock(return_value=httpx.Response(200, json=[]))
    data = await get_adapter("alertmanager").fetch("alerts", CONFIG, {}, _ctx())
    assert data.status == "ok" and data.items == [] and data.meta["empty"] == "Nothing is firing"


@respx.mock
async def test_the_silences_show_what_is_on_with_their_matchers_as_alertmanager_writes_them() -> None:
    _alertmanager()
    data = await get_adapter("alertmanager").fetch("silences", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ['alertname="ContainerRestarting"', 'instance!~"pi.*"']
    assert [row["value"] for row in data.items] == ["active", "pending"]
    assert "ends in" in data.items[0]["subtitle"] and "starts in" in data.items[1]["subtitle"]
    assert data.primary == {"label": "Active", "value": 1}


@respx.mock
async def test_basic_authentication_is_sent_only_when_a_user_is_named() -> None:
    _alertmanager()
    adapter = get_adapter("alertmanager")
    await adapter.fetch("alerts", CONFIG, {}, _ctx())
    assert "authorization" not in respx.calls[-1].request.headers
    await adapter.fetch("alerts", {**CONFIG, "username": "am", "password": "pw"}, {}, _ctx())
    assert respx.calls[-1].request.headers["authorization"].startswith("Basic ")


@respx.mock
async def test_a_refused_sign_in_says_where_basic_auth_comes_from() -> None:
    respx.get(f"{AM}/alerts").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthFailed):
        await get_adapter("alertmanager").fetch("alerts", CONFIG, {}, _ctx())


@respx.mock
async def test_an_address_without_the_api_is_said_plainly() -> None:
    respx.get(f"{AM}/alerts").mock(return_value=httpx.Response(404))
    with pytest.raises(AdapterError) as failure:
        await get_adapter("alertmanager").fetch("alerts", CONFIG, {}, _ctx())
    assert failure.value.code == "no_such_path"


@respx.mock
async def test_the_connection_test_names_the_version_and_what_fires() -> None:
    _alertmanager()
    said = await get_adapter("alertmanager").test(CONFIG, _ctx())
    assert said == "Alertmanager 0.34.1 answers with 3 firing alerts."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("alertmanager")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
