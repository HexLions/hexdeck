"""Jackett: the indexers it serves the *arr apps, and which of them are failing.

Two doors. The Torznab endpoint takes the API key and lists the configured
indexers: ``/api/v2.0/indexers/all/results/torznab/api?t=indexers&configured=true``.
The admin API, ``/api/v2.0/indexers?configured=true``, carries each indexer's
``last_error``, but answers only a browser session: the API key does not open
it, and without a session it redirects to the login page.

⚠️ A wrong API key is not a 401. Torznab answers 200 with
``<error code="100" description="Invalid API Key"/>``, so the body is read
before anything else.

⚠️ The session comes from the login form: a POST of the admin password to
``/UI/Dashboard``. Without an admin password set in Jackett the same page
hands one out, but only at the end of four redirects that first check the
browser keeps cookies (Login, TestCookie, Login?cookiesChecked=1). Either way
the cookie is kept on a client of this connection's own, never on the shared
one.

⚠️ ``last_error`` is the indexer's last failure as Jackett wrote it, such as
"Challenge detected but FlareSolverr is not configured". It stays until the
indexer next succeeds.

Checked against Jackett v0.24.2813 running locally on 2026-10-10, with two
public indexers configured, both failing (one behind Cloudflare without
FlareSolverr, one unreachable), an admin password set and unset.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

import httpx

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    Unreachable,
    WidgetData,
    WidgetType,
    base_url,
    measured,
    outbound_client,
)

TORZNAB = "/api/v2.0/indexers/all/results/torznab/api"


def parse_indexers(xml: str) -> list[dict[str, Any]]:
    """The configured indexers from Torznab's t=indexers answer.

    Raises AuthFailed for Torznab's error 100, which comes with HTTP 200.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as failure:
        raise AdapterError("Jackett did not answer with Torznab's XML.", code="not_xml",
                           hint="The address of Jackett itself, usually on port 9117.") from failure
    if root.tag == "error":
        code, said = root.get("code") or "", root.get("description") or "an error"
        if code == "100":
            raise AuthFailed("Jackett refused the API key.", hint="The API key from the top right of Jackett's dashboard.")
        raise AdapterError(f"Jackett answered with Torznab error {code}: {said}.", code="torznab_error")
    found = []
    for one in root.findall("indexer"):
        found.append({
            "id": one.get("id") or "",
            "title": (one.findtext("title") or one.get("id") or "?").strip(),
            "type": (one.findtext("type") or "").strip(),
            "language": (one.findtext("language") or "").strip(),
        })
    return found


def _reason(error: str, indexer: str) -> str:
    """Jackett's error without its own prefix, "Exception (1337x): "."""
    text = error.strip()
    prefix = f"Exception ({indexer}): "
    if text.startswith(prefix):
        text = text[len(prefix):]
    # It often says the same thing twice: "X: X".
    head, sep, tail = text.partition(": ")
    return head if sep and tail.strip() == head.strip() else text


