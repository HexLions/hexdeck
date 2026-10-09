"""Mastodon: the server, the account, the notifications, and a week that is not over.

fixtures/mastodon_social.json holds mastodon.social's /api/v2/instance and the
first four rows of /api/v1/instance/activity as they were on 2026-10-09,
trimmed to the fields the cards read. The account and notification answers
follow the Account and Notification entities in Mastodon's documentation.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AuthFailed, Context

FIXTURES = Path(__file__).parent / "fixtures"
SOCIAL = json.loads((FIXTURES / "mastodon_social.json").read_text())
SERVER = "https://social.example.com"
CONFIG = {"url": SERVER, "token": "made-up-token"}
ACCOUNT = {"id": "1", "acct": "hex", "display_name": "Hex", "followers_count": 412, "following_count": 188,
           "statuses_count": 1324, "url": f"{SERVER}/@hex"}
NOTIFICATIONS = [
    {"id": "3", "type": "mention", "created_at": "2026-10-09T10:00:00.000Z",
     "account": {"acct": "alex@example.org", "display_name": "Alex", "url": "https://example.org/@alex"},
     "status": {"content": "<p><span class=\"h-card\">@hex</span> which <b>UniFi</b> version?</p>", "url": "https://example.org/@alex/1"}},
    {"id": "2", "type": "follow_request", "created_at": "2026-10-09T09:00:00.000Z",
     "account": {"acct": "noa", "display_name": "", "url": f"{SERVER}/@noa"}},
    {"id": "1", "type": "added_to_collection", "created_at": "2026-10-09T08:00:00.000Z",
     "account": {"acct": "sam", "display_name": "Sam", "url": f"{SERVER}/@sam"}},
]


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _mastodon(activity: httpx.Response | None = None, unread: httpx.Response | None = None) -> None:
    respx.get(f"{SERVER}/api/v2/instance").mock(return_value=httpx.Response(200, json=SOCIAL["instance"]))
    respx.get(f"{SERVER}/api/v1/instance/activity").mock(return_value=activity or httpx.Response(200, json=SOCIAL["activity"]))
    respx.get(f"{SERVER}/api/v1/accounts/verify_credentials").mock(return_value=httpx.Response(200, json=ACCOUNT))
    respx.get(f"{SERVER}/api/v1/notifications/unread_count").mock(return_value=unread or httpx.Response(200, json={"count": 5}))
    respx.get(f"{SERVER}/api/v1/notifications").mock(return_value=httpx.Response(200, json=NOTIFICATIONS))


@respx.mock
async def test_the_server_card_reads_this_week_as_the_week_in_progress() -> None:
    """⚠️ The first row of the activity is the week not yet over, and its counts are strings."""
    _mastodon()
    data = await get_adapter("mastodon").fetch("instance", CONFIG, {}, _ctx())
    labels = {row["label"]: row["value"] for row in data.secondary}
    assert labels["Active this month"] == 268223
    assert labels["Posts this week"] == 86305 and labels["Logins this week"] == 38911
    assert labels["Sign-ups"] == "open" and labels["Version"] == "4.8.0-nightly.2026-10-06"
    assert data.primary == {"label": "mastodon.social", "value": "Mastodon"}


@respx.mock
async def test_the_server_card_asks_without_the_token() -> None:
    """The server's own facts are public; the token is not sent where it is not needed."""
    _mastodon()
    await get_adapter("mastodon").fetch("instance", CONFIG, {}, _ctx())
    assert all("authorization" not in call.request.headers for call in respx.calls)


@respx.mock
async def test_a_server_without_activity_shows_the_rest() -> None:
    _mastodon(activity=httpx.Response(404, json={"error": "Record not found"}))
    data = await get_adapter("mastodon").fetch("instance", CONFIG, {}, _ctx())
    assert "Posts this week" not in {row["label"] for row in data.secondary}
    assert data.metrics == {"active": 268223.0}


@respx.mock
async def test_the_account_card_counts_followers_and_unread() -> None:
    _mastodon()
    data = await get_adapter("mastodon").fetch("account", CONFIG, {}, _ctx())
    assert data.primary == {"label": "Followers", "value": 412}
    assert {row["label"]: row["value"] for row in data.secondary} == {"Unread": 5, "Following": 188, "Posts": 1324}
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-token"


@respx.mock
async def test_a_server_before_4_3_leaves_unread_out_rather_than_saying_none() -> None:
    _mastodon(unread=httpx.Response(404, json={"error": "Record not found"}))
    data = await get_adapter("mastodon").fetch("account", CONFIG, {}, _ctx())
    assert "Unread" not in {row["label"] for row in data.secondary}


@respx.mock
async def test_notifications_say_who_did_what_in_plain_text() -> None:
    _mastodon()
    data = await get_adapter("mastodon").fetch("notifications", CONFIG, {}, _ctx())
    assert [row["title"] for row in data.items] == ["Alex mentioned you", "noa wants to follow you", "Sam added_to_collection"]
    assert data.items[0]["subtitle"].startswith("@alex@example.org · @hex which UniFi version?")
    assert [row["status"] for row in data.items] == ["ok", "warn", "ok"]


@respx.mock
async def test_mentions_only_asks_for_mentions() -> None:
    _mastodon()
    await get_adapter("mastodon").fetch("notifications", CONFIG, {"mentions_only": True}, _ctx())
    assert respx.calls[0].request.url.params.get_list("types[]") == ["mention"]


@respx.mock
async def test_a_card_that_needs_a_token_says_so_when_there_is_none() -> None:
    respx.get(f"{SERVER}/api/v1/accounts/verify_credentials").mock(return_value=httpx.Response(401, json={"error": "The access token is invalid"}))
    with pytest.raises(AuthFailed) as failure:
        await get_adapter("mastodon").fetch("account", {"url": SERVER}, {}, _ctx())
    assert failure.value.message == "This card needs an access token."


@respx.mock
async def test_the_connection_test_names_the_server_and_the_account() -> None:
    _mastodon()
    assert await get_adapter("mastodon").test(CONFIG, _ctx()) == "Mastodon 4.8.0-nightly.2026-10-06 answers, signed in as @hex."


def test_the_demo_fills_every_card() -> None:
    adapter = get_adapter("mastodon")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 100)
        assert data.items or data.secondary, widget.kind
