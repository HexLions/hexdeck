"""Homebox: how many things are in the inventory, what they are worth, and whose warranty runs out.

Measured against Homebox v0.26.2 (ghcr.io/sysadminsmedia/homebox) on
11.09.2026, with six invented items in one location: three with a warranty
(one ending in 20 days, one next spring, one ended ten days ago), one with a
lifetime warranty, one without, and four batteries at 5.00 each.

⚠️ An API key from Profile > API Keys works as ``Authorization: Bearer <key>``
and without the word Bearer as well. The two refusals mean different things, and
which one came back is the whole diagnosis, so the card repeats it:

* "authorization header or query is required" means no header arrived. The key is
  not the problem; something between HexDeck and Homebox dropped the header.
* "valid authorization token is required" means the header arrived and the key is
  not one Homebox knows. Read from its source (v0.26.2,
  ``app/api/middleware.go`` and ``internal/data/repo/repo_api_keys.go``), a key is
  looked up as ``HMAC-SHA256(pepper, key)`` and refused when it has expired or
  when the hash is not in the table. Since rotating
  ``HBOX_AUTH_API_KEY_PEPPER`` changes every hash, a stack rebuilt with a new
  pepper invalidates every key ever issued, including the one in your hand.

⚠️ A key made by Homebox is ``hb_`` and 43 more characters. Anything else in that
field is a session token at best, and a truncated paste at worst; the card says so
rather than leaving somebody to count characters.

⚠️ API keys exist from Homebox 0.26 onwards and not before: ``/users/self/api-keys``
is not a route on 0.25 and older, so whatever is put in the key field there is refused
with 401. Those installations sign in with an account instead (``POST /users/login``,
which answers a token that already carries the word Bearer), and the token is kept
until shortly before it runs out.

⚠️ The items were renamed to entities in 0.26, and so was their export:
``/entities/export`` from 0.26, ``/items/export`` before it. Whichever answers is
remembered, because a 404 there is a version and not a wrong address, and "check the
URL" would send somebody after the wrong thing entirely.

⚠️ Items are "entities" in this version, and the summary the list hands out
has no warranty at all. The CSV export has it for every item in one request,
together with the location and a link of the item's own (``/item/<id>``).

⚠️ ``totalItemPrice`` in the statistics multiplies by the quantity: the four
batteries counted 20.00 there. The ``totalPrice`` of the entity list covers
only the page it came with and ignores the quantity, so the card takes the
statistics. ``totalWithWarranty`` counts a warranty that already ended and
not a lifetime one.

⚠️ ``/v1/currency`` is in the API documentation and answers 404. The currency
is on ``/v1/groups``.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
)

#: How long an ended warranty stays on the card.
ENDED_DAYS = 30
#: What every key Homebox issues begins with, and 43 characters follow it.
KEY_PREFIX = "hb_"
#: Where the CSV of every item lives, by the name this version gives it.
EXPORTS = ("/entities/export", "/items/export")
#: How long a sign-in token is kept. ``stayLoggedIn`` buys a week; stopping short of
#: it costs one request and never uses a token that has just run out.
SIGN_IN_SECONDS = 6 * 86400


def _said(response: Any) -> str:
    """Homebox's own words for a refusal, which name the cause precisely."""
    try:
        answer = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(answer, dict):
        for key in ("error", "message", "detail"):
            if answer.get(key):
                return " ".join(str(answer[key]).split())[:120]
    return f"HTTP {response.status_code}"


