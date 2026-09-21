"""Homarr up to 0.15: the ``default.json`` (or any board's) config file.

The file holds ``apps``, ``widgets``, ``categories`` and ``wrappers``. An
app has a ``url``, an ``appearance.iconUrl``, a ``behaviour.externalUrl``
and, when Homarr read the service, an ``integration`` with a ``type`` and
``properties`` (``apiKey``, ``username``, ``password``). Every app and widget
sits in an ``area``: a named category or an unnamed wrapper, each with its
own grid, and a ``shape`` with a place and size per screen.

Every category becomes a page and keeps its places; the wrappers go on one
page in the order they had. Homarr 1.x keeps its boards in a database and
offers no file of its own; its "Migrate to 1.0" export is these same files.
"""

from __future__ import annotations

import json
from typing import Any

from . import DashboardImportError, Plan, adapter_for, icon_name

#: Homarr's integration types whose name differs from HexDeck's adapter kind.
TYPES: dict[str, str] = {
    "qBittorrent": "qbittorrent",
    "nzbGet": "nzbget",
    "adGuardHome": "adguard",
    "homeAssistant": "homeassistant",
}

#: Homarr's widgets that read a service, and the adapter cards that show the same.
SERVICE_WIDGETS: dict[str, tuple[tuple[str, str], ...]] = {
    "media-server": (("plex", "nowplaying"), ("jellyfin", "nowplaying"), ("emby", "nowplaying")),
    "dns-hole-summary": (("pihole", "summary"), ("adguard", "summary")),
    "dns-hole-controls": (("pihole", "summary"), ("adguard", "summary")),
    "torrents-status": (("qbittorrent", "queue"), ("transmission", "queue"), ("deluge", "queue")),
    "dlspeed": (("qbittorrent", "speed"), ("transmission", "speed"), ("deluge", "speed"), ("sabnzbd", "speed"), ("nzbget", "speed")),
    "usenet": (("sabnzbd", "queue"), ("nzbget", "queue")),
    "media-requests-list": (("overseerr", "requests"), ("jellyseerr", "requests")),
    "media-requests-stats": (("overseerr", "counts"), ("jellyseerr", "counts")),
    "indexer-manager": (("prowlarr", "indexers"),),
    "media-transcoding": (("tdarr", "queue"),),
    "calendar": (("sonarr", "calendar"), ("radarr", "calendar"), ("lidarr", "calendar"), ("readarr", "calendar")),
    "health-monitoring": (("proxmox", "summary"),),
    "smart-home/entity-state": (),
    "smart-home/trigger-automation": (),
}

WRAPPER_PAGE = "Overview"


def _shape(node: dict[str, Any]) -> dict[str, int] | None:
    shape = (node.get("shape") or {}).get("lg") or (node.get("shape") or {}).get("md")
    if not isinstance(shape, dict):
        return None
    try:
        location, size = shape["location"], shape["size"]
        return {"x": int(location["x"]), "y": int(location["y"]), "w": max(1, int(size["width"])), "h": max(1, int(size["height"]))}
    except (KeyError, TypeError, ValueError):
        return None


def _page_of(node: dict[str, Any], categories: dict[str, str]) -> tuple[str, bool]:
    """The page a tile belongs on, and whether it keeps its place there."""
    area = node.get("area") or {}
    if area.get("type") == "category":
        name = categories.get(str((area.get("properties") or {}).get("id")))
        if name:
            return name, True
    return WRAPPER_PAGE, False


def _given(app: dict[str, Any]) -> dict[str, Any]:
    given: dict[str, Any] = {"url": app.get("url")}
    for prop in (app.get("integration") or {}).get("properties") or []:
        if isinstance(prop, dict) and prop.get("field") and prop.get("value") not in (None, ""):
            given[str(prop["field"])] = prop["value"]
    return given


def _apps(plan: Plan, apps: list[Any], categories: dict[str, str], services: dict[str, tuple[str, dict[str, Any]]]) -> None:
    for app in apps:
        if not isinstance(app, dict) or not app.get("name"):
            continue
        name = str(app["name"])
        page, placed = _page_of(app, categories)
        layout = _shape(app) if placed else None
        behaviour = app.get("behaviour") or {}
        href = str(behaviour.get("externalUrl") or app.get("url") or "")
        icon = icon_name((app.get("appearance") or {}).get("iconUrl"))
        description = str(behaviour.get("tooltipDescription") or "")
        kind = str((app.get("integration") or {}).get("type") or "")
        adapter = adapter_for(TYPES.get(kind, kind)) if kind else None
        if kind and adapter is None:
            plan.warn(f"{name}: HexDeck has no adapter for the Homarr integration {kind!r}; a tile was made instead.")
        if adapter is not None:
            key = plan.connection(adapter, name, _given(app))
            services.setdefault(adapter.kind, (key, app))
        plan.tile(page, name, href, icon or (adapter.icon if adapter else ""), description, layout=layout)


