"""Seafile: the space your account uses of its quota, and your libraries with
their size and last change.

Written from Seahub's source, v14.0.9-server (seahub/api2/views.py:
ObtainAuthToken, AccountInfo, Repos; serializers.py, authentication.py).
Not yet run against a Seafile, which runs here only as a container image.

⚠️ The token goes as ``Authorization: Token <key>``, not Bearer. It is made
by ``POST /api2/auth-token/`` with the user name and password as a form,
and does not run out, so the card asks for one once and keeps it. A wrong
password is 400 with ``non_field_errors``, not 401. An account with
two-factor sign-in answers the same 400 with the header ``X-Seafile-OTP:
required``; the card says to paste a token instead, made in Seafile's
settings under Web API Auth Token.

⚠️ ``total`` in ``/api2/account/info/`` is the quota in bytes, and a
negative one (-2) means no limit; ``usage`` is what the account's own
libraries take.

⚠️ ``/api2/repos/?type=mine`` gives the libraries the account owns, newest
change first, with ``mtime`` in seconds, ``size`` in bytes and who changed
it last. Virtual libraries and wikis are left out by the server.
"""

from __future__ import annotations

import time
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
    human_bytes,
    percent,
    percent_text,
    status_from_percent,
)

TOKEN_KEY = "seafile:token"


