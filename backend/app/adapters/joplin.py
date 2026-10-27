"""Joplin Server: when your notes last synced, how many items it holds, and for
an administrator every account with its storage.

Written from Joplin Server's source, server-v3.7.3 (packages/server/src:
routes/api/sessions.ts, users.ts, items.ts, ping.ts; models/SessionModel.ts,
UserModel.ts, ItemModel.ts, utils/pagination.ts). Not yet run against a
Joplin Server: it ships only as a container image, and this machine runs none.

⚠️ Joplin Server keeps the notes as sync items, often end-to-end encrypted.
The card reads what the server itself knows about them, never their content:
no titles, no to-dos.

⚠️ There is no API token. ``POST /api/sessions`` takes the e-mail and password
and answers ``{"id", "user_id"}``; the ``id`` goes in ``X-API-AUTH``. A session
lasts 12 hours, after which every call is 403 "Your session has expired", so a
403 signs in once more before it counts as a refusal. Every refusal is 403:
a wrong password, a missing session and a call only an administrator may make.

⚠️ An account with two-factor sign-in cannot sign in over the API at all: the
route passes no code, and the server answers 403 "Invalid authentication
code". Such an account needs a second one, without it, for the dashboard.

⚠️ Sign-ins are rate limited per address (``limiterLoginBruteForce``), which
answers 429; the session is kept so the card signs in about twice a day.

⚠️ ``GET /api/users/{id}`` gives a normal account only its name, e-mail and
account type, not its storage. ``GET /api/users`` gives every column of every
account, password hash included, and only to an administrator: the card reads
the name, e-mail, storage, quota and whether it is enabled, and nothing else
of it leaves this module.

⚠️ ``/api/items/root/children`` pages 100 at a time, newest change first, with
``has_more`` and a ``cursor``; it has no total. The card counts at most
``MAX_PAGES`` pages and says "1000+" past that.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

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
    human_bytes,
    percent,
    percent_text,
)

TOKEN_KEY = "joplin:session"
#: Pages of 100 items counted at most.
MAX_PAGES = 10
PAGE = 100
FULL_PERCENT = 90.0


def _seconds(value: Any) -> float | None:
    """Joplin Server writes times in milliseconds since the epoch."""
    try:
        return float(value) / 1000 if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class JoplinAdapter(Adapter):
    kind = "joplin"
    label = "Joplin Server"
    category = "other"
    description = "Joplin Server: when the notes last synced and how many items it holds; for an administrator, every account with its storage."
    icon = "joplin"
    beta = True
    docs_url = "https://github.com/laurent22/joplin/blob/dev/packages/server/README.md"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://joplin.example.com"),
        Field("email", "E-mail", required=True),
        Field("password", "Password", type="password", secret=True, required=True,
              help="An account without two-factor sign-in: Joplin Server allows none over its API. An administrator also sees every account."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="sync", label="Sync", description="When the notes last changed on the server, and how many items it holds.",
                   renderer="value", default_size=(3, 2), refresh_seconds=300, metrics=("items",)),
        WidgetType(kind="users", label="Accounts", description="Every account with its storage and quota, the fullest first. Needs an administrator.",
                   renderer="list", default_size=(3, 3), refresh_seconds=900,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    # -- signing in ----------------------------------------------------------

    async def _sign_in(self, config: dict[str, Any], ctx: Context) -> str:
        response = await ctx.request("POST", f"{base_url(config)}/api/sessions",
                                     json_body={"email": str(config.get("email") or "").strip(), "password": config.get("password") or ""},
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.status_code == 429:
            raise AdapterError("Joplin Server turned away too many sign-ins from this address for now.", code="rate_limited",
                               hint="It lets the address try again after a while.")
        if response.status_code in (400, 401, 403):
            if "authentication code" in response.text:
                raise AuthFailed("This account signs in with a second factor, which Joplin Server allows none of over its API.",
                                 hint="Make an account without two-factor sign-in for the dashboard.")
            raise AuthFailed("Joplin Server refused the e-mail or password.", hint="Check them; it is the account's own e-mail.")
        if response.status_code >= 400:
            raise AdapterError(f"Joplin Server answered the sign-in with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Joplin Server's own address, without /api.")
        try:
            answer = response.json()
        except ValueError:
            answer = None
        session = str(answer.get("id") or "") if isinstance(answer, dict) else ""
        if not session:
            raise AdapterError("This address answers, but not the way Joplin Server does.", code="not_joplin",
                               hint="Check the URL; it is Joplin Server's own address, without /api.")
        ctx.cache[TOKEN_KEY] = (session, str(answer.get("user_id") or ""))
        return session

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 60, admin: bool = False) -> Any:
        kept = ctx.cache.get(TOKEN_KEY)
        session = kept[0] if kept else await self._sign_in(config, ctx)
        for attempt in (0, 1):
            response = await ctx.request("GET", f"{base_url(config)}/api{path}", headers={"X-API-AUTH": session},
                                         verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
            if response.status_code != 403:
                break
            if attempt == 0 and kept:
                # Twelve hours on, the session is gone: sign in again, once.
                ctx.cache.pop(TOKEN_KEY, None)
                session = await self._sign_in(config, ctx)
                kept = None
                continue
            if admin:
                raise AuthFailed("Only an administrator of Joplin Server sees the accounts.",
                                 hint="Use an administrator's account for this card, or the Sync card.")
            raise AuthFailed("Joplin Server refused the session.", hint="Check the e-mail and password.")
        if response.status_code >= 400:
            raise AdapterError(f"Joplin Server answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Joplin Server's own address, without /api.")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way Joplin Server does.", code="not_joplin",
                               hint="Check the URL; it is Joplin Server's own address, without /api.") from error

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        ctx.cache.pop(TOKEN_KEY, None)
        await self._sign_in(config, ctx)
        user_id = ctx.cache[TOKEN_KEY][1]
        me = await self._get(config, ctx, f"/users/{quote(user_id, safe='')}", cache=0) if user_id else {}
        who = str((me or {}).get("full_name") or (me or {}).get("email") or config.get("email") or "").strip()
        return f"Joplin Server signs in {who}." if who else "Joplin Server signs in."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "users":
            answer = await self._get(config, ctx, "/users", cache=300, admin=True)
            users = answer.get("items") if isinstance(answer, dict) else None
            if not isinstance(users, list):
                raise AdapterError("This address answers, but not the way Joplin Server does.", code="not_joplin")
            return self._users(users, max(1, int(options.get("limit") or 10)))
        newest: Any = None
        count = 0
        cursor = ""
        more = False
        for _page in range(MAX_PAGES):
            query = f"?cursor={quote(cursor, safe='')}" if cursor else f"?limit={PAGE}"
            answer = await self._get(config, ctx, f"/items/root/children{query}", cache=240)
            items = answer.get("items") if isinstance(answer, dict) else None
            if not isinstance(items, list):
                raise AdapterError("This address answers, but not the way Joplin Server does.", code="not_joplin")
            if newest is None and items:
                newest = items[0].get("updated_time")
            count += len(items)
            more = bool(answer.get("has_more")) and bool(answer.get("cursor"))
            if not more:
                break
            cursor = str(answer.get("cursor"))
        return self._sync(count, more, _seconds(newest))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _sync(count: int, more: bool, newest: float | None) -> WidgetData:
        when = ago(newest)
        return WidgetData(
            status="ok" if newest is not None else "unknown",
            primary={"label": "Last change", "value": f"{when} ago" if when else "Never"},
            secondary=[{"label": "Items", "value": f"{count}+" if more else count}],
            metrics={"items": float(count)},
        )

    @staticmethod
    def _users(users: list[Any], limit: int) -> WidgetData:
        rows = []
        for user in users:
            if not isinstance(user, dict):
                continue
            used = float(user.get("total_item_size") or 0)
            quota = float(user.get("max_total_item_size") or 0)
            share = percent(used, quota) if quota > 0 else None
            enabled = user.get("enabled") not in (0, False)
            state = "unknown" if not enabled else "warn" if share is not None and share >= FULL_PERCENT else "ok"
            rows.append({
                "title": str(user.get("full_name") or user.get("email") or "?"),
                "subtitle": " · ".join(part for part in (
                    str(user.get("email") or "") if user.get("full_name") else "",
                    f"{human_bytes(used)} of {human_bytes(quota)}" if quota > 0 else human_bytes(used),
                    "" if enabled else "disabled",
                ) if part),
                "status": state,
                "value": percent_text(share) if share is not None else human_bytes(used),
                **({"progress": share} if share is not None else {}),
                "_used": used,
            })
        rows.sort(key=lambda row: -row["_used"])
        items = [{key: value for key, value in row.items() if key != "_used"} for row in rows[:limit]]
        total = sum(row["_used"] for row in rows)
        return WidgetData(status="warn" if any(item["status"] == "warn" for item in items) else "ok", items=items,
                          secondary=[{"label": "Accounts", "value": len(rows)}, {"label": "Storage", "value": human_bytes(total)}],
                          meta={"empty": "Joplin Server has no account."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "users":
            gb = 1024**3
            return self._users([
                {"full_name": "Alex", "email": "alex@example.com", "total_item_size": int(fake.walk("joplin-alex", tick, 1.5, 1.9) * gb), "max_total_item_size": 2 * gb, "enabled": 1},
                {"full_name": "Sam", "email": "sam@example.com", "total_item_size": int(0.4 * gb), "max_total_item_size": 2 * gb, "enabled": 1},
                {"full_name": "", "email": "guest@example.com", "total_item_size": 12_000_000, "max_total_item_size": 0, "enabled": 0},
            ], max(1, int(options.get("limit") or 10)))
        return self._sync(1240 + tick % 7, False, time.time() - fake.walk("joplin-last", tick, 60, 900))


ADAPTER = JoplinAdapter()