def _widgets(plan: Plan, widgets: list[Any], categories: dict[str, str], services: dict[str, tuple[str, dict[str, Any]]]) -> None:
    for widget in widgets:
        if not isinstance(widget, dict):
            continue
        kind = str(widget.get("type") or "")
        props = widget.get("properties") or {}
        page, placed = _page_of(widget, categories)
        layout = _shape(widget) if placed else None
        if kind == "date":
            plan.card(page, "core.clock", str(props.get("customTitle") or "Clock"),
                      options={"format": "24h" if props.get("display24HourFormat", True) else "12h", "timezone": str(props.get("timezone") or "")}, layout=layout)
        elif kind == "weather":
            location = props.get("location") or {}
            options: dict[str, Any] = {"units": "imperial" if props.get("displayInFahrenheit") else "metric"}
            if location.get("name"):
                options["place"] = location["name"]
            for key in ("latitude", "longitude"):
                if isinstance(location.get(key), (int, float)):
                    options[key] = location[key]
            plan.card(page, "weather.current", str(location.get("name") or "Weather"), options=options, layout=layout)
        elif kind == "rss":
            urls = [str(u) for u in (props.get("rssFeedUrl") or []) if u]
            if urls:
                plan.card(page, "rss.headlines", "Feeds", options={"urls": "\n".join(urls), "limit": int(props.get("maximumAmountOfPosts") or 10)}, layout=layout)
        elif kind == "iframe":
            if props.get("embedUrl"):
                plan.card(page, "core.iframe", "Embedded page", options={"url": str(props["embedUrl"])}, layout=layout)
        elif kind == "notebook":
            plan.card(page, "notepad.pad", "Notebook", options={"content": str(props.get("content") or "")}, layout=layout)
        elif kind == "bookmark":
            lines = [f"{item.get('name') or item.get('href')} | {item['href']} | {icon_name(item.get('iconUrl')) or 'lucide:link'}"
                     for item in (props.get("items") or []) if isinstance(item, dict) and item.get("href")]
            if lines:
                plan.card(page, "core.bookmarks", str(props.get("name") or "Bookmarks"), icon="lucide:bookmark",
                          options={"links": "\n".join(lines), "layout": "grid" if props.get("layout") == "autoGrid" else "list"}, layout=layout)
        elif kind in SERVICE_WIDGETS:
            for adapter_kind, widget_kind in SERVICE_WIDGETS[kind]:
                if adapter_kind in services:
                    key, app = services[adapter_kind]
                    adapter = adapter_for(adapter_kind)
                    label = adapter.label if adapter else adapter_kind
                    plan.card(page, f"{adapter_kind}.{widget_kind}", str(app.get("name") or label), icon=adapter.icon if adapter else "", connection=key, layout=layout)
                    break
            else:
                plan.warn(f"The Homarr widget {kind!r} reads a service no app of this config is connected to, so it was left out.")
        elif kind:
            plan.warn(f"The Homarr widget {kind!r} has no HexDeck card and was left out.")


def plan(files: dict[str, str]) -> Plan:
    text = files.get("config") or next(iter(files.values()), "")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as failure:
        raise DashboardImportError(f"The Homarr config is not valid JSON: {failure}") from failure
    if not isinstance(document, dict) or not isinstance(document.get("apps"), list):
        raise DashboardImportError("This is not a Homarr config: it has no 'apps' list.")
    name = str((document.get("configProperties") or {}).get("name") or "Homarr")
    result = Plan("homarr", name)
    categories = {str(c.get("id")): str(c.get("name") or "Category") for c in document.get("categories") or [] if isinstance(c, dict)}
    # Pages in the order Homarr showed them: wrappers and categories by position, the wrappers merged into one.
    ordered = sorted(
        [(int(c.get("position") or 0), str(c.get("name") or "Category")) for c in document.get("categories") or [] if isinstance(c, dict)]
        + [(int(w.get("position") or 0), WRAPPER_PAGE) for w in document.get("wrappers") or [] if isinstance(w, dict)],
    )
    for _position, page in ordered:
        result.page(page)
    services: dict[str, tuple[str, dict[str, Any]]] = {}
    _apps(result, document["apps"], categories, services)
    _widgets(result, document.get("widgets") or [], categories, services)
    result.pages = [p for p in result.pages if p["cards"]]
    if not result.pages:
        raise DashboardImportError("Nothing in this config could be turned into a card.")
    return result
