"""Boards from other dashboards: Homepage and Homarr.

The importer reads what the other dashboard's files say, turns it into a
plan, and the plan into a board. The plan is shown first: every connection
it would make, with what is still missing; every card, with a tick to leave
it out; and what could not be carried over, said in words. Nothing in the
files is ever read as an environment reference: a pasted file is data.

A service the other dashboard only linked to becomes an app tile. A service
it read through a widget becomes a connection and a card, when HexDeck has
an adapter for it; otherwise a tile and a warning.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...adapters import all_adapters, get_adapter
from ...adapters.base import Adapter, WidgetType
from ...models import Board, Integration, User
from ..boards import ImportError_, import_board
from ..integrations import store_config, validate_required


class DashboardImportError(Exception):
    pass


#: Which of an adapter's widgets stands for the service when the other
#: dashboard showed "the" widget of it: the summary-like one, else the first.
SUMMARY_KINDS = ("summary", "status", "counts", "latest", "system", "nowplaying", "overview", "library", "queue", "monitors")

#: Where a HexDeck connection field may take its value from, in order of
#: preference, among the names the other dashboards use.
FIELD_SOURCES: dict[str, tuple[str, ...]] = {
    "url": ("url",),
    "host": ("url", "host"),
    "api_key": ("key", "apikey", "apiKey", "api_key", "token"),
    "token": ("token", "key", "apiKey", "apikey"),
    "app_token": ("key", "token"),
    "password": ("password", "key"),
    "username": ("username", "user"),
    "token_id": ("username", "token_id"),
    "token_secret": ("password", "key", "token_secret"),
    "email": ("username", "email"),
}


def adapter_for(kind: str | None) -> Adapter | None:
    if not kind:
        return None
    try:
        return get_adapter(kind)
    except KeyError:
        return None


def summary_widget(adapter: Adapter) -> WidgetType | None:
    """The widget that stands for the service, if one needs no option filled in by hand."""
    ranked = sorted(adapter.widgets, key=lambda w: SUMMARY_KINDS.index(w.kind) if w.kind in SUMMARY_KINDS else len(SUMMARY_KINDS))
    for widget in ranked:
        if not any(field.required and field.default is None for field in widget.options):
            return widget
    return None


def config_from(adapter: Adapter, given: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A connection's settings out of what the other dashboard had, and the required ones still missing."""
    config: dict[str, Any] = {}
    for field in adapter.fields:
        for source in FIELD_SOURCES.get(field.name, (field.name,)):
            value = given.get(source)
            if isinstance(value, (str, int, float)) and str(value).strip() != "":
                config[field.name] = str(value).strip() if isinstance(value, str) else value
                break
    return config, validate_required(adapter.kind, config)


def icon_name(icon: Any) -> str:
    """A service icon as HexDeck names it: the dashboard-icons name without its
    extension, a URL as it is, and the other dashboards' prefixed symbol sets
    reduced to the name behind the prefix."""
    text = str(icon or "").strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://", "data:")):
        return text
    # A path inside the other dashboard ("/imgs/app-icons/sonarr.png") is
    # not reachable from here; its file name usually is a service name.
    text = text.rsplit("/", 1)[-1]
    text = re.sub(r"\.(png|svg|webp|jpg|jpeg)$", "", text, flags=re.I)
    text = re.sub(r"^(si|mdi|sh)-", "", text)
    return text


def guess_adapter_by_name(name: str) -> Adapter | None:
    """A tile called "Sonarr" is Sonarr, for the icon and nothing else."""
    wanted = re.sub(r"[^a-z0-9]", "", name.lower())
    for adapter in all_adapters():
        if re.sub(r"[^a-z0-9]", "", adapter.label.lower()) == wanted or adapter.kind == wanted:
            return adapter
    return None


class Plan:
    """What an import would make, built up page by page."""

    def __init__(self, source: str, name: str) -> None:
        self.source = source
        self.name = name
        self.connections: list[dict[str, Any]] = []
        self.pages: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self._keys = 0

    def _key(self, prefix: str) -> str:
        self._keys += 1
        return f"{prefix}{self._keys}"

    def page(self, name: str) -> dict[str, Any]:
        found = next((p for p in self.pages if p["name"] == name), None)
        if found is None:
            found = {"name": name, "cards": []}
            self.pages.append(found)
        return found

    def connection(self, adapter: Adapter, name: str, given: dict[str, Any]) -> str:
        """A connection for the plan; the same service twice is one connection."""
        config, missing = config_from(adapter, given)
        for existing in self.connections:
            if existing["kind"] == adapter.kind and existing["config"].get("url") and existing["config"].get("url") == config.get("url"):
                return existing["key"]
        key = self._key("c")
        self.connections.append({"key": key, "kind": adapter.kind, "label": adapter.label, "icon": adapter.icon, "name": name, "config": config, "missing": missing})
        return key

    def card(self, page: str, kind: str, title: str, *, icon: str = "", link: str = "", options: dict[str, Any] | None = None,
             connection: str | None = None, layout: dict[str, int] | None = None) -> dict[str, Any]:
        adapter = adapter_for(kind.split(".", 1)[0])
        widget_kind = kind.split(".", 1)[-1]
        label = next((w.label for w in adapter.widgets if w.kind == widget_kind), adapter.label) if adapter else kind
        card = {"key": self._key("k"), "kind": kind, "label": label, "title": title, "icon": icon, "link": link, "options": options or {},
                "connection": connection, "layout": layout, "enabled": True}
        self.page(page)["cards"].append(card)
        return card

    def tile(self, page: str, title: str, href: str, icon: str, description: str = "", layout: dict[str, int] | None = None) -> dict[str, Any]:
        options = {"description": description} if description else {}
        return self.card(page, "core.app", title, icon=icon, link=href, options=options, layout=layout)

    def service_card(self, page: str, adapter: Adapter, title: str, given: dict[str, Any], *, layout: dict[str, int] | None = None) -> dict[str, Any] | None:
        """A card on a connection for a service the other dashboard read; None when no widget of the adapter works unasked."""
        widget = summary_widget(adapter)
        if widget is None:
            self.warnings.append(f"{title}: every {adapter.label} card needs a setting filled in by hand, so only a tile was made.")
            return None
        key = self.connection(adapter, title, given)
        return self.card(page, f"{adapter.kind}.{widget.kind}", title, icon=adapter.icon, connection=key, layout=layout)

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "name": self.name, "connections": self.connections, "pages": self.pages, "warnings": self.warnings}


