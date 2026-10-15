"""KitchenOwl: what is on the shopping list, and what is planned to cook.

KitchenOwl's API lives under ``/api`` and takes a long-lived token (Settings,
Sessions, add a long-lived token) as a Bearer token. ``/api/household`` lists
the households of the token's user; ``/api/household/<id>/shoppinglist``
gives every list with its ``items`` (each with ``description``, the amount,
and ``category``), and ``/api/household/<id>/planner`` the planned meals with
their ``recipe`` and ``cooking_date``.

⚠️ Every moment is milliseconds since the epoch, in UTC
(``KitchenOwlJSONProvider``).

⚠️ A household and a list are picked by name; empty takes the first.

⚠️ A malformed token is 422, a revoked or expired one 401 (Flask-JWT-Extended).
Every installation describes its API at ``/api/openapi``.

Written from KitchenOwl's source (``register_controller.py``, the household,
shopping list and planner controllers and the JSON provider, release
v0.7.10). Not yet run against an installation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    base_url,
)

READ_SECONDS = 120


def _named(entries: list[Any], name: str) -> dict[str, Any] | None:
    entries = [one for one in entries if isinstance(one, dict)]
    wanted = name.strip().lower()
    if wanted:
        return next((one for one in entries if str(one.get("name") or "").lower() == wanted), None)
    return entries[0] if entries else None


class KitchenOwlAdapter(Adapter):
    kind = "kitchenowl"
    label = "KitchenOwl"
    category = "other"
    description = "KitchenOwl: what is on the shopping list, and the meals planned for the coming days."
    icon = "kitchenowl"
    docs_url = "https://docs.kitchenowl.org/latest/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://kitchenowl:8080"),
        Field("token", "Long-lived token", type="password", secret=True, required=True,
              help="Under Settings, Sessions: add a long-lived token for HexDeck."),
        Field("household", "Household", help="Its name; empty takes the first household of the token's user."),
    )
    widgets = (
        WidgetType(kind="shopping", label="Shopping list", description="What is on a shopping list, with the amounts.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120,
                   options=(Field("list", "List", help="Its name; empty takes the first list."),
                            Field("limit", "Entries", type="number", default=12))),
        WidgetType(kind="planner", label="Meal plan", description="The meals planned for the next seven days, by day.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/api{path}",
                                     headers={"Accept": "application/json", "Authorization": f"Bearer {config.get('token') or ''}"},
                                     cache_seconds=READ_SECONDS, auth_errors=False)
        if response.status_code in (401, 422):
            raise AuthFailed("KitchenOwl refused the token.", hint="A long-lived token from Settings, Sessions.")
        if response.status_code == 403:
            raise AuthFailed("The token's user is not a member of this household.")
        if response.status_code >= 400:
            raise AdapterError(f"KitchenOwl answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address you open KitchenOwl at, without /api.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("KitchenOwl did not answer with JSON.", code="not_json", hint="The address you open KitchenOwl at.") from failure

    async def _household(self, config: dict[str, Any], ctx: Context) -> dict[str, Any]:
        household = _named(await self._get(config, ctx, "/household") or [], str(config.get("household") or ""))
        if household is None:
            raise AdapterError("KitchenOwl has no such household for this user.", code="no_such_household",
                               hint="The household's name as KitchenOwl shows it, or empty for the first.")
        return household

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        household = await self._household(config, ctx)
        lists = await self._get(config, ctx, f"/household/{int(household['id'])}/shoppinglist") or []
        return f"KitchenOwl answers: {household.get('name')}, {len(lists)} shopping list{'s' if len(lists) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        household = await self._household(config, ctx)
        if widget_kind == "planner":
            return self._planner(await self._get(config, ctx, f"/household/{int(household['id'])}/planner") or [], datetime.now(UTC))
        lists = await self._get(config, ctx, f"/household/{int(household['id'])}/shoppinglist") or []
        chosen = _named(lists, str(options.get("list") or ""))
        if chosen is None:
            raise AdapterError("This household has no shopping list of that name.", code="no_such_list")
        return self._shopping(chosen, max(1, int(options.get("limit") or 12)))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _shopping(shopping: dict[str, Any], limit: int) -> WidgetData:
        items = [one for one in shopping.get("items") or [] if isinstance(one, dict)]
        rows = [{"id": str(item.get("id") or item.get("name")), "title": str(item.get("name") or "?"),
                 "subtitle": str((item.get("category") or {}).get("name") or ""), "value": str(item.get("description") or ""), "status": "ok"}
                for item in items]
        return WidgetData(status="ok", items=rows[:limit], secondary=[{"label": "Items", "value": len(items)}],
                          meta={"empty": "The list is empty", "title": str(shopping.get("name") or "")})

    @staticmethod
    def _planner(plans: list[Any], now: datetime) -> WidgetData:
        today = now.date()
        rows = []
        for plan in sorted((one for one in plans if isinstance(one, dict) and one.get("cooking_date") is not None),
                           key=lambda one: float(one["cooking_date"])):
            day = datetime.fromtimestamp(float(plan["cooking_date"]) / 1000, UTC).date()
            if not today <= day <= today + timedelta(days=6):
                continue
            recipe = plan.get("recipe") or {}
            rows.append({
                "id": f"{plan.get('recipe_id')}:{plan['cooking_date']}",
                "title": str(recipe.get("name") or "?"),
                "subtitle": "today" if day == today else "tomorrow" if day == today + timedelta(days=1) else day.isoformat(),
                "value": f"{int(plan['yields'])} servings" if plan.get("yields") else "",
                "status": "ok",
            })
        return WidgetData(status="ok", items=rows, meta={"empty": "Nothing planned for the next seven days"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = datetime.now(UTC)
        if widget_kind == "planner":
            day = 86_400_000
            start = int(now.timestamp() * 1000)
            plans = [{"recipe_id": 1, "cooking_date": start, "yields": 4, "recipe": {"name": "Sourdough pizza"}},
                     {"recipe_id": 2, "cooking_date": start + day, "yields": 2, "recipe": {"name": "Lentil curry"}},
                     {"recipe_id": 3, "cooking_date": start + 3 * day, "yields": 4, "recipe": {"name": "Risotto"}}]
            return self._planner(plans, now)
        items = [{"id": 1, "name": "Milk", "description": "2 l", "category": {"name": "Dairy"}},
                 {"id": 2, "name": "Tomatoes", "description": "1 kg", "category": {"name": "Vegetables"}},
                 {"id": 3, "name": "Flour", "description": "", "category": {"name": "Baking"}}]
        if fake.flicker("kitchenowl-coffee", tick, 0.5):
            items.append({"id": 4, "name": "Coffee", "description": "500 g", "category": {"name": "Drinks"}})
        return self._shopping({"name": "Weekly", "items": items}, max(1, int(options.get("limit") or 12)))


ADAPTER = KitchenOwlAdapter()
