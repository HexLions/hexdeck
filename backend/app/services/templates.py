"""Board templates: ready-made boards for the usual corners of a homelab.

A template is a board file (the same YAML the export writes) under
``app/templates/`` with a ``template`` section on top: an id, a name, a
description and tags. Its connections are placeholders. Installing a
template maps each placeholder to one of the connections this installation
has, or leaves it out, and then goes through the ordinary import, with the
rights of whoever asked.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters import get_adapter
from ..models import Board, Integration, User
from .boards import COLUMNS, ImportError_, import_board
from .layout import ALLOWED_COLUMNS

DIRECTORY = Path(__file__).resolve().parent.parent / "templates"


class TemplateError(Exception):
    pass


@lru_cache(maxsize=1)
def _documents() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(DIRECTORY.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        meta = document["template"]
        found[str(meta["id"])] = document
    return found


def list_templates() -> list[dict[str, Any]]:
    return [summary(document) for document in _documents().values()]


def get_template(template_id: str) -> dict[str, Any]:
    try:
        return _documents()[template_id]
    except KeyError as error:
        raise TemplateError("There is no such template.") from error


def summary(document: dict[str, Any]) -> dict[str, Any]:
    meta = document["template"]
    connections = []
    for entry in document.get("integrations") or []:
        adapter = get_adapter(entry["kind"])
        connections.append({"name": entry["name"], "kind": entry["kind"], "label": adapter.label, "icon": adapter.icon})
    cards = sum(len(page.get("widgets") or []) for page in document.get("pages") or [])
    return {
        "id": meta["id"], "name": meta["name"], "description": meta.get("description", ""), "tags": list(meta.get("tags") or []),
        "icon": (document.get("board") or {}).get("icon", "layout-dashboard"), "cards": cards, "pages": len(document.get("pages") or []),
        "integrations": connections,
    }


def _fill_breakpoints(document: dict[str, Any]) -> None:
    """A template lays its cards out for the wide screen only; the narrower
    screens follow: the middle one scaled, the phone stacked in order."""
    columns = int((document.get("board") or {}).get("settings", {}).get("columns") or COLUMNS["lg"])
    if columns not in ALLOWED_COLUMNS:
        raise TemplateError(f"A template is laid out on {sorted(ALLOWED_COLUMNS)} columns, not {columns}.")
    for page in document.get("pages") or []:
        stacked = 0
        for widget in page.get("widgets") or []:
            layout = widget.get("layout") or {}
            wide = layout.get("lg")
            if not wide:
                continue
            factor = COLUMNS["md"] / columns
            layout["md"] = {"x": round(wide["x"] * factor), "y": wide["y"], "w": max(2, round(wide["w"] * factor)), "h": wide["h"]}
            layout["sm"] = {"x": 0, "y": stacked, "w": COLUMNS["sm"], "h": wide["h"]}
            stacked += wide["h"]
            widget["layout"] = layout


def install(db: Session, template_id: str, *, user: User, name: str | None, connections: dict[str, int | None]) -> Board:
    """Make a board from a template, its placeholders mapped to real connections.

    A placeholder mapped to nothing takes its cards with it. A connection has
    to be of the kind the template asked for, and one the user may use.
    """
    document = copy.deepcopy(get_template(template_id))
    wanted = {entry["name"]: entry["kind"] for entry in document.get("integrations") or []}
    unknown = set(connections) - set(wanted)
    if unknown:
        raise TemplateError(f"The template has no connection called {sorted(unknown)[0]!r}.")
    renamed: dict[str, str] = {}
    kept = []
    for placeholder, kind in wanted.items():
        integration_id = connections.get(placeholder)
        if integration_id is None:
            continue
        integration = db.get(Integration, integration_id)
        if integration is None or integration.kind != kind:
            raise TemplateError(f"Connection {integration_id} is not a {get_adapter(kind).label} connection.")
        if integration.admin_only and user.role != "admin":
            raise TemplateError(f"The connection {integration.name!r} is reserved for administrators.")
        renamed[placeholder] = integration.name
        kept.append({"name": integration.name, "kind": kind})
    document["integrations"] = kept
    for page in document.get("pages") or []:
        widgets = []
        for widget in page.get("widgets") or []:
            placeholder = widget.get("integration")
            if placeholder:
                if placeholder not in renamed:
                    continue
                widget["integration"] = renamed[placeholder]
            widgets.append(widget)
        if len(widgets) < len(page.get("widgets") or []):
            # Cards were left out, and the drawn layout has their holes in
            # it. The rest flow into place in their order instead.
            for widget in widgets:
                widget.pop("layout", None)
        page["widgets"] = widgets
    if name:
        document["board"]["name"] = name
    _fill_breakpoints(document)
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    try:
        return import_board(db, text, owner_id=user.id, trusted=False, allow_locked=user.role == "admin")
    except ImportError_ as failure:
        raise TemplateError(str(failure)) from failure


def integrations_for(db: Session, kinds: set[str], user: User) -> list[dict[str, Any]]:
    """The connections a template's placeholders may be mapped to."""
    rows = db.scalars(select(Integration).where(Integration.kind.in_(kinds)).order_by(Integration.name))
    return [{"id": row.id, "name": row.name, "kind": row.kind}
            for row in rows if not row.admin_only or user.role == "admin"]
