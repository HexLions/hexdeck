"""Widgets that need no service: clock, notes, bookmarks, search, iframe."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func

from .base import Action, Adapter, AdapterError, Context, Field, WidgetData, WidgetType, ago


def todo_items(text: str) -> list[dict[str, Any]]:
    """The lines of a to-do list as ``{title, done}``.

    A line that begins with ``x`` and a space is done; the marker is written
    back the same way, so the list stays a list somebody can edit by hand in
    the card's settings.
    """
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        done = stripped[:2].lower() in ("x ", "- ") and stripped[:2].lower() == "x "
        items.append({"title": stripped[2:].strip() if done else stripped, "done": done})
    return items


def todo_text(items: list[dict[str, Any]]) -> str:
    """The lines a list of items is stored as."""
    return "\n".join(("x " if item.get("done") else "") + str(item.get("title") or "").strip() for item in items if str(item.get("title") or "").strip())


class CoreAdapter(Adapter):
    kind = "core"
    label = "Basics"
    category = "basics"
    description = "Clock, notes, bookmarks and embedded pages. No connection needed."
    icon = "lucide:layout-dashboard"
    beta = False
    needs_integration = False
    widgets = (
        WidgetType(
            kind="clock",
            label="Clock",
            description="Time and date, optionally for another time zone.",
            renderer="clock",
            default_size=(3, 2),
            min_size=(2, 1),
            refresh_seconds=3600,
            client_only=True,
            options=(
                Field("face", "Face", type="select", default="digits",
                      options=(("digits", "Digits"), ("hands", "Hands"))),
                Field("timezone", "Time zone", type="timezone", placeholder="Europe/Berlin", help="Empty means the browser's zone."),
                Field("format", "Format", type="select", default="24h", options=(("24h", "24-hour"), ("12h", "12-hour")),
                      only_when=("face", "digits")),
                Field("seconds", "Show seconds", type="bool", default=False),
                Field("date", "Show date", type="bool", default=True),
                Field("label", "Subtitle", placeholder="Home", help="A small line under the date, such as the place."),
                Field("colour", "Colour", type="colour", default="",
                      help="Empty takes the board's own. It applies to the digits and to the hands."),
            ),
        ),
        WidgetType(
            kind="markdown",
            label="Notes",
            description="A card of text with Markdown formatting.",
            renderer="text",
            default_size=(3, 2),
            refresh_seconds=3600,
            client_only=True,
            options=(Field("content", "Text", type="textarea", default="Write something here.\n\n- lists\n- **bold**\n- [links](https://example.com)"),),
        ),
        WidgetType(
            kind="bookmarks",
            label="Bookmarks",
            description="A list of links with icons.",
            renderer="bookmarks",
            default_size=(3, 2),
            refresh_seconds=3600,
            client_only=True,
            options=(
                Field(
                    "links",
                    "Links",
                    type="textarea",
                    help="One per line: Title | URL | icon (optional, a service name like plex, or lucide:book for a drawn symbol).",
                    # ⚠️ book and activity are drawn symbols, not service
                    # logos. Written without the prefix they were looked up
                    # as logos, and every board answered two 404s per load.
                    default="Documentation | https://example.com/docs | lucide:book\nStatus page | https://example.com/status | lucide:activity",
                ),
                Field("layout", "Layout", type="select", default="list", options=(("list", "List"), ("grid", "Icon grid"))),
            ),
        ),
        WidgetType(
            kind="search",
            label="Search",
            description="A search field on the board, for the engines and services set up under Search.",
            renderer="search",
            # Two rows: the field, and the row of targets under it. With the
            # row switched off one row is enough, and two columns is still a
            # usable bar: a search field in a corner is a reasonable want.
            default_size=(4, 2),
            min_size=(2, 1),
            refresh_seconds=3600,
            client_only=True,
            options=(
                Field("placeholder", "Placeholder", placeholder="Search", help="The grey text in the empty field."),
                Field("target", "Default target", placeholder="g", help="The shortcut of the target that Enter uses. Empty means the first one in the list."),
                Field("show_targets", "Show the other targets", type="bool", default=True, help="A row of buttons under the field, one per target. With a dozen targets a card two rows high is mostly buttons."),
                Field("show_shortcuts", "Show the shortcuts on the buttons", type="bool", default=True, help="The !x behind each name. Off makes the row narrower."),
                Field("new_tab", "Open in a new tab", type="bool", default=True),
                Field("autofocus", "Put the cursor in the field", type="bool", default=False, help="Only sensible once on a board, and it takes the keyboard from everything else."),
            ),
        ),
        WidgetType(
            kind="iframe",
            label="Embedded page",
            description="Shows another page inside the card. Many services forbid embedding.",
            renderer="iframe",
            default_size=(6, 4),
            min_size=(2, 2),
            refresh_seconds=3600,
            client_only=True,
            options=(
                Field("url", "URL", type="url", required=True, placeholder="https://example.com"),
                Field("refresh", "Reload every (seconds)", type="number", default=0, help="0 means never."),
            ),
        ),
        WidgetType(
            kind="button",
            label="Button card",
            description="One button that leads to a board or an address. Its name and symbol are the card's own.",
            renderer="button",
            default_size=(2, 1),
            min_size=(1, 1),
            refresh_seconds=3600,
            client_only=True,
            options=(
                Field("kind", "What it does", type="select", default="board",
                      options=(("board", "Open a board"), ("link", "Open an address"),
                               ("action", "Trigger an action"))),
                Field("board", "Which board", type="board", only_when=("kind", "board"),
                      help="A board, or one page of it."),
                Field("url", "Address", type="url", only_when=("kind", "link"),
                      placeholder="https://nas.example.com"),
                Field("new_tab", "Open in a new tab", type="bool", default=True, only_when=("kind", "link")),
                # ⚠️ Three lists, nothing typed. A library is "section 7" on
                # one server and an item id on the next, and a name written by
                # hand points at nothing the day it is renamed.
                Field("service", "Connection", type="integrations", default="",
                      only_when=("kind", "action"),
                      help="Only connections that offer something a button may trigger."),
                Field("deed", "Action", type="choices", from_field="service",
                      only_when=("kind", "action"),
                      help="Pick the connection first; what it offers appears here."),
                Field("target", "What it acts on", type="choices", from_field="service",
                      only_when=("kind", "action")),
                Field("confirm", "Ask before it runs", type="bool", default=False,
                      only_when=("kind", "action")),
                Field("look", "Look", type="select", default="label",
                      options=(("label", "Symbol and name"), ("icon", "Symbol only, large"), ("text", "Name only"))),
                Field("colour", "Colour", type="colour", default="",
                      help="Empty keeps the look of every other card."),
            ),
        ),
        WidgetType(
            kind="image",
            label="Picture",
            description="One picture, or a list of them as a slideshow.",
            renderer="image",
            default_size=(3, 2),
            min_size=(1, 1),
            refresh_seconds=3600,
            client_only=True,
            options=(
                Field("pictures", "Pictures", type="pictures",
                      help="Upload files, or name an address. Both go in the same list."),
                Field("every", "Move on every (seconds)", type="number", default=8,
                      help="0 keeps the first picture. On a wall display, slower is better."),
                Field("fit", "Crop", type="select", default="cover",
                      options=(("cover", "Fill the card, edges cropped"), ("contain", "Whole picture, edges left free"))),
                Field("captions", "Show the captions", type="bool", default=True),
            ),
        ),
        WidgetType(
            kind="problems",
            label="Problems",
            description="Every card on this board that is yellow or red, with its reason.",
            renderer="list",
            default_size=(3, 2),
            min_size=(2, 1),
            refresh_seconds=15,
        ),
        WidgetType(
            kind="updates",
            label="Updates",
            description="One list of what has a newer version: the containers WUD, Cup or Watchtower watch, the releases you follow, and HexDeck itself.",
            renderer="list",
            default_size=(3, 3),
            min_size=(2, 2),
            refresh_seconds=900,
            options=(
                Field("sources", "Connections", type="integrations",
                      options=(("wud", "What's Up Docker"), ("cup", "Cup"), ("watchtower", "Watchtower")),
                      default=[], help="The services that watch your images."),
                Field("repos", "Repositories", type="textarea", placeholder="owner/name",
                      help="One per line, as owner/name on GitHub. Their newest release is shown, whether or not it runs in Docker."),
                Field("hexdeck", "HexDeck itself", type="bool", default=True,
                      help="Needs the update check under System; without it HexDeck asks GitHub nothing."),
                Field("limit", "Entries", type="number", default=12),
            ),
        ),
        WidgetType(
            kind="todo",
            label="To do",
            description="A list to tick off, kept on the server: the same list on every browser and every screen.",
            renderer="todo",
            default_size=(3, 3),
            min_size=(2, 2),
            refresh_seconds=3600,
            options=(
                Field("items", "Items", type="textarea", default="",
                      help="One per line. A line that begins with 'x ' is already done; ticking a box on the card writes the same thing."),
                Field("hide_done", "Hide what is done", type="bool", default=False),
                Field("open", "Anyone who may see the board may tick", type="bool", default=False,
                      help="For a shopping list at home: guests and viewers tick and add, and change nothing else on the board."),
            ),
        ),
        WidgetType(
            kind="status",
            label="Status page",
            description="Every card with a reachability check, what it answers and how it has been doing.",
            renderer="list",
            default_size=(4, 3),
            min_size=(3, 2),
            refresh_seconds=30,
            options=(
                Field("scope", "Which cards", type="select", default="board",
                      options=(("board", "This board"), ("all", "Every board")),
                      help="Only cards whose reachability check is switched on are listed."),
                Field("bars", "Availability bars", type="select", default="24h",
                      options=(("24h", "Last 24 hours"), ("6h", "Last 6 hours"), ("1h", "Last hour"), ("live", "Last 48 checks"), ("", "None")),
                      help="The row under each service."),
                Field("only_down", "Only what is down", type="bool", default=False),
            ),
        ),
        WidgetType(
            kind="notices",
            label="Notices",
            description="What HexDeck has announced: outages, maintenance that is due, and everything else it sends.",
            renderer="list",
            default_size=(3, 3),
            min_size=(2, 2),
            refresh_seconds=60,
            options=(
                Field("level", "Which ones", type="select", default="",
                      options=(("", "Everything"), ("warning", "Warnings and errors"), ("error", "Errors only"))),
                Field("limit", "Entries", type="number", default=10),
                Field("unread_only", "Only unread", type="bool", default=False),
            ),
        ),
        WidgetType(
            kind="app",
            label="App tile",
            description="A launcher tile: icon, name, link and an optional reachability check.",
            renderer="app",
            default_size=(2, 1),
            min_size=(1, 1),
            refresh_seconds=3600,
            client_only=True,
            options=(
                Field("description", "Description", placeholder="What this service does"),
                Field("check", "Check reachability", type="bool", default=True),
                Field(
                    "bars",
                    "Availability bars",
                    type="select",
                    default="24h",
                    help="What the row of bars at the bottom of the tile shows.",
                    options=(
                        ("24h", "Last 24 hours, one bar per 30 minutes"),
                        ("6h", "Last 6 hours, one bar per 7.5 minutes"),
                        ("1h", "Last hour, one bar per minute"),
                        ("live", "Last 48 checks, one bar each"),
                    ),
                ),
                Field("open_new_tab", "Open in a new tab", type="bool", default=True),
            ),
        ),
    )

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        return "Nothing to test."

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any],
                     config: dict[str, Any], options: dict[str, Any], ctx: Context) -> str:
        """A button, pressed.

        ⚠️ Everything a card offers is checked against its last answer before
        this is reached, and for every other card that is enough: the answer
        came from the service. A button's answer comes from its own options,
        and those are written by whoever may edit the board. So the deed and
        its target are checked again here, against the target adapter's own
        declaration and its own list, which nobody editing a board can write.
        """
        if widget_kind != "button":
            return await super().action(widget_kind, action_id, params, config, options, ctx)
        if str(options.get("kind") or "") != "action":
            raise AdapterError("This button does not trigger anything.", code="no_such_action")
        if ctx.resolve_integration is None:
            raise AdapterError("The connection cannot be resolved here.", code="no_resolver")
        try:
            service = int(str(options.get("service") or "").strip())
        except ValueError:
            raise AdapterError(
                "This button has no connection picked.", code="no_integration",
                hint="Open the button settings and pick one.") from None

        adapter, service_config, service_ctx = await ctx.resolve_integration(service)
        deed = adapter.deed(action_id)
        if deed is None:
            raise AdapterError(f"{adapter.label} does not offer that.", code="no_such_action")

        call: dict[str, Any] = {}
        if deed.target_field:
            target = str(params.get("target") or options.get("target") or "")
            offered = {value for value, _label in await adapter.choices(deed.target_field, service_config, service_ctx)}
            # ⚠️ Against the service's own list, not against a pattern. A
            # target that is not on it either never existed or is gone, and
            # both are a reason to stop rather than to send it anyway.
            if target not in offered:
                raise AdapterError(
                    "That is not one of the things this connection offers.", code="no_such_target",
                    hint="Open the button settings and pick it again.")
            call[deed.target_field] = target
        return await adapter.action(deed.widget_kind, deed.id, call, service_config, {}, service_ctx)

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "problems":
            return self._problems(ctx)
        if widget_kind == "status":
            return self._status(ctx, options)
        if widget_kind == "notices":
            return self._notices(options)
        if widget_kind == "todo":
            return self._todo(options)
        if widget_kind == "updates":
            return await self._updates(ctx, options)
        return self.demo(widget_kind, options, 0)

    @staticmethod
    def _status(ctx: Context, options: dict[str, Any]) -> WidgetData:
        """Every checked card, what it answered last and how it has been doing.

        The checks, their latency and their history are HexDeck's own: the loop
        in ``services/health`` writes ``up`` and ``latency`` for each of them,
        which is what the tiles draw as their bars. This card puts the same
        thing in one list, so a board does not have to carry thirty tiles to
        say whether the house is up.
        """
        from sqlalchemy import select

        from ..db import db_session
        from ..models import HealthCheck, Page, Widget
        from ..services import health as health_service

        window = str(options.get("bars", "24h") or "")
        empty = WidgetData(status="warn", items=[], meta={"empty": "No card on this board has a reachability check"})
        with db_session() as db:
            me = db.get(Widget, ctx.widget_id) if ctx.widget_id else None
            page = db.get(Page, me.page_id) if me is not None else None
            if me is None or page is None:
                return empty
            widgets = select(Widget).join(Page)
            if str(options.get("scope") or "board") != "all":
                widgets = widgets.where(Page.board_id == page.board_id)
            rows = {widget.id: widget for widget in db.scalars(widgets)}
            checks = [
                check for check in db.scalars(select(HealthCheck).where(HealthCheck.enabled.is_(True)))
                if check.widget_id in rows
            ]
            if not checks:
                return empty
            bars = health_service.bars_for(db, {check.widget_id: window for check in checks}) if window else {}
            items: list[dict[str, Any]] = []
            for check in checks:
                widget = rows[check.widget_id]
                row = bars.get(check.widget_id) or []
                known = [value for value in row if value is not None]
                items.append({
                    "id": widget.id,
                    "title": widget.title or widget.kind,
                    # What it said, or how fast it said it: the one is a reason, the other a number.
                    "subtitle": check.last_error if check.last_ok is False else (f"{check.last_latency_ms} ms" if check.last_latency_ms is not None else ""),
                    "status": "unknown" if check.last_ok is None else ("ok" if check.last_ok else "bad"),
                    "url": widget.link or "",
                    "bars": row,
                    "uptime": round(100.0 * sum(known) / len(known), 1) if known else None,
                })
        if options.get("only_down"):
            items = [item for item in items if item["status"] != "ok"]
        # Down first, then unknown, then by name: a status page is read from the top.
        order = {"bad": 0, "unknown": 1, "ok": 2}
        items.sort(key=lambda item: (order.get(str(item["status"]), 3), str(item["title"]).lower()))
        up = sum(1 for item in items if item["status"] == "ok")
        down = sum(1 for item in items if item["status"] == "bad")
        return WidgetData(
            status="bad" if down else ("warn" if up < len(items) else "ok"),
            items=items,
            primary={"label": "Up", "value": f"{up} / {len(items)}"},
            metrics={"up": float(up), "down": float(down)},
            meta={"empty": "Everything answers", "bars": window},
        )

    @staticmethod
    async def _updates(ctx: Context, options: dict[str, Any]) -> WidgetData:
        """Everything that has a newer version, in one list.

        Three kinds of source, asked in turn and never allowed to take the card
        down: a connection whose adapter knows about updates, the releases of
        the repositories somebody follows, and HexDeck's own version.
        """
        from .. import __version__
        from . import get_adapter

        items: list[dict[str, Any]] = []
        failures: list[str] = []
        sources = options.get("sources") or []
        if isinstance(sources, str):
            sources = [part for part in sources.split(",") if part.strip()]
        for source in sources:
            try:
                adapter, config_of, source_ctx = await ctx.resolve_integration(int(source)) if ctx.resolve_integration else (None, {}, None)
                hook = getattr(adapter, "updates", None)
                if adapter is None or hook is None:
                    continue
                for row in await hook(config_of, source_ctx):
                    items.append({**row, "source": adapter.label, "icon": adapter.icon})
            except Exception as error:  # noqa: BLE001 - one source that fails is one line of warning
                name = getattr(adapter, "label", "A connection") if "adapter" in dir() else "A connection"
                failures.append(f"{name}: {getattr(error, 'message', str(error))}")
        repos = [line.strip() for line in str(options.get("repos") or "").splitlines() if line.strip()]
        if repos:
            try:
                github = get_adapter("github")
                data = await github.fetch("releases", {}, {"repos": "\n".join(repos[:10]), "preset": "", "limit": 10}, ctx)
                for row in data.items:
                    items.append({
                        "title": str(row.get("source") or row.get("title") or ""),
                        "subtitle": "Newest release",
                        "value": str(row.get("title") or ""),
                        "status": "ok",
                        "url": str(row.get("url") or ""),
                        "icon": github.icon,
                    })
            except Exception as error:  # noqa: BLE001 - the same
                failures.append(f"GitHub: {getattr(error, 'message', str(error))}")
        if options.get("hexdeck", True):
            try:
                from ..routers.system import latest_version

                newest = await latest_version()
                if newest and newest != __version__:
                    items.append({
                        "title": "HexDeck",
                        "subtitle": "A newer version is out",
                        "value": f"{__version__} → {newest}",
                        "status": "warn",
                        "url": "https://github.com/HexLions/hexdeck/releases/latest",
                        "icon": "lucide:layout-dashboard",
                    })
            except Exception as error:  # noqa: BLE001 - the same
                failures.append(f"HexDeck: {error}")
        # What waits first, then what only reports; inside each by name.
        order = {"bad": 0, "warn": 1, "ok": 2}
        items.sort(key=lambda item: (order.get(str(item.get("status")), 3), str(item.get("title", "")).lower()))
        limit = max(1, min(50, int(options.get("limit") or 12)))
        waiting = sum(1 for item in items if item.get("status") == "warn")
        return WidgetData(
            status="warn" if waiting or failures else "ok",
            items=items[:limit],
            primary={"label": "Waiting", "value": len(items)},
            metrics={"updates": float(waiting)},
            meta={"empty": "Everything is up to date", "failures": failures},
        )

    @staticmethod
    def _todo(options: dict[str, Any]) -> WidgetData:
        """The list as the card draws it. The items live in the card's own
        options, as one line each, a done one marked with ``x``; the card
        writes them back through ``POST /widgets/{id}/todo``."""
        items = todo_items(str(options.get("items") or ""))
        left = sum(1 for item in items if not item["done"])
        shown = [item for item in items if not (options.get("hide_done") and item["done"])]
        return WidgetData(
            status="ok",
            items=[{
                "id": index,
                "title": item["title"],
                "status": "ok" if item["done"] else "unknown",
                "done": item["done"],
            } for index, item in enumerate(shown)],
            primary={"label": "Left", "value": left},
            meta={"empty": "Nothing to do", "open": bool(options.get("open")), "hide_done": bool(options.get("hide_done"))},
        )

    @staticmethod
    def _notices(options: dict[str, Any]) -> WidgetData:
        """What HexDeck has announced, newest first.

        The notice centre is a drawer behind a bell; on a wall display nobody
        opens it. The same messages as a card say what happened while nobody
        was looking.
        """
        from sqlalchemy import select

        from ..db import db_session
        from ..models import Notice
        from ..services.notify import EVENTS

        wanted = str(options.get("level") or "")
        levels = {"error": ["error"], "warning": ["error", "warning"]}.get(wanted)
        limit = max(1, min(50, int(options.get("limit") or 10)))
        with db_session() as db:
            query = select(Notice).order_by(Notice.created_at.desc(), Notice.id.desc())
            if levels:
                query = query.where(Notice.level.in_(levels))
            if options.get("unread_only"):
                query = query.where(Notice.read_at.is_(None))
            found = list(db.scalars(query.limit(limit)))
            unread = db.scalar(select(func.count()).select_from(Notice).where(Notice.read_at.is_(None))) or 0
            items = [{
                "id": notice.id,
                "title": notice.title,
                "subtitle": notice.body or EVENTS.get(notice.event, notice.event),
                "status": {"error": "bad", "warning": "warn"}.get(notice.level, "unknown"),
                "url": notice.link or "",
                "value": ago(notice.created_at),
            } for notice in found]
        return WidgetData(
            status="bad" if any(item["status"] == "bad" for item in items) else ("warn" if any(item["status"] == "warn" for item in items) else "ok"),
            items=items,
            primary={"label": "Unread", "value": int(unread)},
            meta={"empty": "Nothing has happened"},
        )

    @staticmethod
    def _problems(ctx: Context) -> WidgetData:
        """Every other card of the same board that is yellow, red or failing, with its reason.

        A yellow dot says that something is wrong; this card says what. It reads
        the live state the collector already holds, so it costs no request.
        """
        from sqlalchemy import select

        from ..db import db_session
        from ..models import HealthCheck, Page, Widget
        from ..services.state import live

        fine = WidgetData(status="ok", items=[], meta={"empty": "Everything is fine"})
        if ctx.widget_id is None:
            return fine
        with db_session() as db:
            me = db.get(Widget, ctx.widget_id)
            page = db.get(Page, me.page_id) if me is not None else None
            if me is None or page is None:
                return fine
            pages = list(db.scalars(select(Page).where(Page.board_id == page.board_id).order_by(Page.position)))
            rows = [
                (other.name, widget.id, widget.title or widget.kind)
                for other in pages
                for widget in db.scalars(select(Widget).where(Widget.page_id == other.id))
                if widget.id != me.id
            ]
            several_pages = len(pages) > 1
            # ⚠️ The checks of the cards on this board count as well. An app tile
            # draws itself in the browser and has no live state, so a failing
            # check turned it red and this card beside it said everything was
            # fine. Found on 07.09.2026.
            failing = {
                check.widget_id: check.last_error
                for check in db.scalars(select(HealthCheck).where(
                    HealthCheck.widget_id.in_([widget_id for _page, widget_id, _title in rows]),
                    HealthCheck.enabled.is_(True),
                    HealthCheck.last_ok.is_(False),
                ))
            }
        items: list[dict[str, Any]] = []
        for page_name, widget_id, title in rows:
            data = live.get(widget_id)
            where = page_name if several_pages else ""
            if data is not None and data.error:
                items.append({"title": title, "subtitle": data.error, "error_code": str(data.meta.get("code") or ""), "status": "bad", "value": where})
            elif widget_id in failing:
                items.append({"title": title, "subtitle": failing[widget_id] or "Error", "status": "bad", "value": where})
            elif data is None:
                continue
            elif data.status in ("warn", "bad"):
                reasons = [str(data.meta.get("status_reason") or ""), *(str(u) for u in (data.meta.get("urgent") or []))]
                subtitle = " · ".join(r for r in reasons if r) or ("Error" if data.status == "bad" else "Warning")
                items.append({"title": title, "subtitle": subtitle, "status": data.status, "value": where})
        items.sort(key=lambda item: (item["status"] != "bad", item["title"].lower()))
        status = "bad" if any(item["status"] == "bad" for item in items) else ("warn" if items else "ok")
        return WidgetData(status=status, items=items, meta={"empty": "Everything is fine"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "clock":
            return WidgetData(meta={
                "face": "hands" if options.get("face") == "hands" else "digits",
                "timezone": options.get("timezone") or "",
                "format": options.get("format") or "24h",
                "seconds": bool(options.get("seconds")),
                "date": options.get("date", True),
                "label": options.get("label") or "",
                "colour": str(options.get("colour") or ""),
            })
        if widget_kind == "markdown":
            return WidgetData(meta={"markdown": options.get("content") or ""})
        if widget_kind == "image":
            return WidgetData(items=parse_pictures(options.get("pictures")), meta={
                "every": max(0, int(options.get("every") or 0)),
                "fit": "contain" if options.get("fit") == "contain" else "cover",
                "captions": options.get("captions", True),
            })
        if widget_kind == "button":
            asked = str(options.get("kind") or "board")
            kind = asked if asked in ("link", "action") else "board"
            where = str((options.get("url") if kind == "link" else options.get("board")) or "").strip()
            data = WidgetData(meta={
                "kind": kind,
                "where": where,
                "new_tab": options.get("new_tab", True),
                "look": str(options.get("look") or "label"),
                "colour": str(options.get("colour") or ""),
            })
            if kind == "action":
                # ⚠️ The action goes into the card's own answer, which is what
                # the guard in the collector checks a press against. A button
                # that only knew its action from its options would be the one
                # card in nexdeck that can be asked for anything.
                deed_id = str(options.get("deed") or "")
                target = str(options.get("target") or "")
                if deed_id:
                    params = {"target": target} if target else {}
                    data.actions = [Action(id=deed_id, label=deed_id, icon="zap",
                                           confirm=bool(options.get("confirm")), params=params)]
                else:
                    data.status = "warn"
                    data.error = "This button has no action picked yet."
                data.meta["deed"] = deed_id
                data.meta["target"] = target
            return data
        if widget_kind == "bookmarks":
            return WidgetData(items=parse_links(options.get("links") or ""), meta={"layout": options.get("layout") or "list"})
        if widget_kind == "search":
            return WidgetData(meta={
                "placeholder": options.get("placeholder") or "",
                "target": options.get("target") or "",
                "show_targets": options.get("show_targets", True),
                "show_shortcuts": options.get("show_shortcuts", True),
                "new_tab": options.get("new_tab", True),
                "autofocus": bool(options.get("autofocus")),
            })
        if widget_kind == "iframe":
            return WidgetData(meta={"url": options.get("url") or "", "refresh": int(options.get("refresh") or 0)})
        if widget_kind == "problems":
            return WidgetData(
                status="bad",
                items=[
                    {"title": "Radarr", "subtitle": "The service could not be reached.", "error_code": "unreachable", "status": "bad", "value": ""},
                    {"title": "UniFi Network", "subtitle": "1 device(s) offline", "status": "warn", "value": ""},
                ],
                meta={"empty": "Everything is fine"},
            )
        if widget_kind == "updates":
            return WidgetData(
                status="warn",
                primary={"label": "Waiting", "value": 3},
                items=[
                    {"title": "radarr", "subtitle": "Minor", "value": "5.2.0 → 5.3.0", "status": "warn", "icon": "lucide:package", "url": ""},
                    {"title": "HexDeck", "subtitle": "A newer version is out", "value": "0.17.0 → 0.18.0", "status": "warn", "icon": "lucide:layout-dashboard", "url": ""},
                    {"title": "jellyfin/jellyfin", "subtitle": "Newest release", "value": "10.11.12", "status": "ok", "icon": "lucide:package", "url": ""},
                ],
                meta={"empty": "Everything is up to date", "failures": []},
            )
        if widget_kind == "todo":
            return WidgetData(
                status="ok",
                primary={"label": "Left", "value": 2},
                items=[
                    {"id": 0, "title": "Order the rack rails", "status": "unknown", "done": False},
                    {"id": 1, "title": "Label the cables", "status": "unknown", "done": False},
                    {"id": 2, "title": "Replace the UPS battery", "status": "ok", "done": True},
                ],
                meta={"empty": "Nothing to do", "open": False, "hide_done": False},
            )
        if widget_kind == "status":
            bars = [1.0] * 40 + [0.0, 0.0] + [1.0] * 6
            return WidgetData(
                status="bad",
                primary={"label": "Up", "value": "3 / 4"},
                items=[
                    {"id": 1, "title": "Radarr", "subtitle": "The service could not be reached.", "status": "bad", "bars": bars[:30] + [0.0] * 18, "uptime": 62.5, "url": ""},
                    {"id": 2, "title": "Jellyfin", "subtitle": "31 ms", "status": "ok", "bars": bars, "uptime": 95.8, "url": ""},
                    {"id": 3, "title": "Pi-hole", "subtitle": "8 ms", "status": "ok", "bars": [1.0] * 48, "uptime": 100.0, "url": ""},
                    {"id": 4, "title": "Proxmox", "subtitle": "12 ms", "status": "ok", "bars": [1.0] * 48, "uptime": 100.0, "url": ""},
                ],
                meta={"empty": "Everything answers", "bars": options.get("bars", "24h")},
            )
        if widget_kind == "notices":
            return WidgetData(
                status="bad",
                primary={"label": "Unread", "value": 3},
                items=[
                    {"id": 1, "title": "Radarr is down", "subtitle": "No answer since 21:40", "status": "bad", "value": "12m", "url": ""},
                    {"id": 2, "title": "Test the backup restore", "subtitle": "Homelab, today", "status": "warn", "value": "3h", "url": ""},
                    {"id": 3, "title": "A new HexDeck version", "subtitle": "0.17.0 is out", "status": "unknown", "value": "1d", "url": ""},
                ],
                meta={"empty": "Nothing has happened"},
            )
        if widget_kind == "app":
            return WidgetData(meta={
                "description": options.get("description") or "",
                "check": options.get("check", True),
                "open_new_tab": options.get("open_new_tab", True),
            })
        raise KeyError(widget_kind)


def parse_pictures(written: Any) -> list[dict[str, str]]:
    """The pictures of a card, however they were written down.

    The list the picker builds is ``[{"url": …, "caption": …}]``. ⚠️ The old
    shape, one line per picture with the caption after a pipe, is still read:
    a card made before the picker existed must not lose its pictures because
    the field it was filled in with was replaced.
    """
    pictures: list[dict[str, str]] = []
    if isinstance(written, list):
        for one in written:
            if not isinstance(one, dict):
                continue
            url = str(one.get("url") or "").strip()
            if url:
                pictures.append({"url": url, "title": str(one.get("caption") or one.get("title") or "").strip()})
        return pictures
    for line in str(written or "").splitlines():
        line = line.strip()
        if not line:
            continue
        url, _, caption = line.partition("|")
        url = url.strip()
        if url:
            pictures.append({"url": url, "title": caption.strip()})
    return pictures


def parse_links(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        title = parts[0]
        url = parts[1] if len(parts) > 1 else ""
        icon = parts[2] if len(parts) > 2 else ""
        if not url and title.startswith("http"):
            url, title = title, title
        items.append({"title": title, "url": url, "icon": icon})
    return items


ADAPTER = CoreAdapter()
