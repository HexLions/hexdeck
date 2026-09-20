"""Projects, milestones and items, from HexDeck's own tables.

No service and no connection: the truth is the local database, and the
cards read it the way the "problems" card of the basics reads the live
state. GitHub is a reference on an item, shown as a link, never written to.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from .base import Adapter, Context, Field, WidgetData, WidgetType

WEEKS = (("4", "4 weeks"), ("8", "8 weeks"), ("12", "12 weeks"))


class ProjectsAdapter(Adapter):
    kind = "projects"
    label = "Projects"
    category = "basics"
    description = "What is being built: roadmap of milestones, one project's state, and its items to tick off."
    icon = "lucide:map"
    beta = False
    needs_integration = False
    widgets = (
        WidgetType(
            kind="roadmap",
            label="Roadmap",
            description="Every milestone of every project on one time line, the late and the imminent ones marked.",
            renderer="roadmap",
            default_size=(6, 2),
            min_size=(4, 2),
            refresh_seconds=300,
            options=(
                Field("weeks", "Horizon", type="select", default="4", options=WEEKS,
                      help="Milestones due within this are marked as coming up."),
                Field("project", "Project", type="project", help="Empty shows every project."),
                Field("done", "Show finished milestones", type="bool", default=False),
            ),
        ),
        WidgetType(
            kind="project",
            label="Project",
            description="One project: its state, the next milestone, how much is done and the repositories it reads.",
            renderer="project",
            default_size=(3, 2),
            min_size=(3, 2),
            refresh_seconds=300,
            options=(Field("project", "Project", type="project", required=True),),
        ),
        WidgetType(
            kind="items",
            label="Items",
            description="The items of a project, to tick off and reorder on the board.",
            renderer="items",
            default_size=(3, 3),
            min_size=(3, 2),
            refresh_seconds=300,
            options=(
                Field("project", "Project", type="project", required=True),
                Field("milestone", "Milestone", type="milestone", from_field="project", help="Empty shows every item."),
                Field("hide_done", "Hide finished items", type="bool", default=False),
            ),
        ),
    )

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        return "Projects live in this installation."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        from ..db import db_session

        with db_session() as db:
            if widget_kind == "roadmap":
                return _roadmap(db, options, date.today())
            if widget_kind == "project":
                return _project(db, options, date.today())
            if widget_kind == "items":
                return _items(db, options)
        return WidgetData(status="warn", meta={"empty": "No such card"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        today = date.today()
        milestones = [
            {"id": 1, "title": "Fill the screen", "project": "HexDeck", "colour": "#3aa0ff",
             "date": (today - timedelta(days=2)).isoformat(), "days": -2, "status": "late"},
            {"id": 2, "title": "Projects and roadmap", "project": "HexDeck", "colour": "#3aa0ff",
             "date": (today + timedelta(days=9)).isoformat(), "days": 9, "status": "soon"},
            {"id": 3, "title": "GitHub adapter", "project": "HexDeck", "colour": "#3aa0ff",
             "date": (today + timedelta(days=40)).isoformat(), "days": 40, "status": "open"},
        ]
        if widget_kind == "roadmap":
            return WidgetData(status="bad", items=milestones, primary={"label": "Due in 4 weeks", "value": 1},
                              meta={"today": today.isoformat(), "weeks": 4})
        if widget_kind == "project":
            return WidgetData(status="ok", primary={"label": "Done", "value": 50, "unit": "%"},
                              secondary=[{"label": "Items", "value": "3 / 6"}, {"label": "Milestones open", "value": 3}],
                              items=[{"repo": "HexLions/hexdeck", "url": "https://github.com/HexLions/hexdeck"}],
                              meta={"name": "HexDeck", "colour": "#3aa0ff", "status": "active", "next": milestones[1]})
        items = [
            {"id": 1, "title": "Columns per board", "notes": "", "status": "done", "milestone": "Fill the screen", "issue": "", "url": ""},
            {"id": 2, "title": "Fit to screen", "notes": "", "status": "done", "milestone": "Fill the screen", "issue": "", "url": ""},
            {"id": 3, "title": "Roadmap card", "notes": "", "status": "doing", "milestone": "Projects and roadmap",
             "issue": "HexLions/hexdeck#7", "url": "https://github.com/HexLions/hexdeck/issues/7"},
            {"id": 4, "title": "Items card", "notes": "", "status": "todo", "milestone": "Projects and roadmap", "issue": "", "url": ""},
            {"id": 5, "title": "Settings page", "notes": "", "status": "todo", "milestone": "Projects and roadmap", "issue": "", "url": ""},
            {"id": 6, "title": "Rate limit and ETag", "notes": "", "status": "todo", "milestone": "GitHub adapter", "issue": "", "url": ""},
        ]
        return WidgetData(status="ok", items=items, meta={"project_id": 0, "name": "HexDeck", "demo": True})


def _grade(milestone: Any, today: date, weeks: int) -> tuple[str, int | None]:
    """``late``, ``soon``, ``open`` or ``done``, and the whole days from today."""
    if milestone.status == "done":
        return "done", None
    if milestone.target_date is None:
        return "open", None
    days = (milestone.target_date - today).days
    if days < 0:
        return "late", days
    if days <= weeks * 7:
        return "soon", days
    return "open", days


def _roadmap(db: Any, options: dict[str, Any], today: date) -> WidgetData:
    from ..models import Milestone, Project

    weeks = int(options.get("weeks") or 4)
    only = str(options.get("project") or "")
    query = select(Milestone, Project).join(Project, Milestone.project_id == Project.id)
    if only.isdigit():
        query = query.where(Project.id == int(only))
    rows: list[dict[str, Any]] = []
    for milestone, project in db.execute(query):
        status, days = _grade(milestone, today, weeks)
        if status == "done" and not options.get("done"):
            continue
        rows.append({"id": milestone.id, "title": milestone.title, "project": project.name, "colour": project.colour,
                     "date": milestone.target_date.isoformat() if milestone.target_date else None, "days": days, "status": status})
    # By date; the undated ones last, and among them by project then title.
    rows.sort(key=lambda r: (r["date"] is None, r["date"] or "", r["project"], r["title"]))
    due = sum(1 for r in rows if r["status"] == "soon")
    late = any(r["status"] == "late" for r in rows)
    return WidgetData(
        status="bad" if late else "warn" if due else "ok",
        items=rows,
        primary={"label": f"Due in {weeks} weeks", "value": due},
        meta={"today": today.isoformat(), "weeks": weeks, "empty": "No milestones yet"},
    )


def _project(db: Any, options: dict[str, Any], today: date) -> WidgetData:
    from ..models import Project

    chosen = str(options.get("project") or "")
    project = db.get(Project, int(chosen)) if chosen.isdigit() else None
    if project is None:
        return WidgetData(status="warn", meta={"empty": "Pick a project in the card settings"})
    total = len(project.items)
    done = sum(1 for i in project.items if i.status == "done")
    open_ones = [m for m in project.milestones if m.status != "done"]
    dated = sorted((m for m in open_ones if m.target_date), key=lambda m: m.target_date)
    nxt = None
    if dated:
        status, days = _grade(dated[0], today, 4)
        nxt = {"id": dated[0].id, "title": dated[0].title, "date": dated[0].target_date.isoformat(), "days": days, "status": status}
    return WidgetData(
        status="bad" if nxt and nxt["status"] == "late" else "ok",
        primary={"label": "Done", "value": round(100 * done / total) if total else 0, "unit": "%"},
        secondary=[{"label": "Items", "value": f"{done} / {total}"}, {"label": "Milestones open", "value": len(open_ones)}],
        items=[{"repo": r.repo, "url": f"https://github.com/{r.repo}"} for r in project.repos],
        meta={"name": project.name, "colour": project.colour, "status": project.status, "description": project.description, "next": nxt},
    )


def _items(db: Any, options: dict[str, Any]) -> WidgetData:
    from ..models import Project
    from ..services.projects import issue_url

    chosen = str(options.get("project") or "")
    project = db.get(Project, int(chosen)) if chosen.isdigit() else None
    if project is None:
        return WidgetData(status="warn", meta={"empty": "Pick a project in the card settings"})
    only = str(options.get("milestone") or "")
    names = {m.id: m.title for m in project.milestones}
    rows = []
    for item in sorted(project.items, key=lambda i: (i.position, i.id)):
        if only.isdigit() and item.milestone_id != int(only):
            continue
        if options.get("hide_done") and item.status == "done":
            continue
        rows.append({"id": item.id, "title": item.title, "notes": item.notes, "status": item.status,
                     "milestone": names.get(item.milestone_id or 0, ""), "issue": item.issue, "url": issue_url(item.issue)})
    return WidgetData(status="ok", items=rows, meta={"project_id": project.id, "name": project.name, "empty": "Nothing to do"})


ADAPTER = ProjectsAdapter()
