"""The three project cards, fed from the local tables."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from app.adapters import get_adapter
from app.adapters.base import RENDERER_MIN, Context
from app.db import db_session
from app.models import Milestone, Project, ProjectItem, ProjectRepo


@pytest.fixture
def ctx() -> Context:
    return Context(httpx.AsyncClient(), integration_id=None, widget_id=1, cache={})


@pytest.fixture
def seeded(client) -> dict:
    today = date.today()
    with db_session() as db:
        p = Project(name="HexDeck", slug="hexdeck", status="active", colour="#3aa0ff", position=0)
        q = Project(name="Garden", slug="garden", status="active", colour="", position=1)
        db.add_all([p, q])
        db.flush()
        db.add(ProjectRepo(project_id=p.id, repo="HexLions/hexdeck", position=0))
        late = Milestone(project_id=p.id, title="Late", target_date=today - timedelta(days=3), status="open", position=0)
        soon = Milestone(project_id=p.id, title="Soon", target_date=today + timedelta(days=10), status="open", position=1)
        far = Milestone(project_id=q.id, title="Far", target_date=today + timedelta(days=60), status="open", position=0)
        done = Milestone(project_id=q.id, title="Done", target_date=today - timedelta(days=30), status="done", position=1)
        undated = Milestone(project_id=p.id, title="Someday", target_date=None, status="open", position=2)
        db.add_all([late, soon, far, done, undated])
        db.flush()
        db.add_all([
            ProjectItem(project_id=p.id, milestone_id=soon.id, title="Roadmap card", status="done", position=0),
            ProjectItem(project_id=p.id, milestone_id=soon.id, title="Items card", status="doing", issue="HexLions/hexdeck#7", position=1),
            ProjectItem(project_id=p.id, milestone_id=None, title="Docs", status="todo", position=2),
        ])
        db.commit()
        return {"p": p.id, "q": q.id, "soon": soon.id}


async def test_the_roadmap_sorts_by_date_and_grades_each_milestone(seeded, ctx: Context) -> None:
    data = await get_adapter("projects").fetch("roadmap", {}, {"weeks": "4", "done": False}, ctx)
    assert [(i["title"], i["status"]) for i in data.items] == [("Late", "late"), ("Soon", "soon"), ("Far", "open"), ("Someday", "open")]
    assert data.items[0]["days"] == -3 and data.items[1]["project"] == "HexDeck" and data.items[1]["colour"] == "#3aa0ff"
    assert data.primary["value"] == 1, "one milestone is due within four weeks; the late one is late, not due"
    assert data.items[0]["project_id"] == seeded["p"] and [p["name"] for p in data.meta["projects"]] == ["HexDeck", "Garden"]
    assert data.status == "bad", "a late milestone turns the card red"


async def test_the_roadmap_can_show_done_ones_and_one_project_only(seeded, ctx: Context) -> None:
    adapter = get_adapter("projects")
    data = await adapter.fetch("roadmap", {}, {"weeks": "12", "done": True, "project": str(seeded["q"])}, ctx)
    assert [(i["title"], i["status"]) for i in data.items] == [("Done", "done"), ("Far", "soon")]
    assert data.status == "warn", "sixty days is within a twelve-week horizon"


async def test_the_project_card_counts_and_names_the_next_milestone(seeded, ctx: Context) -> None:
    data = await get_adapter("projects").fetch("project", {}, {"project": str(seeded["p"])}, ctx)
    assert data.primary["value"] == 33 and data.primary["unit"] == "%"
    assert data.meta["name"] == "HexDeck" and data.meta["colour"] == "#3aa0ff"
    assert data.meta["next"]["title"] == "Late" and data.meta["next"]["days"] == -3
    assert [r["repo"] for r in data.items] == ["HexLions/hexdeck"]


async def test_a_project_without_repositories_works(seeded, ctx: Context) -> None:
    data = await get_adapter("projects").fetch("project", {}, {"project": str(seeded["q"])}, ctx)
    assert data.items == [] and data.status == "ok" and data.primary["value"] == 0


async def test_the_items_card_lists_in_order_and_can_narrow_to_a_milestone(seeded, ctx: Context) -> None:
    adapter = get_adapter("projects")
    data = await adapter.fetch("items", {}, {"project": str(seeded["p"])}, ctx)
    assert [i["title"] for i in data.items] == ["Roadmap card", "Items card", "Docs"]
    assert data.items[1]["url"] == "https://github.com/HexLions/hexdeck/issues/7" and data.items[0]["milestone"] == "Soon"
    narrowed = await adapter.fetch("items", {}, {"project": str(seeded["p"]), "milestone": str(seeded["soon"]), "hide_done": True}, ctx)
    assert [i["title"] for i in narrowed.items] == ["Items card"]
    assert data.meta["project_id"] == seeded["p"]


async def test_a_card_without_a_project_says_so(seeded, ctx: Context) -> None:
    data = await get_adapter("projects").fetch("project", {}, {}, ctx)
    assert data.status == "warn" and data.meta.get("empty")


def test_the_demo_fits_the_floors() -> None:
    adapter = get_adapter("projects")
    for widget in adapter.widgets:
        data = adapter.demo(widget.kind, {}, 0)
        assert data.items or data.primary
        assert RENDERER_MIN[widget.renderer] <= widget.min_size
