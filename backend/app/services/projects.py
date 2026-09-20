"""Projects, milestones and items: the shapes the router and the adapter share."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Milestone, Project, ProjectItem, ProjectRepo, Widget
from .boards import slugify

REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
ISSUE = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+)$")


def is_repo(text: str) -> bool:
    return bool(REPO.match(text))


def is_issue(text: str) -> bool:
    return text == "" or bool(ISSUE.match(text))


def issue_url(issue: str) -> str:
    found = ISSUE.match(issue)
    return f"https://github.com/{found.group(1)}/issues/{found.group(2)}" if found else ""


def slug_for(db: Session, name: str) -> str:
    base = slugify(name)
    slug, counter = base, 2
    while db.scalar(select(Project).where(Project.slug == slug)) is not None:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def repo_view(repo: ProjectRepo) -> dict[str, Any]:
    return {"id": repo.id, "repo": repo.repo, "url": f"https://github.com/{repo.repo}", "position": repo.position}


def milestone_view(milestone: Milestone) -> dict[str, Any]:
    return {"id": milestone.id, "title": milestone.title, "status": milestone.status, "position": milestone.position,
            "target_date": milestone.target_date.isoformat() if milestone.target_date else None}


def item_view(item: ProjectItem) -> dict[str, Any]:
    return {"id": item.id, "milestone_id": item.milestone_id, "title": item.title, "notes": item.notes, "status": item.status,
            "issue": item.issue, "url": issue_url(item.issue), "position": item.position,
            "due_on": item.due_on.isoformat() if item.due_on else None, "repeat_days": item.repeat_days or 0,
            "last_done": item.last_done.isoformat() if item.last_done else None}


def tick(item: ProjectItem, today: date) -> None:
    """Mark an item done. A recurring one is done for now: its date moves on
    by the interval, counted from the date that was due (a tick a day late
    does not drift the schedule) until it is in the future, and it goes back
    to do. A one-off item finishes."""
    item.last_done = today
    if not item.repeat_days:
        item.status = "done"
        return
    due = item.due_on or today
    while due <= today:
        due += timedelta(days=item.repeat_days)
    item.due_on = due
    item.status = "todo"


def announce_due(db: Session, today: date) -> int:
    """Tell about every open item that is due today or late, once a day. Returns how many."""
    from . import notify

    count = 0
    rows = db.scalars(select(ProjectItem).where(
        ProjectItem.due_on.is_not(None), ProjectItem.due_on <= today, ProjectItem.status != "done",
    ))
    for item in rows:
        if item.announced_on == today:
            continue
        late = (today - item.due_on).days
        when = "due today" if late == 0 else f"{late} day{'s' if late != 1 else ''} late"
        notify.emit("maintenance_due", item.title, f"{item.project.name} · {when}", level="warning" if late else "info")
        item.announced_on = today
        count += 1
    db.commit()
    return count


def project_view(project: Project) -> dict[str, Any]:
    return {
        "id": project.id, "name": project.name, "slug": project.slug, "description": project.description,
        "status": project.status, "colour": project.colour, "position": project.position,
        "repos": [repo_view(r) for r in project.repos],
        "milestones": [milestone_view(m) for m in project.milestones],
        "items": [item_view(i) for i in sorted(project.items, key=lambda i: (i.position, i.id))],
    }


def next_position(rows: list[Any]) -> int:
    return max((row.position for row in rows), default=-1) + 1


def reschedule_cards(db: Session) -> None:
    """Every project card fetches again, so a change on the page shows on the boards."""
    from .collector import collector

    for widget_id in db.scalars(select(Widget.id).where(Widget.kind.like("projects.%"))):
        collector.schedule(widget_id)
