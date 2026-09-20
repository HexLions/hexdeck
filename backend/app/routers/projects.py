"""Projects, milestones and items: what is being built, in HexDeck's own database.

Projects are global to the installation. Administrators and users change
them, guests only look, and the cards on a board show what the board's
viewers may see. GitHub is a reference on an item, never written to.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from sqlalchemy import select

from ..deps import CurrentUser, DbSession, MemberUser, error
from ..models import Milestone, Project, ProjectItem, ProjectRepo
from ..schemas import (
    ItemCreate,
    ItemOrder,
    ItemPatch,
    MilestoneCreate,
    MilestonePatch,
    ProjectCreate,
    ProjectPatch,
    RepoCreate,
)
from ..services.projects import (
    is_issue,
    is_repo,
    item_view,
    milestone_view,
    next_position,
    project_view,
    repo_view,
    reschedule_cards,
    slug_for,
)

router = APIRouter(prefix="/api/v1", tags=["projects"])
logger = logging.getLogger("hexdeck.projects")


def _project(db: DbSession, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise error("not_found", "There is no such project.", status.HTTP_404_NOT_FOUND)
    return project


@router.get("/projects", summary="Every project with its repositories, milestones and items")
def list_projects(user: CurrentUser, db: DbSession) -> list[dict]:
    return [project_view(p) for p in db.scalars(select(Project).order_by(Project.position, Project.id))]


@router.post("/projects", status_code=status.HTTP_201_CREATED, summary="Create a project")
def create_project(body: ProjectCreate, user: MemberUser, db: DbSession) -> dict:
    rows = list(db.scalars(select(Project)))
    project = Project(name=body.name.strip(), slug=slug_for(db, body.name), description=body.description, status=body.status,
                      colour=body.colour.lower(), position=next_position(rows))
    db.add(project)
    db.commit()
    logger.info("Project %r created by %s.", project.name, user.username)
    reschedule_cards(db)
    return project_view(project)


@router.patch("/projects/{project_id}", summary="Change a project")
def patch_project(project_id: int, body: ProjectPatch, user: MemberUser, db: DbSession) -> dict:
    project = _project(db, project_id)
    if body.name is not None:
        project.name = body.name.strip()
    if body.description is not None:
        project.description = body.description
    if body.status is not None:
        project.status = body.status
    if body.colour is not None:
        project.colour = body.colour.lower()
    if body.position is not None:
        project.position = body.position
    db.commit()
    reschedule_cards(db)
    return project_view(project)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a project and everything in it")
def delete_project(project_id: int, user: MemberUser, db: DbSession) -> None:
    project = _project(db, project_id)
    logger.info("Project %r deleted by %s, with %d milestone(s) and %d item(s).", project.name, user.username, len(project.milestones), len(project.items))
    db.delete(project)
    db.commit()
    reschedule_cards(db)


@router.post("/projects/{project_id}/repos", status_code=status.HTTP_201_CREATED, summary="Link a repository")
def add_repo(project_id: int, body: RepoCreate, user: MemberUser, db: DbSession) -> dict:
    project = _project(db, project_id)
    repo = body.repo.strip()
    if not is_repo(repo):
        raise error("bad_repo", "A repository is written owner/name.")
    row = ProjectRepo(project_id=project.id, repo=repo, position=next_position(project.repos))
    db.add(row)
    db.commit()
    reschedule_cards(db)
    return repo_view(row)


@router.delete("/projects/{project_id}/repos/{repo_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Unlink a repository")
def remove_repo(project_id: int, repo_id: int, user: MemberUser, db: DbSession) -> None:
    row = db.get(ProjectRepo, repo_id)
    if row is None or row.project_id != project_id:
        raise error("not_found", "There is no such repository.", status.HTTP_404_NOT_FOUND)
    db.delete(row)
    db.commit()
    reschedule_cards(db)


@router.post("/projects/{project_id}/milestones", status_code=status.HTTP_201_CREATED, summary="Add a milestone")
def add_milestone(project_id: int, body: MilestoneCreate, user: MemberUser, db: DbSession) -> dict:
    project = _project(db, project_id)
    row = Milestone(project_id=project.id, title=body.title.strip(), target_date=body.target_date, status=body.status,
                    position=next_position(project.milestones))
    db.add(row)
    db.commit()
    reschedule_cards(db)
    return milestone_view(row)


@router.patch("/milestones/{milestone_id}", summary="Change a milestone")
def patch_milestone(milestone_id: int, body: MilestonePatch, user: MemberUser, db: DbSession) -> dict:
    row = db.get(Milestone, milestone_id)
    if row is None:
        raise error("not_found", "There is no such milestone.", status.HTTP_404_NOT_FOUND)
    if body.title is not None:
        row.title = body.title.strip()
    if body.clear_date:
        row.target_date = None
    elif body.target_date is not None:
        row.target_date = body.target_date
    if body.status is not None:
        row.status = body.status
    if body.position is not None:
        row.position = body.position
    db.commit()
    reschedule_cards(db)
    return milestone_view(row)


@router.delete("/milestones/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a milestone; its items stay")
def delete_milestone(milestone_id: int, user: MemberUser, db: DbSession) -> None:
    row = db.get(Milestone, milestone_id)
    if row is None:
        raise error("not_found", "There is no such milestone.", status.HTTP_404_NOT_FOUND)
    # By hand as well as by the foreign key: SQLite only honours SET NULL with
    # the pragma on, and an item must never point at a milestone that is gone.
    for item in db.scalars(select(ProjectItem).where(ProjectItem.milestone_id == row.id)):
        item.milestone_id = None
    db.delete(row)
    db.commit()
    reschedule_cards(db)


@router.post("/projects/{project_id}/items", status_code=status.HTTP_201_CREATED, summary="Add an item")
def add_item(project_id: int, body: ItemCreate, user: MemberUser, db: DbSession) -> dict:
    project = _project(db, project_id)
    if not is_issue(body.issue.strip()):
        raise error("bad_issue", "An issue is written owner/name#123.")
    if body.milestone_id is not None and not any(m.id == body.milestone_id for m in project.milestones):
        raise error("bad_milestone", "That milestone is not part of this project.")
    row = ProjectItem(project_id=project.id, milestone_id=body.milestone_id, title=body.title.strip(), notes=body.notes,
                      status=body.status, issue=body.issue.strip(), position=next_position(project.items))
    db.add(row)
    db.commit()
    reschedule_cards(db)
    return item_view(row)


@router.patch("/items/{item_id}", summary="Change an item")
def patch_item(item_id: int, body: ItemPatch, user: MemberUser, db: DbSession) -> dict:
    row = db.get(ProjectItem, item_id)
    if row is None:
        raise error("not_found", "There is no such item.", status.HTTP_404_NOT_FOUND)
    if body.issue is not None and not is_issue(body.issue.strip()):
        raise error("bad_issue", "An issue is written owner/name#123.")
    if body.milestone_id is not None and not any(m.id == body.milestone_id for m in row.project.milestones):
        raise error("bad_milestone", "That milestone is not part of this project.")
    if body.title is not None:
        row.title = body.title.strip()
    if body.notes is not None:
        row.notes = body.notes
    if body.status is not None:
        row.status = body.status
    if body.clear_milestone:
        row.milestone_id = None
    elif body.milestone_id is not None:
        row.milestone_id = body.milestone_id
    if body.issue is not None:
        row.issue = body.issue.strip()
    db.commit()
    reschedule_cards(db)
    return item_view(row)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an item")
def delete_item(item_id: int, user: MemberUser, db: DbSession) -> None:
    row = db.get(ProjectItem, item_id)
    if row is None:
        raise error("not_found", "There is no such item.", status.HTTP_404_NOT_FOUND)
    db.delete(row)
    db.commit()
    reschedule_cards(db)


@router.put("/projects/{project_id}/items/order", summary="Put the items in this order")
def order_items(project_id: int, body: ItemOrder, user: MemberUser, db: DbSession) -> dict:
    """The listed ids come first, in that order; the rest follow in the order they had."""
    project = _project(db, project_id)
    by_id = {item.id: item for item in project.items}
    wanted = [by_id[i] for i in body.ids if i in by_id]
    rest = [item for item in sorted(project.items, key=lambda i: (i.position, i.id)) if item not in wanted]
    for position, item in enumerate([*wanted, *rest]):
        item.position = position
    db.commit()
    db.refresh(project)
    reschedule_cards(db)
    return project_view(project)