def _warranty_rows(text: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if "HB.warranty_expires" not in (reader.fieldnames or []):
        raise AdapterError("This address answers, but not the way Homebox does.", code="not_homebox",
                           hint="The export of the items has no warranty column.")
    return [row for row in reader if str(row.get("HB.url") or "").startswith("/item/")]


class HomeboxAdapter(Adapter):
    kind = "homebox"
    label = "Homebox"
    category = "other"
    description = "How many things are in the inventory, what they are worth, and which warranty runs out."
    icon = "homebox"
    #: Confirmed against a live instance on 2026-09-11.
    beta = False
    docs_url = "https://homebox.software/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://homebox:7745"),
        Field("api_key", "API key", type="password", secret=True,
              help="Profile > API Keys > Create API Key, on Homebox 0.26 and later. Homebox shows the key only once."),
        Field("username", "User name", placeholder="you@example.com",
              help="Instead of a key, and the only way on Homebox 0.25 and older, which has no API keys."),
        Field("password", "Password", type="password", secret=True,
              help="Signs in as that account. The token is kept until shortly before it runs out."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="inventory", label="Inventory", description="How many items there are, what they are worth together and how many locations.",
                   renderer="value", default_size=(3, 2), refresh_seconds=900, metrics=("items",)),
        WidgetType(kind="warranties", label="Warranties", description="The warranties that end within the chosen days, and those that ended in the last 30.",
                   renderer="list", default_size=(3, 3), refresh_seconds=3600,
                   options=(Field("days", "Days ahead", type="number", default=60),
                            Field("limit", "Entries", type="number", default=8))),
    )

    @staticmethod
    def _why(config: dict[str, Any], response: Any) -> str:
        """What to look at, decided by what Homebox said and what was given."""
        said = _said(response).lower()
        if "header" in said:
            return ("The header never reached Homebox. A proxy in front of it is dropping Authorization; "
                    "point the connection at Homebox directly to tell the two apart.")
        key = str(config.get("api_key") or "").strip()
        if key and not key.startswith(KEY_PREFIX) and not key.lower().startswith("bearer "):
            return (f"A key made by Homebox reads {KEY_PREFIX} and 43 more characters. This one does not, "
                    "so it is probably not the key, or not all of it.")
        return ("The key is not one this Homebox knows. Either it has expired, or it was issued before "
                "HBOX_AUTH_API_KEY_PEPPER was changed, which invalidates every key at once. Make a new key "
                "under Profile > API Keys. Before 0.26 there are no API keys at all: use a user name and "
                "password instead.")

    @staticmethod
    def _key_header(config: dict[str, Any]) -> str:
        """An API key as the header Homebox wants it.

        ⚠️ A key pasted with the word Bearer in front of it is not given a second
        one; two of them are a 401 and nothing says why.
        """
        key = str(config.get("api_key") or "").strip()
        return key if key.lower().startswith("bearer ") else f"Bearer {key}"

    async def _sign_in(self, config: dict[str, Any], ctx: Context) -> str:
        """The token of an account, for a Homebox that has no API keys."""
        response = await ctx.request(
            "POST", f"{base_url(config)}/api/v1/users/login",
            # ⚠️ Homebox picks the decoder by Content-Type and refuses what it does
            # not recognise, so the body has to be JSON.
            json_body={"username": str(config.get("username") or ""),
                       "password": str(config.get("password") or ""), "stayLoggedIn": True},
            verify=not config.get("insecure"), auth_errors=False,
        )
        if response.status_code in (401, 403):
            raise AuthFailed("Homebox refused this user name and password.",
                             hint="A Homebox with local sign-in switched off cannot be used this way; "
                                  "on 0.26 and later use an API key instead.")
        if response.status_code >= 400:
            raise AdapterError(f"Homebox answered with HTTP {response.status_code} to the sign-in.", code="http_error")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("The sign-in did not answer with JSON.", code="not_json",
                               hint="The URL probably points at something else than Homebox.") from failure
        # ⚠️ The answer already reads "Bearer <token>". Prefixing it again is a 401.
        token = str((answer or {}).get("token") or "")
        if not token:
            raise AuthFailed("Homebox answered the sign-in without a token.")
        return token

    async def _authorization(self, config: dict[str, Any], ctx: Context) -> str:
        """Whichever of the two ways in this connection was given."""
        if config.get("api_key"):
            return self._key_header(config)
        if not config.get("username"):
            raise AdapterError("This connection has no way in.", code="no_credentials",
                               hint="An API key on Homebox 0.26 and later, or a user name and password on any version.")
        kept = ctx.cache.get("homebox:token")
        if kept and kept[0] > time.monotonic():
            return str(kept[1])
        token = await self._sign_in(config, ctx)
        ctx.cache["homebox:token"] = (time.monotonic() + SIGN_IN_SECONDS, token)
        return token

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 60,
                   again: bool = True) -> Any:
        response = await ctx.request(
            "GET", f"{base_url(config)}/api/v1{path}",
            headers={"Authorization": await self._authorization(config, ctx)},
            verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False,
        )
        if response.status_code in (401, 403):
            if config.get("api_key"):
                raise AuthFailed(f"Homebox rejected the API key: {_said(response)}",
                                 hint=self._why(config, response))
            # A token that has run out looks exactly like a wrong one, so the only
            # way to tell them apart is to sign in again and try once more.
            if again:
                ctx.cache.pop("homebox:token", None)
                # ⚠️ Without cache=0 the retry reads the 401 back out of the
                # response cache, because a fresh token can be the same string.
                return await self._get(config, ctx, path, 0, again=False)
            raise AuthFailed(f"Homebox rejected these credentials: {_said(response)}",
                             hint="The user name is the e-mail address the account signs in with. A Homebox with "
                                  "local sign-in switched off refuses this way in altogether.")
        if response.status_code >= 400:
            raise AdapterError(f"Homebox answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is the address of Homebox itself, without /api.")
        return response

    async def _export(self, config: dict[str, Any], ctx: Context) -> Any:
        """The CSV of every item, from the address this version keeps it at."""
        known = str(ctx.cache.get("homebox:export") or "")
        for path in ([known] if known in EXPORTS else []) + [one for one in EXPORTS if one != known]:
            try:
                answer = await self._get(config, ctx, path, cache=600)
            except AdapterError as failure:
                if failure.code != "http_error" or "404" not in str(failure):
                    raise
                continue
            ctx.cache["homebox:export"] = path
            return answer
        raise AdapterError("This Homebox offers no export of its items.", code="no_export",
                           hint="The warranty card reads the CSV export, at /entities/export from 0.26 "
                                "and /items/export before it.")

    async def _json(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 60) -> dict[str, Any]:
        response = await self._get(config, ctx, path, cache)
        try:
            answer = response.json()
        except ValueError as error:
            raise AdapterError("Homebox did not answer with JSON.", code="not_json",
                               hint="The URL probably points at something else than Homebox.") from error
        if not isinstance(answer, dict):
            raise AdapterError("This address answers, but not the way Homebox does.", code="not_homebox")
        return answer

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        status = await self._json(config, ctx, "/status", cache=0)
        statistics = await self._json(config, ctx, "/groups/statistics", cache=0)
        version = (status.get("build") or {}).get("version") if isinstance(status.get("build"), dict) else None
        return f"Homebox {version or '?'} answers with {int(statistics.get('totalItems') or 0)} items."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "warranties":
            export = await self._export(config, ctx)
            return self._warranties(_warranty_rows(export.text), datetime.now(UTC).date(), base_url(config), options)
        statistics = await self._json(config, ctx, "/groups/statistics")
        group = await self._json(config, ctx, "/groups", cache=3600)
        return self._inventory(statistics, str(group.get("currency") or ""))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _inventory(statistics: dict[str, Any], currency: str) -> WidgetData:
        items = int(statistics.get("totalItems") or 0)
        try:
            worth = f"{float(statistics.get('totalItemPrice') or 0):,.2f} {currency}".strip()
        except (TypeError, ValueError):
            worth = ""
        return WidgetData(
            status="ok",
            primary={"label": "Things", "value": items},
            secondary=[{"label": "Total value", "value": worth}, {"label": "Locations", "value": int(statistics.get("totalLocations") or 0)}],
            metrics={"items": float(items)},
        )

    @staticmethod
    def _warranties(rows: list[dict[str, str]], today: date, base: str, options: dict[str, Any]) -> WidgetData:
        ahead = max(1, int(options.get("days") or 60))
        ending: list[tuple[date, dict[str, Any]]] = []
        soon = 0
        for row in rows:
            try:
                ends = date.fromisoformat(str(row.get("HB.warranty_expires") or "")[:10])
            except ValueError:
                continue
            left = (ends - today).days
            if left > ahead or left < -ENDED_DAYS:
                continue
            place = str(row.get("HB.location") or "")
            item: dict[str, Any] = {
                "title": str(row.get("HB.name") or "?"),
                "subtitle": " · ".join(part for part in ("Expired" if left < 0 else "", place, ends.isoformat()) if part),
                "status": "bad" if left < 0 else "warn" if left <= 30 else "ok",
                "value": f"{left} d" if left >= 0 else "",
            }
            if str(row.get("HB.url") or "").startswith("/item/"):
                item["url"] = f"{base}{row['HB.url']}"
            if left >= 0:
                soon += 1
            ending.append((ends, item))
        ending.sort(key=lambda pair: pair[0])
        return WidgetData(
            status="bad" if any(item["status"] == "bad" for _ends, item in ending) else "ok",
            items=[item for _ends, item in ending][: int(options.get("limit") or 8)],
            secondary=[{"label": "Ending soon", "value": soon}],
            meta={"empty": "No warranty ends in this time."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        today = datetime.now(UTC).date()
        if widget_kind == "warranties":
            rows = [
                {"HB.name": "Cordless Drill", "HB.location": "Garage", "HB.url": "/item/demo-drill", "HB.warranty_expires": (today + timedelta(days=20)).isoformat()},
                {"HB.name": "Coffee Machine", "HB.location": "Kitchen", "HB.url": "/item/demo-coffee", "HB.warranty_expires": (today + timedelta(days=48)).isoformat()},
                {"HB.name": "Wifi Router", "HB.location": "Office", "HB.url": "/item/demo-router", "HB.warranty_expires": (today - timedelta(days=10)).isoformat()},
            ]
            return self._warranties(rows, today, "https://homebox.example.com", options)
        return self._inventory({"totalItems": 214 + tick % 3, "totalItemPrice": 18_430.5, "totalLocations": 9}, "EUR")


ADAPTER = HomeboxAdapter()
