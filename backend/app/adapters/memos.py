"""Memos: the latest notes, and the ones with tasks still open.

API v1, read-only: ``/api/v1/memos`` with a CEL filter and an order, and
``/api/v1/instance/profile`` for the version. A personal access token goes in
as ``Authorization: Bearer``.

⚠️ The instance profile answers anyone, a wrong token included, so the
connection test asks for a memo, which an instance refuses without a valid
token (401, "authentication required").

⚠️ The filter's fields are not the memo's: ``has_incomplete_tasks`` in the
filter, ``property.hasIncompleteTasks`` in the answer. Asked the other way,
the filter is refused as an undeclared reference.

⚠️ A user is ``users/<username>`` in this version, not ``users/<id>``.

Read from Memos' OpenAPI spec (proto/gen/openapi.yaml) at v0.31.0 and checked
against that release running locally on 2026-10-09.
"""

from __future__ import annotations

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

MEMOS_SECONDS = 120


def _tag_filter(tag: str) -> str:
    """A tag as a CEL term. Quotes in it would end the string, so they go."""
    clean = tag.strip().lstrip("#").replace('"', "").replace("\\", "")
    return f'"{clean}" in tags' if clean else ""


class MemosAdapter(Adapter):
    kind = "memos"
    label = "Memos"
    category = "other"
    description = "Your latest memos with their tags, and the ones with tasks still to tick."
    icon = "memos"
    docs_url = "https://usememos.com/docs"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://memos:5230"),
        Field("token", "Access token", type="password", secret=True, required=True,
              help="A personal access token from Settings > My account > Access tokens."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="memos", label="Memos", description="The latest memos, pinned ones first, with their tags.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("tag", "Tag", placeholder="homelab", help="Only the memos with this tag. Empty means all of them."),
                            Field("limit", "Entries", type="number", default=8))),
        WidgetType(kind="tasks", label="Open tasks", description="The memos with a task list that still has something unticked.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300, metrics=("open",),
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None) -> Any:
        token = str(config.get("token") or "").strip()
        response = await ctx.request("GET", f"{base_url(config)}/api/v1{path}", params=params,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                                     verify=not config.get("insecure"), cache_seconds=MEMOS_SECONDS, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Memos refused the access token.",
                             hint="A personal access token from Settings > My account > Access tokens. One that has expired is refused the same way.")
        if response.status_code >= 400:
            said = ""
            try:
                said = str(response.json().get("message") or "")
            except (ValueError, AttributeError):
                pass
            raise AdapterError(f"Memos answered with HTTP {response.status_code}{': ' + said if said else ''}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Memos did not answer with JSON.", code="not_json",
                               hint="The address of Memos itself, the one its web page opens on.") from failure

    async def _memos(self, config: dict[str, Any], ctx: Context, *, limit: int, filter_: str = "", order: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"pageSize": max(1, min(100, limit))}
        if filter_:
            params["filter"] = filter_
        if order:
            params["orderBy"] = order
        answer = await self._get(config, ctx, "/memos", params)
        return [one for one in ((answer or {}).get("memos") or []) if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        # The profile answers anyone; a memo needs the token.
        await self._memos(config, ctx, limit=1)
        profile = await self._get(config, ctx, "/instance/profile")
        version = (profile or {}).get("version") if isinstance(profile, dict) else ""
        return f"Memos{' ' + str(version) if version else ''} answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        limit = max(1, int(options.get("limit") or 8))
        if widget_kind == "tasks":
            return self._tasks(await self._memos(config, ctx, limit=100, filter_="has_incomplete_tasks"), limit)
        tag = str(options.get("tag") or "")
        found = await self._memos(config, ctx, limit=limit, filter_=_tag_filter(tag), order="pinned desc, create_time desc")
        return self._list(found, limit, tag)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _row(memo: dict[str, Any]) -> dict[str, Any]:
        prop = memo.get("property") or {}
        title = str(prop.get("title") or "").strip() or str(memo.get("snippet") or memo.get("content") or "").strip()
        tags = [f"#{tag}" for tag in memo.get("tags") or []]
        parts = [" ".join(tags[:4]), ago(memo.get("updateTime") or memo.get("createTime"))]
        return {
            "id": memo.get("name"),
            "title": title[:120] or "?",
            "subtitle": " · ".join(part for part in parts if part),
            "value": "pinned" if memo.get("pinned") else "",
            "status": "ok",
        }

    @classmethod
    def _list(cls, memos: list[dict[str, Any]], limit: int, tag: str) -> WidgetData:
        rows = [cls._row(memo) for memo in memos][:limit]
        return WidgetData(status="ok", items=rows,
                          meta={"empty": f"No memo tagged {tag.strip().lstrip('#')}" if tag.strip() else "No memo yet"})

    @classmethod
    def _tasks(cls, memos: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        for memo in memos:
            row = cls._row(memo)
            # ⚠️ Counted from the Markdown: the answer says that a task is open, not how many.
            content = str(memo.get("content") or "")
            open_ = sum(1 for line in content.splitlines() if line.lstrip().startswith(("- [ ]", "* [ ]", "+ [ ]")))
            row["value"] = f"{open_} open" if open_ else "open"
            row["status"] = "warn"
            rows.append(row)
        return WidgetData(status="ok", items=rows[:limit], primary={"label": "Open", "value": len(rows)},
                          metrics=measured({"open": float(len(rows))}), meta={"empty": "Nothing left to tick"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        done = fake.flicker("memos-ups", tick, 0.3)
        memos = [
            {"name": "memos/a", "pinned": True, "tags": ["homelab"], "content": "# Rack\nReplace the UPS battery\n- [ ] order battery\n- [x] check model",
             "snippet": "Rack Replace the UPS battery", "property": {"title": "Rack", "hasIncompleteTasks": not done}, "updateTime": ""},
            {"name": "memos/b", "pinned": False, "tags": ["ideas", "hexdeck"], "content": "A card that shows the backups of the week",
             "snippet": "A card that shows the backups of the week", "property": {}, "updateTime": ""},
            {"name": "memos/c", "pinned": False, "tags": ["network"], "content": "Move the guest Wi-Fi to its own VLAN\n- [ ] new SSID\n- [ ] firewall rule",
             "snippet": "Move the guest Wi-Fi to its own VLAN", "property": {"hasIncompleteTasks": True}, "updateTime": ""},
            {"name": "memos/d", "pinned": False, "tags": [], "content": "Read the Caddy docs on passive health checks",
             "snippet": "Read the Caddy docs on passive health checks", "property": {}, "updateTime": ""},
        ]
        limit = max(1, int(options.get("limit") or 8))
        if widget_kind == "tasks":
            return self._tasks([one for one in memos if (one.get("property") or {}).get("hasIncompleteTasks")], limit)
        return self._list(memos, limit, str(options.get("tag") or ""))


ADAPTER = MemosAdapter()