class SeafileAdapter(Adapter):
    kind = "seafile"
    label = "Seafile"
    category = "nas"
    description = "Seafile: the space your account uses of its quota, and your libraries with their size and last change."
    icon = "seafile"
    beta = True
    docs_url = "https://manual.seafile.com/latest/develop/web_api_v2.1/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://seafile.example.com"),
        Field("token", "API token", secret=True,
              help="In Seafile: Settings, then Web API Auth Token, Generate. Or leave it empty and sign in below; an account with two-factor sign-in needs the token."),
        Field("username", "E-mail or user name", help="Only without a token."),
        Field("password", "Password", type="password", secret=True, help="Only without a token."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="space", label="Storage", description="How much of the account's quota its libraries take.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("used_percent",)),
        WidgetType(kind="libraries", label="Libraries", description="The account's libraries, the most recently changed first, with their size.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    async def _token(self, config: dict[str, Any], ctx: Context) -> str:
        given = str(config.get("token") or "").strip()
        if given:
            return given
        kept = ctx.cache.get(TOKEN_KEY)
        if kept:
            return str(kept)
        if not config.get("username") or not config.get("password"):
            raise AuthFailed("Seafile needs a token, or a user name and password.", hint="Paste a token, or fill in the sign-in.")
        response = await ctx.request("POST", f"{base_url(config)}/api2/auth-token/",
                                     data={"username": str(config.get("username")).strip(), "password": config.get("password")},
                                     verify=not config.get("insecure"), auth_errors=False)
        if response.headers.get("x-seafile-otp", "").lower() == "required":
            raise AuthFailed("This account signs in with a second factor.",
                             hint="Paste a token instead: in Seafile, Settings, then Web API Auth Token, Generate.")
        if response.status_code in (400, 401, 403):
            raise AuthFailed("Seafile refused the user name or password.", hint="Check them, or paste a token instead.")
        if response.status_code >= 400:
            raise AdapterError(f"Seafile answered the sign-in with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Seafile's own address.")
        try:
            token = str((response.json() or {}).get("token") or "")
        except (ValueError, AttributeError):
            token = ""
        if not token:
            raise AdapterError("This address answers, but not the way Seafile does.", code="not_seafile",
                               hint="Check the URL; it is Seafile's own address.")
        ctx.cache[TOKEN_KEY] = token
        return token

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 120) -> Any:
        signed_in = not str(config.get("token") or "").strip()
        for attempt in (0, 1):
            token = await self._token(config, ctx)
            response = await ctx.request("GET", f"{base_url(config)}/api2{path}", headers={"Authorization": f"Token {token}"},
                                         verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
            if response.status_code not in (401, 403):
                break
            if signed_in and attempt == 0:
                # A token from an earlier sign-in that was deleted since: sign in once more.
                ctx.cache.pop(TOKEN_KEY, None)
                continue
            raise AuthFailed("Seafile refused the token." if not signed_in else "Seafile refused the sign-in's token.",
                             hint="Paste a new token: in Seafile, Settings, then Web API Auth Token, Generate." if not signed_in
                             else "Check the user name and password.")
        if response.status_code >= 400:
            raise AdapterError(f"Seafile answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Seafile's own address.")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way Seafile does.", code="not_seafile",
                               hint="Check the URL; it is Seafile's own address.") from error

    async def _account(self, config: dict[str, Any], ctx: Context, cache: float = 120) -> dict[str, Any]:
        info = await self._get(config, ctx, "/account/info/", cache=cache)
        if not isinstance(info, dict) or "usage" not in info:
            raise AdapterError("This address answers, but not the way Seafile does.", code="not_seafile",
                               hint="Check the URL; it is Seafile's own address.")
        return info

    async def _libraries(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/repos/?type=mine")
        if not isinstance(answer, list):
            raise AdapterError("This address answers, but not the way Seafile does.", code="not_seafile")
        return [one for one in answer if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        info = await self._account(config, ctx, cache=0)
        who = str(info.get("name") or info.get("email") or "").strip()
        return f"Seafile answers for {who}." if who else "Seafile answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        libraries = await self._libraries(config, ctx)
        if widget_kind == "libraries":
            return self._list(libraries, max(1, int(options.get("limit") or 10)))
        return self._space(await self._account(config, ctx), len(libraries))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _space(info: dict[str, Any], libraries: int) -> WidgetData:
        used = float(info.get("usage") or 0)
        total = float(info.get("total") or 0)
        secondary: list[dict[str, Any]] = [{"label": "Used", "value": human_bytes(used)}]
        if total > 0:
            share = percent(used, total)
            secondary.append({"label": "Quota", "value": human_bytes(total)})
            secondary.append({"label": "Libraries", "value": libraries})
            return WidgetData(status=status_from_percent(share, 85, 95), primary={"label": "Used", "value": percent_text(share)},
                              secondary=secondary, metrics={"used_percent": share} if share is not None else {})
        secondary = [{"label": "Quota", "value": "No limit"}, {"label": "Libraries", "value": libraries}]
        return WidgetData(status="ok", primary={"label": "Used", "value": human_bytes(used)}, secondary=secondary)

    @staticmethod
    def _list(libraries: list[dict[str, Any]], limit: int) -> WidgetData:
        ordered = sorted(libraries, key=lambda one: -float(one.get("mtime") or 0))
        items = []
        for one in ordered[:limit]:
            when = ago(one.get("mtime"))
            items.append({
                "title": str(one.get("name") or "?"),
                "subtitle": " · ".join(part for part in (str(one.get("modifier_name") or ""), f"{when} ago" if when else "",
                                                         "encrypted" if one.get("encrypted") else "") if part),
                "status": "ok",
                "value": human_bytes(one.get("size") or 0),
            })
        return WidgetData(status="ok", items=items, meta={"empty": "This account owns no library."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = time.time()
        gb = 1024**3
        libraries = [
            {"name": "Photos", "size": int(fake.walk("seafile-photos", tick, 61, 63) * gb), "mtime": now - 600, "modifier_name": "Alex"},
            {"name": "Documents", "size": 4 * gb, "mtime": now - 3 * 3600, "modifier_name": "Sam"},
            {"name": "Tax returns", "size": 900 * 1024**2, "mtime": now - 9 * 86400, "modifier_name": "Alex", "encrypted": True},
        ]
        if widget_kind == "libraries":
            return self._list(libraries, max(1, int(options.get("limit") or 10)))
        return self._space({"usage": sum(one["size"] for one in libraries), "total": 100 * gb}, len(libraries))


ADAPTER = SeafileAdapter()
