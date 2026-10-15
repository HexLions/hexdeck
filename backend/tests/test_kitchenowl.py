"""KitchenOwl: the shopping list and the meal plan.

The answers are written in the shapes KitchenOwl v0.7.10's controllers build
(``obj_to_dict`` of a household, ``obj_to_item_dict`` of a list item, the
planner's ``obj_to_full_dict``), with every moment in milliseconds since the
epoch as its JSON provider writes them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.adapters import get_adapter
from app.adapters.base import AdapterError, AuthFailed, Context

BASE = "http://kitchenowl.local:8080"
CONFIG = {"url": BASE, "token": "made-up-token"}
HOUSEHOLDS = [{"id": 1, "name": "Home", "photo": None, "language": "en", "planner_feature": True, "expenses_feature": True,
               "view_ordering": [], "created_at": 1726000000000, "updated_at": 1726000000000}]
LISTS = [
    {"id": 1, "name": "Weekly", "household_id": 1, "created_at": 1726000000000, "updated_at": 1726000000000, "recentItems": [],
     "items": [{"id": 11, "name": "Milk", "icon": None, "category_id": 2, "category": {"id": 2, "name": "Dairy"}, "description": "2 l",
                "created_at": 1726000000000, "updated_at": 1726000000000, "created_by": 1},
               {"id": 12, "name": "Flour", "icon": None, "category_id": None, "description": "", "created_at": 1726000000000,
                "updated_at": 1726000000000, "created_by": 1}]},
    {"id": 2, "name": "Hardware store", "household_id": 1, "recentItems": [], "items": []},
]


def _ms(day: datetime) -> int:
    return int(day.timestamp() * 1000)


def _ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={}, resolve_integration=None)


def _kitchenowl() -> None:
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    plans = [{"recipe_id": 5, "cooking_date": _ms(now + timedelta(days=1)), "yields": 2, "household_id": 1, "recipe": {"id": 5, "name": "Lentil curry"}},
             {"recipe_id": 4, "cooking_date": _ms(now), "yields": 4, "household_id": 1, "recipe": {"id": 4, "name": "Sourdough pizza"}},
             {"recipe_id": 6, "cooking_date": _ms(now + timedelta(days=12)), "yields": 4, "household_id": 1, "recipe": {"id": 6, "name": "Far away"}}]
    respx.get(f"{BASE}/api/household").mock(return_value=httpx.Response(200, json=HOUSEHOLDS))
    respx.get(f"{BASE}/api/household/1/shoppinglist").mock(return_value=httpx.Response(200, json=LISTS))
    respx.get(f"{BASE}/api/household/1/planner").mock(return_value=httpx.Response(200, json=plans))


@respx.mock
async def test_the_first_shopping_list() -> None:
    _kitchenowl()
    data = await get_adapter("kitchenowl").fetch("shopping", CONFIG, {}, _ctx())
    assert [(row["title"], row["subtitle"], row["value"]) for row in data.items] == [("Milk", "Dairy", "2 l"), ("Flour", "", "")]
    assert respx.calls[0].request.headers["authorization"] == "Bearer made-up-token"


@respx.mock
async def test_a_list_by_name_and_one_that_is_not_there() -> None:
    _kitchenowl()
    data = await get_adapter("kitchenowl").fetch("shopping", CONFIG, {"list": "hardware STORE"}, _ctx())
    assert data.items == [] and data.meta["title"] == "Hardware store"
    with pytest.raises(AdapterError, match="no shopping list"):
        await get_adapter("kitchenowl").fetch("shopping", CONFIG, {"list": "Garden"}, _ctx())


@respx.mock
async def test_the_meals_of_the_coming_week_in_order() -> None:
    _kitchenowl()
    data = await get_adapter("kitchenowl").fetch("planner", CONFIG, {}, _ctx())
    assert [(row["title"], row["subtitle"], row["value"]) for row in data.items] == [
        ("Sourdough pizza", "today", "4 servings"), ("Lentil curry", "tomorrow", "2 servings")]


@respx.mock
async def test_a_bad_token_and_an_unknown_household() -> None:
    route = respx.get(f"{BASE}/api/household").mock(return_value=httpx.Response(422, json={"msg": "Not enough segments"}))
    with pytest.raises(AuthFailed):
        await get_adapter("kitchenowl").test(CONFIG, _ctx())
    route.mock(return_value=httpx.Response(200, json=HOUSEHOLDS))
    with pytest.raises(AdapterError, match="no such household"):
        await get_adapter("kitchenowl").test({**CONFIG, "household": "Cabin"}, _ctx())