class JackettAdapter(Adapter):
    kind = "jackett"
    label = "Jackett"
    category = "downloads"
    description = "The indexers Jackett serves to Sonarr, Radarr and the rest, and which of them are failing and why."
    icon = "jackett"
    docs_url = "https://github.com/Jackett/Jackett/wiki"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://jackett:9117"),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="The API key from the top right of Jackett's dashboard."),
        Field("admin_password", "Admin password", type="password", secret=True,
              help="Only to see which indexers fail and why: that lives behind Jackett's login, which the API key does not open. Empty works when Jackett has no admin password."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Overview", description="How many indexers are configured, how many of them private, and how many are failing.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("failing",)),
        WidgetType(kind="indexers", label="Indexers", description="The configured indexers, the failing ones first with their reason.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("failing_only", "Only the failing ones", type="bool", default=False),
                            Field("limit", "Entries", type="number", default=12))),
    )

    def _client(self, config: dict[str, Any], ctx: Context) -> httpx.AsyncClient:
        client = ctx.cache.get("jackett_client")
        if client is None or client.is_closed:
            # One client per connection: it keeps the session cookie of the login.
            client = outbound_client(base_url=base_url(config), verify=not config.get("insecure"), timeout=20, keep_cookies=True)
            ctx.cache["jackett_client"] = client
        return client

    async def _indexers(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        response = await ctx.request("GET", f"{base_url(config)}{TORZNAB}", verify=not config.get("insecure"), cache_seconds=300,
                                     params={"apikey": str(config.get("api_key") or "").strip(), "t": "indexers", "configured": "true"},
                                     auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Jackett refused the API key.", hint="The API key from the top right of Jackett's dashboard.")
        if response.status_code >= 400:
            raise AdapterError(f"Jackett answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of Jackett itself, usually on port 9117.")
        return parse_indexers(response.text)

    async def _errors(self, config: dict[str, Any], ctx: Context, *, retry: bool = True) -> dict[str, str] | None:
        """Each indexer's last error, or None when the admin API stays shut."""
        client = self._client(config, ctx)
        try:
            response = await client.get("/api/v2.0/indexers", params={"configured": "true"}, follow_redirects=False)
            if response.status_code in (301, 302) and retry:
                password = str(config.get("admin_password") or "")
                if password:
                    await client.post("/UI/Dashboard", data={"password": password}, follow_redirects=False)
                else:
                    # ⚠️ Four hops before a session: Dashboard, Login, TestCookie,
                    # Login?cookiesChecked=1, which sets the cookie. Measured.
                    await client.get("/UI/Dashboard", follow_redirects=True)
                return await self._errors(config, ctx, retry=False)
        except httpx.HTTPError as error:
            raise Unreachable(f"Jackett could not be reached: {error.__class__.__name__}.") from error
        if response.status_code in (301, 302):
            if config.get("admin_password"):
                raise AuthFailed("Jackett refused the admin password.",
                                 hint="The password set under Admin password on Jackett's dashboard, not the API key.")
            return None
        if response.status_code >= 400:
            return None
        try:
            listed = response.json()
        except ValueError:
            return None
        return {str(one.get("id")): str(one.get("last_error") or "") for one in listed if isinstance(one, dict)}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        indexers = await self._indexers(config, ctx)
        errors = await self._errors(config, ctx)
        said = "" if errors is not None else "; their failures need the admin password"
        return f"Jackett answers with {len(indexers)} configured indexer{'s' if len(indexers) != 1 else ''}{said}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        indexers = await self._indexers(config, ctx)
        errors = await self._errors(config, ctx)
        if widget_kind == "indexers":
            return self._list(indexers, errors, bool(options.get("failing_only")), max(1, int(options.get("limit") or 12)))
        return self._summary(indexers, errors)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(indexers: list[dict[str, Any]], errors: dict[str, str] | None) -> WidgetData:
        private = sum(1 for one in indexers if one["type"] in ("private", "semi-private"))
        secondary: list[dict[str, Any]] = [{"label": "Private", "value": private}, {"label": "Public", "value": len(indexers) - private}]
        failing = None
        if errors is not None:
            failing = sum(1 for one in indexers if errors.get(one["id"]))
            secondary.insert(0, {"label": "Failing", "value": failing, "metric": "failing"})
        return WidgetData(
            status="warn" if failing else "ok",
            primary={"label": "Indexers", "value": len(indexers)},
            secondary=secondary,
            metrics=measured({"failing": float(failing) if failing is not None else None}),
            meta={"notice": "" if errors is not None else "Which indexers fail needs Jackett's admin password."},
        )

    @staticmethod
    def _list(indexers: list[dict[str, Any]], errors: dict[str, str] | None, failing_only: bool, limit: int) -> WidgetData:
        rows = []
        for one in indexers:
            error = (errors or {}).get(one["id"], "")
            if failing_only and not error:
                continue
            rows.append({
                "id": one["id"],
                "title": one["title"],
                "subtitle": _reason(error, one["id"])[:120] if error else " · ".join(part for part in (one["type"], one["language"]) if part),
                "value": "failing" if error else "",
                "status": "bad" if error else "ok",
            })
        rows.sort(key=lambda row: (row["status"] != "bad", row["title"].lower()))
        return WidgetData(
            status="warn" if any(row["status"] == "bad" for row in rows) else "ok",
            items=rows[:limit],
            meta={"empty": "No indexer is failing" if failing_only else "No indexer configured",
                  "notice": "" if errors is not None else "Which indexers fail needs Jackett's admin password."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        indexers = [
            {"id": "1337x", "title": "1337x", "type": "public", "language": "en-US"},
            {"id": "torrentleech", "title": "TorrentLeech", "type": "private", "language": "en-US"},
            {"id": "nyaasi", "title": "Nyaa.si", "type": "public", "language": "en-US"},
            {"id": "iptorrents", "title": "IPTorrents", "type": "private", "language": "en-US"},
            {"id": "eztv", "title": "EZTV", "type": "public", "language": "en-US"},
        ]
        errors = {"1337x": "Exception (1337x): Challenge detected but FlareSolverr is not configured: Challenge detected but FlareSolverr is not configured"}
        if fake.flicker("jackett-eztv", tick, 0.3):
            errors["eztv"] = "Exception (eztv): The operation has timed out."
        if widget_kind == "indexers":
            return self._list(indexers, errors, bool(options.get("failing_only")), max(1, int(options.get("limit") or 12)))
        return self._summary(indexers, errors)


ADAPTER = JackettAdapter()