def detect(files: dict[str, str]) -> str:
    """Which dashboard the pasted files come from."""
    for text in files.values():
        stripped = text.lstrip()
        if stripped.startswith("{"):
            try:
                document = yaml.safe_load(stripped)
            except yaml.YAMLError:
                continue
            if isinstance(document, dict) and ("apps" in document or "widgets" in document) and "schemaVersion" in document:
                return "homarr"
        elif stripped.startswith("-"):
            return "homepage"
    raise DashboardImportError("This does not look like a Homepage YAML file or a Homarr config.")


def plan_for(source: str, files: dict[str, str]) -> dict[str, Any]:
    from . import homarr, homepage

    if source == "auto":
        source = detect(files)
    if source == "homepage":
        return homepage.plan(files).as_dict()
    if source == "homarr":
        return homarr.plan(files).as_dict()
    raise DashboardImportError(f"There is no importer for {source!r}.")


def apply(db: Session, plan: dict[str, Any], *, user: User) -> Board:
    """Make the connections and the board a plan describes.

    Connections are made here, with the values as they stand in the plan, and
    then the board goes through the ordinary import as an untrusted document:
    that path reads no environment and creates nothing on its own.
    """
    pages = [p for p in plan.get("pages") or [] if any(c.get("enabled", True) for c in p.get("cards") or [])]
    if not pages:
        raise DashboardImportError("Nothing is left to import; every card was left out.")
    used = {c.get("connection") for p in pages for c in p["cards"] if c.get("enabled", True) and c.get("connection")}
    names: dict[str, str] = {}
    for entry in plan.get("connections") or []:
        if entry["key"] not in used:
            continue
        adapter = adapter_for(entry.get("kind"))
        if adapter is None:
            raise DashboardImportError(f"There is no adapter called {entry.get('kind')!r}.")
        config = {k: v for k, v in (entry.get("config") or {}).items() if isinstance(v, (str, int, float, bool))}
        # Values are data. A "${VAR}" in a pasted file stays the text "${VAR}".
        name = str(entry.get("name") or adapter.label).strip()[:120] or adapter.label
        existing = db.scalar(select(Integration).where(Integration.name == name, Integration.kind == adapter.kind))
        if existing is None:
            missing = validate_required(adapter.kind, config)
            existing = Integration(kind=adapter.kind, name=name, config=store_config(adapter.kind, config), enabled=not missing, created_by=user.id)
            db.add(existing)
            db.flush()
        names[entry["key"]] = existing.name
    document: dict[str, Any] = {
        "nexdeck": 1,
        "board": {"name": str(plan.get("name") or "Imported board")[:80], "icon": "layout-dashboard", "settings": {"columns": 12}},
        "integrations": [{"name": names[key], "kind": next(e["kind"] for e in plan["connections"] if e["key"] == key)} for key in names],
        "pages": [],
    }
    for page in pages:
        widgets = []
        for card in page["cards"]:
            if not card.get("enabled", True):
                continue
            if card.get("connection") and card["connection"] not in names:
                continue
            widget: dict[str, Any] = {"kind": card["kind"], "title": str(card.get("title") or "")[:120], "icon": str(card.get("icon") or "")[:200],
                                      "link": str(card.get("link") or "")[:600], "options": dict(card.get("options") or {})}
            if card.get("connection"):
                widget["integration"] = names[card["connection"]]
            layout = card.get("layout")
            if isinstance(layout, dict) and all(isinstance(layout.get(k), int) for k in ("x", "y", "w", "h")):
                widget["layout"] = {"lg": {k: layout[k] for k in ("x", "y", "w", "h")}}
            widgets.append(widget)
        document["pages"].append({"name": str(page.get("name") or "Page")[:80], "widgets": widgets})
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    try:
        return import_board(db, text, owner_id=user.id, trusted=False, allow_locked=True)
    except ImportError_ as failure:
        raise DashboardImportError(str(failure)) from failure
