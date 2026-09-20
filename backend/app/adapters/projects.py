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
                Field("items", "Show dated items", type="bool", default=True,
                      help="Items with a due date, maintenance that comes back among them, next to the milestones."),
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
            description="The items of a project, to tick off and reorder on the board; the dated ones say when they are due.",
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
                return _items(db, options, date.today())
        return WidgetData(status="warn", meta={"empty": "No such card"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        today = date.today()
        milestones = [
            {"id": 1, "kind": "milestone", "title": "Fill the screen", "project": "HexDeck", "project_id": 0, "colour": "#3aa0ff",
             "date": (today - timedelta(days=2)).isoformat(), "days": -2, "status": "late", "repeat_days": 0},
            {"id": 7, "kind": "item", "title": "Test the backup restore", "project": "Homelab", "project_id": 0, "colour": "#2fb46a",
             "date": today.isoformat(), "days": 0, "status": "soon", "repeat_days": 30},
            {"id": 2, "kind": "milestone", "title": "Projects and roadmap", "project": "HexDeck", "project_id": 0, "colour": "#3aa0ff",
             "date": (today + timedelta(days=9)).isoformat(), "days": 9, "status": "soon", "repeat_days": 0},
            {"id": 8, "kind": "item", "title": "Renew the certificate", "project": "Homelab", "project_id": 0, "colour": "#2fb46a",
             "date": (today + timedelta(days=21)).isoformat(), "days": 21, "status": "soon", "repeat_days": 90},
            {"id": 3, "kind": "milestone", "title": "GitHub adapter", "project": "HexDeck", "project_id": 0, "colour": "#3aa0ff",
             "date": (today + timedelta(days=40)).isoformat(), "days": 40, "status": "open", "repeat_days": 0},
        ]
        if widget_kind == "roadmap":
            return WidgetData(status="bad", items=milestones, primary={"label": "Due in 4 weeks", "value": 3},
                              meta={"today": today.isoformat(), "weeks": 4, "projects": [], "demo": True})
        if widget_kind == "project":
            return WidgetData(status="ok", primary={"label": "Done", "value": 50, "unit": "%"},
                              secondary=[{"label": "Items", "value": "3 / 6"}, {"label": "Milestones open", "value": 3}],
                              items=[{"repo": "HexLions/hexdeck", "url": "https://github.com/HexLions/hexdeck"}],
                              meta={"name": "HexDeck", "colour": "#3aa0ff", "status": "active", "next": milestones[1], "demo": True})
        blank = {"notes": "", "issue": "", "url": "", "due_on": None, "days": None, "repeat_days": 0, "due": "open"}
        items = [
            {**blank, "id": 1, "title": "Columns per board", "status": "done", "milestone": "Fill the screen", "due": "done"},
            {**blank, "id": 2, "title": "Fit to screen", "status": "done", "milestone": "Fill the screen", "due": "done"},
            {**blank, "id": 3, "title": "Roadmap card", "status": "doing", "milestone": "Projects and roadmap",
             "issue": "HexLions/hexdeck#7", "url": "https://github.com/HexLions/hexdeck/issues/7"},
            {**blank, "id": 4, "title": "Items card", "status": "todo", "milestone": "Projects and roadmap"},
            {**blank, "id": 5, "title": "Test the backup restore", "status": "todo", "milestone": "",
             "due_on": today.isoformat(), "days": 0, "repeat_days": 30, "due": "soon"},
            {**blank, "id": 6, "title": "Rate limit and ETag", "status": "todo", "milestone": "GitHub adapter"},
        ]
        return WidgetData(status="ok", items=items, meta={"project_id": 0, "name": "HexDeck", "demo": True})


def _grade(milestone: Any, today: date, weeks: int) -> tuple[str, int | None]:
    """``late``, ``soon``, ``open`` or ``done``, and the whole days from today."""
    return _grade_date(milestone.status, milestone.target_date, today, weeks)


def _grade_date(status: str, when: date | None, today: date, weeks: int) -> tuple[str, int | None]:
    if status == "done":
        return "done", None
    if when is None:
        return "open", None
    days = (when - today).days
    if days < 0:
        return "late", days
    if days <= weeks * 7:
        return "soon", days
    return "open", days


def _roadmap(db: Any, options: dict[str, Any], today: date) -> WidgetData:
    from ..models import Milestone, Project, ProjectItem

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
        rows.append({"id": milestone.id, "kind": "milestone", "title": milestone.title, "project": project.name,
                     "project_id": project.id, "colour": project.colour,
                     "date": milestone.target_date.isoformat() if milestone.target_date else None,
                     "days": days, "status": status, "repeat_days": 0})
    # The dated items sit among the milestones: maintenance that comes back is
    # never done, only done for now, so it stays; a finished one-off is gone.
    if options.get("items", True):
        items_query = select(ProjectItem, Project).join(Project, ProjectItem.project_id == Project.id).where(
            ProjectItem.due_on.is_not(None), ProjectItem.status != "done",
        )
        if only.isdigit():
            items_query = items_query.where(Project.id == int(only))
        for item, project in db.execute(items_query):
            status, days = _grade_date(item.status, item.due_on, today, weeks)
            rows.append({"id": item.id, "kind": "item", "title": item.title, "project": project.name,
                         "project_id": project.id, "colour": project.colour, "date": item.due_on.isoformat(),
                         "days": days, "status": status, "repeat_days": item.repeat_days or 0})
    # By date; the undated ones last, and among them by project then title.
    rows.sort(key=lambda r: (r["date"] is None, r["date"] or "", r["project"], r["title"]))
    due = sum(1 for r in rows if r["status"] == "soon")
    late = any(r["status"] == "late" for r in rows)
    # The projects a milestone may be added to from the card: the chosen one, or all.
    projects_query = select(Project).order_by(Project.position, Project.id)
    if only.isdigit():
        projects_query = projects_query.where(Project.id == int(only))
    projects = [{"id": p.id, "name": p.name} for p in db.scalars(projects_query)]
    return WidgetData(
        status="bad" if late else "warn" if due else "ok",
        items=rows,
        primary={"label": f"Due in {weeks} weeks", "value": due},
        meta={"today": today.isoformat(), "weeks": weeks, "projects": projects, "empty": "No milestones yet"},
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
        meta={"project_id": project.id, "name": project.name, "colour": project.colour, "status": project.status,
              "description": project.description, "next": nxt},
    )


def _items(db: Any, options: dict[str, Any], today: date) -> WidgetData:
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
        due, days = _grade_date(item.status, item.due_on, today, 2)
        rows.append({"id": item.id, "title": item.title, "notes": item.notes, "status": item.status,
                     "milestone": names.get(item.milestone_id or 0, ""), "issue": item.issue, "url": issue_url(item.issue),
                     "due_on": item.due_on.isoformat() if item.due_on else None, "days": days,
                     "repeat_days": item.repeat_days or 0, "due": due})
    late = any(r["due"] == "late" for r in rows)
    return WidgetData(status="bad" if late else "ok", items=rows,
                      meta={"project_id": project.id, "name": project.name, "empty": "Nothing to do"})


ADAPTER = ProjectsAdapter()
