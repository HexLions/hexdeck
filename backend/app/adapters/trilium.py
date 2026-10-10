"""Trilium Notes: how big the knowledge base is, and the notes changed last.

Trilium's ETAPI, read-only, with an ETAPI token from Options > ETAPI in the
``Authorization`` header as it is, with no "Bearer" in front:
``/etapi/metrics?format=json`` for the counts and the database size,
``/etapi/app-info`` for the version, ``/etapi/notes`` for a search.

⚠️ The metrics came in May 2025 (TriliumNext 0.94); an older Trilium answers
404 there, and the overview then shows only the version.

⚠️ The note count is every note in the database, Trilium's own help and
launchers included, about five hundred of them on a new install.

⚠️ A search needs a query: "*" ignores the order asked for, so the latest
notes are found with ``note.title != ''``, which matches them all. A query
of one's own, like ``#todo`` or ``note.dateCreated >= TODAY-7``, replaces
it. A query Trilium cannot fully read is not an error: "note.title =" lists
every note.

⚠️ The ETAPI token opens everything the password does, writing included;
the adapter only reads. Protected notes stay encrypted to it.

Checked against Trilium Notes 0.106.0 running locally on 2026-10-10, verified
by its digest, on a new document with notes of its own added.
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
    human_bytes,
    measured,
)

EVERY_NOTE = "note.title != ''"
METRICS_SECONDS = 600
NOTES_SECONDS = 120


class TriliumAdapter(Adapter):
    kind = "trilium"
    label = "Trilium Notes"
    category = "other"
    description = "Trilium Notes: how many notes, revisions and attachments it holds, and the notes changed last or found by a search."
    icon = "trilium"
    docs_url = "https://docs.triliumnotes.org/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://trilium:8080"),
        Field("token", "ETAPI token", type="password", secret=True, required=True,
              help="A token made under Options > ETAPI in Trilium."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="overview", label="Overview", description="How many notes, revisions and attachments there are, and how big the database is.",
                   renderer="value", default_size=(3, 2), refresh_seconds=900, metrics=("notes",)),
        WidgetType(kind="notes", label="Notes", description="The notes changed last, or the ones a search of your own finds, linking to each.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("search", "Search", placeholder="#todo",
                                  help="A Trilium search, like #todo or note.dateCreated >= TODAY-7. Empty lists the notes changed last."),
                            Field("limit", "Entries", type="number", default=8))),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float,
                   params: dict[str, Any] | None = None, *, missing_ok: bool = False) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/etapi{path}", verify=not config.get("insecure"), params=params,
                                     headers={"Authorization": str(config.get("token") or "").strip(), "Accept": "application/json"},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Trilium refused the ETAPI token.", hint="A token made under Options > ETAPI in Trilium, not the password.")
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code >= 400:
            raise AdapterError(f"Trilium answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of Trilium's server, without /etapi.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Trilium did not answer with JSON.", code="not_json",
                               hint="The address of Trilium's server, without /etapi.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        info = await self._get(config, ctx, "/app-info", 0) or {}
        return f"Trilium {info.get('appVersion') or '?'} answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "notes":
            limit = max(1, int(options.get("limit") or 8))
            query = str(options.get("search") or "").strip()
            params = {"search": query or EVERY_NOTE, "orderBy": "dateModified", "orderDirection": "desc", "limit": limit}
            answer = await self._get(config, ctx, "/notes", NOTES_SECONDS, params)
            notes = [one for one in ((answer or {}).get("results") or []) if isinstance(one, dict)]
            return self._notes(notes, base_url(config), bool(query), limit)
        metrics = await self._get(config, ctx, "/metrics", METRICS_SECONDS, {"format": "json"}, missing_ok=True)
        if metrics is None:
            info = await self._get(config, ctx, "/app-info", METRICS_SECONDS) or {}
            return WidgetData(status="ok", primary={"label": "Version", "value": str(info.get("appVersion") or "?")},
                              meta={"notice": "The counts need Trilium 0.94 or newer."})
        return self._overview(metrics)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _overview(metrics: dict[str, Any]) -> WidgetData:
        database = metrics.get("database") or {}
        statistics = metrics.get("statistics") or {}
        notes = int(database.get("activeNotes") or 0)
        secondary: list[dict[str, Any]] = [
            {"label": "Revisions", "value": int(database.get("totalRevisions") or 0)},
            {"label": "Attachments", "value": int(database.get("activeAttachments") or 0)},
        ]
        if isinstance(statistics.get("databaseSizeBytes"), (int, float)):
            secondary.append({"label": "Database", "value": human_bytes(statistics["databaseSizeBytes"])})
        version = (metrics.get("version") or {}).get("app")
        if version:
            secondary.append({"label": "Version", "value": str(version)})
        return WidgetData(status="ok", primary={"label": "Notes", "value": notes, "metric": "notes"},
                          secondary=secondary, metrics=measured({"notes": float(notes)}))

    @staticmethod
    def _notes(notes: list[dict[str, Any]], base: str, searched: bool, limit: int) -> WidgetData:
        rows = []
        for note in notes:
            if str(note.get("noteId") or "").startswith("_") or note.get("noteId") == "root":
                continue  # Trilium's own hidden notes and the root
            rows.append({
                "id": note.get("noteId"),
                "title": str(note.get("title") or "?"),
                "subtitle": " · ".join(part for part in (str(note.get("type") or ""), ago(note.get("utcDateModified"))) if part),
                "value": "protected" if note.get("isProtected") else "",
                "status": "ok",
                "url": f"{base}/#root/{note.get('noteId')}" if base else None,
            })
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "No note found" if searched else "No note yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "notes":
            titles = ["Homelab plan", "Reading list", "Meeting notes", "Recipes", "Trip to Lisbon", "Backup checklist"]
            first = fake.counter("trilium-edit", tick, 0, 1 / 120) % len(titles)
            notes = [{"noteId": f"n{index}", "title": titles[(first + index) % len(titles)], "type": "text", "utcDateModified": ""}
                     for index in range(len(titles))]
            return self._notes(notes, "", bool(options.get("search")), max(1, int(options.get("limit") or 8)))
        return self._overview({"database": {"activeNotes": 2481, "totalRevisions": 6120, "activeAttachments": 312},
                               "statistics": {"databaseSizeBytes": 184_000_000}, "version": {"app": "0.106.0"}})


ADAPTER = TriliumAdapter()
