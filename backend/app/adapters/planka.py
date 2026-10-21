"""Planka: the cards that are due, across the boards, overdue ones first.

Checked against Planka 2.2.1 (the prebuilt release, on PostgreSQL 17) running
locally on 21.10.2026, with a project, a board of three lists and four cards:
one overdue, one due tomorrow, one due in twenty days and one overdue but
ticked done; with an API key, a sign-in, a wrong key and a wrong token.

⚠️ Two ways in. An API key (made in Planka under the user's settings, ``POST
/api/users/{id}/api-key``) goes in ``X-Api-Key``. Without one the card signs
in with ``POST /api/access-tokens`` and uses the ``item`` it answers as a
bearer token. A wrong key and a wrong token are both 401 ``E_UNAUTHORIZED``.

⚠️ The very first sign-in of an account is not a token but 403
``E_FORBIDDEN`` with ``"step": "accept-terms"``: the terms have to be accepted
once, in a browser. An account with a second factor answers the same way with
another step. Neither can be done by a dashboard, so the card says so.

⚠️ ``/api/projects`` lists the projects with their boards under
``included.boards``; the cards are only in ``/api/boards/{id}``, under
``included.cards``, one request per board. A board carries two lists beside
its own: one of type ``archive`` and one of type ``trash``, without a name.
Cards in those are not counted. A card ticked done keeps its due date with
``isDueCompleted`` true, and is not due any more.

⚠️ Ids are numbers too long for JavaScript and come as strings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from . import demo as fake
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

TOKEN_KEY = "planka:token"
#: Boards read for the due cards; one request each.
MAX_BOARDS = 10
SOON_DAYS = 2
WEEK_DAYS = 7


def _when(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class PlankaAdapter(Adapter):
    kind = "planka"
    label = "Planka"
    category = "other"
    description = "Planka kanban boards: the cards that are due, overdue ones first, across every board you can see."
    icon = "planka"
    beta = True
    docs_url = "https://docs.planka.cloud/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://planka.example.com"),
        Field("api_key", "API key", secret=True,
              help="Made in Planka under your user settings. Or leave it empty and sign in below."),
        Field("username", "E-mail or user name", help="Only without an API key."),
        Field("password", "Password", type="password", secret=True, help="Only without an API key."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Due cards", description="Overdue cards, cards due this week, and open cards.",
                   renderer="value", default_size=(3, 2), refresh_seconds=300, metrics=("overdue",)),
        WidgetType(kind="due", label="Due", description="Every card with a due date that is not done, overdue first, with its board and list.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    # -- signing in ----------------------------------------------------------

    async def _auth(self, config: dict[str, Any], ctx: Context, fresh: bool = False) -> dict[str, str]:
        key = str(config.get("api_key") or "").strip()
        if key:
            return {"X-Api-Key": key}
        token = ctx.cache.get(TOKEN_KEY)
        if token and not fresh:
            return {"Authorization": f"Bearer {token}"}
        if not config.get("username") or not config.get("password"):
            raise AuthFailed("Planka needs an API key, or a user name and password.", hint="An API key is made in Planka under your user settings.")
        response = await ctx.request("POST", f"{base_url(config)}/api/access-tokens",
                                     json_body={"emailOrUsername": config.get("username"), "password": config.get("password")},
                                     verify=not config.get("insecure"), auth_errors=False)
        try:
            answer = response.json()
        except ValueError:
            answer = {}
        if response.status_code == 403 and isinstance(answer, dict) and answer.get("step"):
            step = str(answer.get("step"))
            raise AuthFailed("Planka wants this account to accept its terms first." if step == "accept-terms"
                             else "Planka asks this account for another sign-in step.",
                             hint="Sign in to Planka once in a browser, or use an API key, which skips these steps.")
        if response.status_code in (400, 401, 403):
            raise AuthFailed("Planka refused the user name or password.", hint="Check them, or use an API key instead.")
        if response.status_code >= 400:
            raise AdapterError(f"Planka answered the sign-in with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Planka's own address, without /api.")
        token = str((answer or {}).get("item") or "")
        if not token:
            raise AdapterError("This address answers, but not the way Planka does.", code="not_planka")
        ctx.cache[TOKEN_KEY] = token
        return {"Authorization": f"Bearer {token}"}

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 120, retry: bool = True) -> Any:
        headers = await self._auth(config, ctx)
        response = await ctx.request("GET", f"{base_url(config)}/api{path}", headers=headers,
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code == 401 and retry and not config.get("api_key"):
            # A token that ran out: sign in again, once.
            await self._auth(config, ctx, fresh=True)
            return await self._get(config, ctx, path, cache, retry=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Planka refused the API key or the sign-in.", hint="Make a new API key in Planka under your user settings.")
        if response.status_code >= 400:
            raise AdapterError(f"Planka answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is Planka's own address, without /api.")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way Planka does.", code="not_planka",
                               hint="Check the URL; it is Planka's own address, without /api.") from error

    async def _cards(self, config: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], int]:
        """The open cards of every board read, each with its board's and list's name, and the board count."""
        projects = await self._get(config, ctx, "/projects")
        boards = ((projects or {}).get("included") or {}).get("boards") if isinstance(projects, dict) else None
        if not isinstance(boards, list):
            raise AdapterError("This address answers, but not the way Planka does.", code="not_planka")
        cards: list[dict[str, Any]] = []
        for board in boards[:MAX_BOARDS]:
            detail = await self._get(config, ctx, f"/boards/{quote(str(board.get('id')), safe='')}")
            included = (detail or {}).get("included") or {}
            lists = {one.get("id"): one for one in included.get("lists") or [] if one.get("type") == "active"}
            for card in included.get("cards") or []:
                place = lists.get(card.get("listId"))
                if place is None:
                    continue  # archived or in the trash
                cards.append({**card, "_board": board.get("name") or "", "_list": place.get("name") or ""})
        return cards, len(boards)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        projects = await self._get(config, ctx, "/projects", cache=0)
        boards = ((projects or {}).get("included") or {}).get("boards") if isinstance(projects, dict) else None
        if not isinstance(boards, list):
            raise AdapterError("This address answers, but not the way Planka does.", code="not_planka")
        return f"Planka answers with {len(boards)} board{'' if len(boards) == 1 else 's'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        cards, boards = await self._cards(config, ctx)
        now = datetime.now(UTC)
        if widget_kind == "due":
            return self._due(cards, now, max(1, int(options.get("limit") or 10)))
        return self._summary(cards, boards, now)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _open_due(cards: list[dict[str, Any]]) -> list[tuple[datetime, dict[str, Any]]]:
        due = [(_when(card.get("dueDate")), card) for card in cards if card.get("dueDate") and not card.get("isDueCompleted")]
        return sorted(((moment, card) for moment, card in due if moment is not None), key=lambda pair: pair[0])

    @staticmethod
    def _summary(cards: list[dict[str, Any]], boards: int, now: datetime) -> WidgetData:
        due = PlankaAdapter._open_due(cards)
        overdue = sum(1 for moment, _card in due if moment < now)
        week = sum(1 for moment, _card in due if now <= moment and (moment - now).days < WEEK_DAYS)
        return WidgetData(
            status="bad" if overdue else "warn" if week else "ok",
            primary={"label": "Overdue", "value": overdue},
            secondary=[{"label": "Due this week", "value": week}, {"label": "Open cards", "value": len(cards)}, {"label": "Boards", "value": boards}],
            metrics={"overdue": float(overdue)},
        )

    @staticmethod
    def _due(cards: list[dict[str, Any]], now: datetime, limit: int) -> WidgetData:
        items = []
        for moment, card in PlankaAdapter._open_due(cards)[:limit]:
            days = (moment - now).total_seconds() / 86400
            if days < 0:
                value, state = f"{max(1, round(-days))} d overdue", "bad"
            else:
                value, state = f"in {max(1, round(days))} d", ("warn" if days < SOON_DAYS else "ok")
            items.append({"title": str(card.get("name") or "?"),
                          "subtitle": " · ".join(part for part in (card["_board"], card["_list"]) if part),
                          "status": state, "value": value})
        return WidgetData(status="bad" if any(item["status"] == "bad" for item in items) else "ok", items=items,
                          meta={"empty": "No card is due."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = datetime.now(UTC)

        def card(name: str, days: float, where: str, done: bool = False) -> dict[str, Any]:
            return {"name": name, "dueDate": (now + timedelta(days=days)).isoformat(), "isDueCompleted": done,
                    "_board": "Upgrades", "_list": where}

        cards = [card("Replace UPS battery", -2, "To do"), card("Renew certificates", 0.5 + (tick % 3), "To do"),
                 card("Move Plex to the new NAS", 20, "Doing"), card("Clean the dust filters", -2, "Doing", done=True),
                 card("Test the backups", fake.walk("planka-backups", tick, 3, 9), "To do")]
        if widget_kind == "due":
            return self._due(cards, now, max(1, int(options.get("limit") or 10)))
        return self._summary(cards, 2, now)


ADAPTER = PlankaAdapter()
