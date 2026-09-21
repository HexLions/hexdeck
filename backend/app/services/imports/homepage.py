"""Homepage (gethomepage.dev): ``services.yaml``, ``bookmarks.yaml`` and ``widgets.yaml``.

Services are a list of groups, each a list of services with ``href``,
``icon``, ``description`` and an optional ``widget`` (or ``widgets``) with a
``type`` and the connection's ``url`` and ``key``. Groups may nest. Every
group becomes a page; a service becomes a tile, and one with a widget HexDeck
has an adapter for also a connection and a card. Bookmarks become one
bookmarks card per group on a page of their own; the information widgets
(clock, weather, search) become the cards of the same name on the first page.
"""

from __future__ import annotations

from typing import Any

import yaml

from . import DashboardImportError, Plan, adapter_for, guess_adapter_by_name, icon_name

#: Homepage's widget types whose name differs from HexDeck's adapter kind.
TYPES: dict[str, str] = {
    "adguard-home": "adguard",
    "uptime-kuma": "uptimekuma",
    "plex-tautulli": "tautulli",
    "proxmoxbackupserver": "pbs",
    "nginx-proxy-manager": "npm",
    "unifi-controller": "unifi",
    "speedtest-tracker": "speedtest",
    "whatsupdocker": "wud",
    "paperlessngx": "paperless",
    "changedetectionio": "changedetection",
    "calibre-web": "calibreweb",
    "diskstation": "synology",
    "openwebui": "openwebui",
    "jellyseerr": "jellyseerr",
    "overseerr": "overseerr",
}


def _load(text: str, what: str) -> Any:
    try:
        return yaml.safe_load(text) if text.strip() else None
    except yaml.YAMLError as failure:
        raise DashboardImportError(f"{what} is not valid YAML: {failure}") from failure


def _entries(node: Any) -> list[tuple[str, Any]]:
    """Homepage writes lists of one-key mappings; this reads them as (name, body) pairs."""
    pairs: list[tuple[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and len(item) == 1:
                name, body = next(iter(item.items()))
                pairs.append((str(name), body))
    elif isinstance(node, dict):
        pairs.extend((str(k), v) for k, v in node.items())
    return pairs


def _service(plan: Plan, page: str, name: str, body: dict[str, Any]) -> None:
    href = str(body.get("href") or "")
    description = str(body.get("description") or "")
    icon = icon_name(body.get("icon"))
    widgets = body.get("widgets") if isinstance(body.get("widgets"), list) else ([body["widget"]] if isinstance(body.get("widget"), dict) else [])
    made_card = False
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        kind = str(widget.get("type") or "")
        if kind == "customapi":
            if _custom_api(plan, page, name, widget):
                made_card = True
            continue
        adapter = adapter_for(TYPES.get(kind, kind))
        if adapter is None:
            if kind:
                plan.warn(f"{name}: HexDeck has no adapter for the Homepage widget {kind!r}; a tile was made instead.")
            continue
        given = {k: v for k, v in widget.items() if k != "type"}
        if not given.get("url") and href:
            given["url"] = href
        card = plan.service_card(page, adapter, name, given)
        if card is not None:
            card["link"] = href
            if not icon:
                icon = adapter.icon
            made_card = True
    if not icon:
        guessed = guess_adapter_by_name(name)
        icon = guessed.icon if guessed else ""
    if href or not made_card:
        plan.tile(page, name, href, icon, description)


def _custom_api(plan: Plan, page: str, name: str, widget: dict[str, Any]) -> bool:
    """Homepage's customapi: one JSON address, several fields read out of the
    answer. HexDeck's JSON API card shows one value, so every mapping becomes
    a card on one connection."""
    adapter = adapter_for("jsonapi")
    url = str(widget.get("url") or "")
    mappings = [m for m in (widget.get("mappings") or []) if isinstance(m, dict) and m.get("field")]
    if adapter is None or not url or not mappings:
        plan.warn(f"{name}: the customapi widget has no address or no mappings, so only a tile was made.")
        return False
    given: dict[str, Any] = {"url": url}
    headers = widget.get("headers") if isinstance(widget.get("headers"), dict) else {}
    if headers:
        given["headers"] = "\n".join(f"{k}: {v}" for k, v in headers.items())
    key = plan.connection(adapter, name, given)
    for mapping in mappings:
        label = str(mapping.get("label") or mapping["field"])
        options: dict[str, Any] = {"value_path": str(mapping["field"]), "label": label}
        if mapping.get("format") == "percent":
            options["unit"] = "%"
        if mapping.get("suffix"):
            options["unit"] = str(mapping["suffix"])
        plan.card(page, "jsonapi.value", f"{name} · {label}", icon=adapter.icon, connection=key, options=options)
    return True


def _walk(plan: Plan, group: str, items: Any) -> None:
    for name, body in _entries(items):
        if isinstance(body, list):
            # A nested group: its own page, named after both.
            _walk(plan, f"{group} · {name}", body)
        elif isinstance(body, dict):
            _service(plan, group, name, body)


def _bookmarks(plan: Plan, document: Any) -> None:
    for group, items in _entries(document):
        lines = []
        for name, body in _entries(items):
            entry = body[0] if isinstance(body, list) and body and isinstance(body[0], dict) else body if isinstance(body, dict) else {}
            href = str(entry.get("href") or "")
            if not href:
                continue
            icon = icon_name(entry.get("icon")) or "lucide:link"
            lines.append(f"{name} | {href} | {icon}")
        if lines:
            plan.card("Bookmarks", "core.bookmarks", group, icon="lucide:bookmark", options={"links": "\n".join(lines), "layout": "list"})


def _information(plan: Plan, document: Any, page: str) -> None:
    """The bar above a Homepage: a clock, the weather, a search field."""
    for kind, body in _entries(document):
        body = body if isinstance(body, dict) else {}
        if kind == "datetime":
            plan.card(page, "core.clock", "Clock", options={"format": "12h" if str(body.get("format", {}).get("hour12", "")).lower() == "true" else "24h"})
        elif kind in ("openmeteo", "openweathermap", "weather"):
            options: dict[str, Any] = {"units": "imperial" if str(body.get("units", "")).lower() == "imperial" else "metric"}
            for theirs, ours in (("latitude", "latitude"), ("longitude", "longitude"), ("label", "place")):
                if body.get(theirs) not in (None, ""):
                    options[ours] = body[theirs]
            plan.card(page, "weather.current", str(body.get("label") or "Weather"), options=options)
        elif kind == "search":
            plan.card(page, "core.search", "Search", options={})
        else:
            plan.warn(f"The information widget {kind!r} has no HexDeck card and was left out.")


def plan(files: dict[str, str]) -> Plan:
    services = _load(files.get("services", ""), "services.yaml")
    bookmarks = _load(files.get("bookmarks", ""), "bookmarks.yaml")
    information = _load(files.get("widgets", ""), "widgets.yaml")
    if services is None and bookmarks is None:
        raise DashboardImportError("Paste at least services.yaml or bookmarks.yaml.")
    result = Plan("homepage", "Homepage")
    first = _entries(services)[0][0] if _entries(services) else "Overview"
    if information is not None:
        _information(result, information, first)
    if services is not None:
        if not _entries(services):
            raise DashboardImportError("services.yaml has no groups: it is a list of groups, each a list of services.")
        for group, items in _entries(services):
            result.page(group)
            _walk(result, group, items)
    if bookmarks is not None:
        _bookmarks(result, bookmarks)
    if not result.pages:
        raise DashboardImportError("Nothing in these files could be turned into a card.")
    return result
