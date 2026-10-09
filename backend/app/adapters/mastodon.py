"""Mastodon: your server, your account, and what happened to you there.

The client API, read-only. ``/api/v2/instance`` and
``/api/v1/instance/activity`` are public; ``/api/v1/accounts/verify_credentials``,
``/api/v1/notifications`` and ``/api/v1/notifications/unread_count`` need a
user token as ``Authorization: Bearer`` with the read scopes.

⚠️ The weekly activity counts the week in progress first, so its first row is
a partial week: on mastodon.social on 2026-10-09 it held 86 thousand posts
against more than a million in each full week. The card calls it "this week".
A server can switch the activity off; then the endpoint answers 404 and the
card goes without it.

⚠️ The numbers in the activity are strings cast from integers.

⚠️ ``unread_count`` came with Mastodon 4.3; an older server answers 404 and the
account card leaves the unread line out rather than claiming none.

Read from Mastodon's documentation (mastodon/documentation: methods/instance,
methods/accounts, methods/notifications and the Instance, Account and
Notification entities) and checked against mastodon.social's public answers
on 2026-10-09.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    ago,
    base_url,
    measured,
)

#: What each notification is, said briefly. Types a newer server adds pass through as they are.
KINDS = {
    "mention": "mentioned you", "status": "posted", "reblog": "boosted", "follow": "followed you",
    "follow_request": "wants to follow you", "favourite": "favourited", "poll": "poll ended", "update": "edited a post",
    "admin.sign_up": "signed up", "admin.report": "filed a report", "quote": "quoted you", "quoted_update": "edited a quote",
    "severed_relationships": "relationships severed", "moderation_warning": "moderation warning",
}
#: Worth a colour: someone waiting on you, or a moderator.
ATTENTION = {"follow_request", "admin.report", "moderation_warning", "severed_relationships"}
INSTANCE_SECONDS = 1800
ACCOUNT_SECONDS = 300


def _text(html: Any) -> str:
    """A status's HTML as one line of plain text."""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(html or "")))).strip()


class MastodonAdapter(Adapter):
    kind = "mastodon"
    label = "Mastodon"
    category = "feeds"
    description = "Your Mastodon server with its active users and this week's posts, your account, and your notifications."
    icon = "mastodon"
    docs_url = "https://docs.joinmastodon.org/client/intro/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://mastodon.social",
              help="The address of the server, yours or the one your account is on."),
        Field("token", "Access token", type="password", secret=True,
              help="For the account and notification cards: Preferences > Development > New application, with read:accounts and read:notifications. The server card needs none."),
    )
    widgets = (
        WidgetType(kind="instance", label="Server", description="The server's version, its active users of the month, whether it takes sign-ups, and the posts and logins of this week.",
                   renderer="stats", default_size=(3, 2), refresh_seconds=3600, metrics=("active", "statuses")),
        WidgetType(kind="account", label="Account", description="Your followers, who you follow, your posts and the notifications you have not read.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("followers", "unread")),
        WidgetType(kind="notifications", label="Notifications", description="The latest notifications: who mentioned, followed, boosted or favourited you, and what.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("mentions_only", "Mentions only", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=8))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: Any = None, cache: float = 0,
                   optional: bool = False, signed: bool = True) -> Any:
        token = str(config.get("token") or "").strip()
        headers = {"Accept": "application/json"}
        if signed and token:
            headers["Authorization"] = f"Bearer {token}"
        response = await ctx.request("GET", f"{base_url(config)}{path}", params=params, headers=headers,
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Mastodon refused the access token." if token else "This card needs an access token.",
                             hint="Preferences > Development > New application, with the scopes read:accounts and read:notifications; "
                                  "the token is the one under Your access token.")
        if response.status_code == 404 and optional:
            return None
        if response.status_code >= 400:
            raise AdapterError(f"Mastodon answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address is the server's own, such as https://mastodon.social.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Mastodon did not answer with JSON.", code="not_json") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        instance = await self._get(config, ctx, "/api/v2/instance", signed=False)
        said = f"{(instance or {}).get('title') or 'Mastodon'} {(instance or {}).get('version') or ''}".strip()
        if str(config.get("token") or "").strip():
            account = await self._get(config, ctx, "/api/v1/accounts/verify_credentials")
            return f"{said} answers, signed in as @{(account or {}).get('acct') or '?'}."
        return f"{said} answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "account":
            account = await self._get(config, ctx, "/api/v1/accounts/verify_credentials", cache=ACCOUNT_SECONDS)
            unread = await self._get(config, ctx, "/api/v1/notifications/unread_count", cache=ACCOUNT_SECONDS, optional=True)
            return self._account(account or {}, unread)
        if widget_kind == "notifications":
            limit = max(1, int(options.get("limit") or 8))
            params: list[tuple[str, Any]] = [("limit", min(40, limit))]
            if options.get("mentions_only"):
                params.append(("types[]", "mention"))
            answer = await self._get(config, ctx, "/api/v1/notifications", params, cache=ACCOUNT_SECONDS)
            return self._notifications(answer if isinstance(answer, list) else [], limit)
        instance = await self._get(config, ctx, "/api/v2/instance", cache=INSTANCE_SECONDS, signed=False)
        activity = await self._get(config, ctx, "/api/v1/instance/activity", cache=INSTANCE_SECONDS, optional=True, signed=False)
        return self._instance(instance or {}, activity if isinstance(activity, list) else None)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _instance(instance: dict[str, Any], activity: list[dict[str, Any]] | None) -> WidgetData:
        active = ((instance.get("usage") or {}).get("users") or {}).get("active_month")
        registrations = instance.get("registrations") or {}
        secondary: list[dict[str, Any]] = []
        if isinstance(active, int):
            secondary.append({"label": "Active this month", "value": active, "metric": "active"})
        week = activity[0] if activity else None
        statuses = None
        if isinstance(week, dict):
            # The first row is the week in progress, and the counts are strings.
            statuses = int(str(week.get("statuses") or 0) or 0)
            secondary.append({"label": "Posts this week", "value": statuses, "metric": "statuses"})
            secondary.append({"label": "Logins this week", "value": int(str(week.get("logins") or 0) or 0)})
        if registrations:
            secondary.append({"label": "Sign-ups", "value": "closed" if not registrations.get("enabled") else
                              "with approval" if registrations.get("approval_required") else "open"})
        if instance.get("version"):
            secondary.append({"label": "Version", "value": str(instance["version"])})
        return WidgetData(
            status="ok",
            primary={"label": str(instance.get("domain") or "Server"), "value": str(instance.get("title") or "")},
            secondary=secondary,
            metrics=measured({"active": float(active) if isinstance(active, int) else None,
                              "statuses": float(statuses) if statuses is not None else None}),
        )

    @staticmethod
    def _account(account: dict[str, Any], unread: Any) -> WidgetData:
        followers = account.get("followers_count")
        count = unread.get("count") if isinstance(unread, dict) else None
        secondary: list[dict[str, Any]] = []
        if isinstance(count, int):
            secondary.append({"label": "Unread", "value": count, "metric": "unread"})
        secondary.append({"label": "Following", "value": account.get("following_count", 0)})
        secondary.append({"label": "Posts", "value": account.get("statuses_count", 0)})
        return WidgetData(
            status="ok",
            primary={"label": "Followers", "value": followers if isinstance(followers, int) else 0},
            secondary=secondary,
            metrics=measured({"followers": float(followers) if isinstance(followers, int) else None,
                              "unread": float(count) if isinstance(count, int) else None}),
            link=str(account.get("url") or "") or None,
        )

    @staticmethod
    def _notifications(notifications: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        for one in notifications:
            if not isinstance(one, dict):
                continue
            kind = str(one.get("type") or "")
            who = one.get("account") or {}
            name = str(who.get("display_name") or who.get("acct") or "?")
            status = one.get("status") or {}
            said = _text(status.get("content"))[:90]
            rows.append({
                "id": one.get("id"),
                "title": f"{name} {KINDS.get(kind, kind)}",
                "subtitle": " · ".join(part for part in (f"@{who.get('acct')}" if who.get("acct") else "", said, ago(one.get("created_at"))) if part),
                "status": "warn" if kind in ATTENTION else "ok",
                "url": status.get("url") or who.get("url"),
            })
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "No notification"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "account":
            return self._account({"followers_count": fake.counter("masto-f", tick, 412, 0.01), "following_count": 188,
                                  "statuses_count": 1324, "url": ""}, {"count": int(fake.walk("masto-u", tick, 0, 7))})
        if widget_kind == "notifications":
            def note(kind: str, name: str, acct: str, html: str = "") -> dict[str, Any]:
                return {"id": f"{kind}{acct}", "type": kind, "created_at": "", "account": {"display_name": name, "acct": acct, "url": ""},
                        "status": {"content": html, "url": ""} if html else None}
            return self._notifications([
                note("mention", "Alex", "alex@example.org", "<p>@you the new map card is lovely, which UniFi version?</p>"),
                note("favourite", "Sam", "sam@example.net", "<p>Moved the rack to 10 GbE this weekend</p>"),
                note("follow_request", "Noa", "noa@example.com"),
                note("reblog", "Homelab News", "news@example.com", "<p>HexDeck 0.23 is out</p>"),
            ], max(1, int(options.get("limit") or 8)))
        return self._instance(
            {"domain": "social.example.com", "title": "Example Social", "version": "4.5.2", "usage": {"users": {"active_month": 1284}},
             "registrations": {"enabled": True, "approval_required": True}},
            [{"week": "0", "statuses": str(int(fake.walk("masto-s", tick, 800, 1400))), "logins": "402", "registrations": "3"}])


ADAPTER = MastodonAdapter()
