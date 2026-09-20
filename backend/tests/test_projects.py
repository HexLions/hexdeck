"""Projects, milestones and items live in HexDeck's own database."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import db_session
from app.models import Milestone, Project, ProjectItem, ProjectRepo

from .conftest import CSRF, create_user, login, setup_admin


def test_a_project_holds_repos_milestones_and_items_and_takes_them_along(client: TestClient) -> None:
    with db_session() as db:
        project = Project(name="HexDeck", slug="hexdeck", description="", status="active", colour="#3aa0ff", position=0)
        db.add(project)
        db.flush()
        db.add(ProjectRepo(project_id=project.id, repo="HexLions/hexdeck", position=0))
        milestone = Milestone(project_id=project.id, title="M3", target_date=date(2026, 10, 1), status="open", position=0)
        db.add(milestone)
        db.flush()
        db.add(ProjectItem(project_id=project.id, milestone_id=milestone.id, title="Roadmap card", notes="", status="todo", issue="", position=0))
        db.commit()
        loaded = db.scalar(select(Project).where(Project.slug == "hexdeck"))
        assert [r.repo for r in loaded.repos] == ["HexLions/hexdeck"]
        assert loaded.milestones[0].target_date == date(2026, 10, 1)
        assert loaded.items[0].milestone_id == milestone.id
        db.delete(milestone)
        db.commit()
        db.expire_all()
        assert db.scalar(select(ProjectItem)).milestone_id is None, "an item outlives its milestone"
        db.delete(db.scalar(select(Project)))
        db.commit()
        assert db.scalar(select(ProjectItem)) is None and db.scalar(select(ProjectRepo)) is None


def _project(client: TestClient, name: str = "HexDeck") -> dict:
    answer = client.post("/api/v1/projects", json={"name": name, "colour": "#3aa0ff"}, headers=CSRF)
    assert answer.status_code == 201, answer.text
    return answer.json()


def test_a_project_is_created_listed_changed_and_deleted(client: TestClient) -> None:
    setup_admin(client)
    project = _project(client)
    assert project["slug"] == "hexdeck" and project["status"] == "active" and project["repos"] == []
    assert client.patch(f"/api/v1/projects/{project['id']}", json={"status": "paused", "description": "the fork"}, headers=CSRF).json()["status"] == "paused"
    listed = client.get("/api/v1/projects").json()
    assert [p["name"] for p in listed] == ["HexDeck"]
    assert client.delete(f"/api/v1/projects/{project['id']}", headers=CSRF).status_code == 204
    assert client.get("/api/v1/projects").json() == []


def test_repos_milestones_and_items_hang_off_the_project(client: TestClient) -> None:
    setup_admin(client)
    project = _project(client)
    pid = project["id"]
    repo = client.post(f"/api/v1/projects/{pid}/repos", json={"repo": "HexLions/hexdeck"}, headers=CSRF)
    assert repo.status_code == 201 and repo.json()["url"] == "https://github.com/HexLions/hexdeck"
    assert client.post(f"/api/v1/projects/{pid}/repos", json={"repo": "not a repo"}, headers=CSRF).status_code == 400
    milestone = client.post(f"/api/v1/projects/{pid}/milestones", json={"title": "M3", "target_date": "2026-10-01"}, headers=CSRF).json()
    item = client.post(f"/api/v1/projects/{pid}/items", json={"title": "Roadmap card", "milestone_id": milestone["id"], "issue": "HexLions/hexdeck#7"}, headers=CSRF).json()
    assert item["url"] == "https://github.com/HexLions/hexdeck/issues/7"
    assert client.post(f"/api/v1/projects/{pid}/items", json={"title": "x", "issue": "nonsense"}, headers=CSRF).status_code == 400
    assert client.patch(f"/api/v1/items/{item['id']}", json={"status": "doing"}, headers=CSRF).json()["status"] == "doing"
    assert client.patch(f"/api/v1/milestones/{milestone['id']}", json={"clear_date": True}, headers=CSRF).json()["target_date"] is None
    assert client.delete(f"/api/v1/milestones/{milestone['id']}", headers=CSRF).status_code == 204
    view = client.get("/api/v1/projects").json()[0]
    assert view["items"][0]["milestone_id"] is None, "the item outlives its milestone"


def test_items_are_reordered_by_a_list_of_ids(client: TestClient) -> None:
    setup_admin(client)
    pid = _project(client)["id"]
    ids = [client.post(f"/api/v1/projects/{pid}/items", json={"title": t}, headers=CSRF).json()["id"] for t in ("a", "b", "c")]
    answer = client.put(f"/api/v1/projects/{pid}/items/order", json={"ids": [ids[2], ids[0]]}, headers=CSRF)
    assert answer.status_code == 200
    assert [i["title"] for i in answer.json()["items"]] == ["c", "a", "b"], "listed ones first, the rest keep their order"


def test_a_guest_reads_and_may_not_write(client: TestClient) -> None:
    setup_admin(client)
    pid = _project(client)["id"]
    create_user(client, "kim", role="guest")
    guest = TestClient(client.app)
    login(guest, "kim", "another-long-password")
    assert guest.get("/api/v1/projects").status_code == 200
    assert guest.post("/api/v1/projects", json={"name": "Nope"}, headers=CSRF).status_code == 403
    assert guest.patch(f"/api/v1/projects/{pid}", json={"name": "Nope"}, headers=CSRF).status_code == 403


def test_a_write_reschedules_the_project_cards(client: TestClient, monkeypatch) -> None:
    from app.services.collector import collector

    scheduled: list[int] = []
    monkeypatch.setattr(collector, "schedule", lambda widget_id: scheduled.append(widget_id))
    setup_admin(client)
    board = client.post("/api/v1/boards", json={"name": "Lab"}, headers=CSRF).json()
    widget = client.post(f"/api/v1/pages/{board['pages'][0]['id']}/widgets", json={"kind": "projects.roadmap"}, headers=CSRF).json()["widget"]
    scheduled.clear()
    _project(client)
    assert widget["id"] in scheduled


def test_a_recurring_item_rolls_over_when_it_is_done(client: TestClient) -> None:
    """Maintenance: an item with a due date and an interval. Ticking it off
    moves the date forward by the interval and puts it back to do."""
    from datetime import date, timedelta

    setup_admin(client)
    pid = _project(client)["id"]
    today = date.today()
    item = client.post(f"/api/v1/projects/{pid}/items", json={"title": "Test the backup restore", "due_on": (today - timedelta(days=2)).isoformat(), "repeat_days": 30}, headers=CSRF).json()
    assert item["repeat_days"] == 30 and item["due_on"] == (today - timedelta(days=2)).isoformat()
    done = client.patch(f"/api/v1/items/{item['id']}", json={"status": "done"}, headers=CSRF).json()
    assert done["status"] == "todo", "a recurring item is never finished, only done for now"
    assert done["due_on"] == (today + timedelta(days=28)).isoformat(), "the next date counts from the one that was due, not from today"
    assert done["last_done"] == today.isoformat()
    # A one-off item with a date simply finishes.
    once = client.post(f"/api/v1/projects/{pid}/items", json={"title": "Replace the UPS battery", "due_on": today.isoformat()}, headers=CSRF).json()
    assert client.patch(f"/api/v1/items/{once['id']}", json={"status": "done"}, headers=CSRF).json()["status"] == "done"
    # A date that fell far behind rolls forward until it is in the future.
    stale = client.post(f"/api/v1/projects/{pid}/items", json={"title": "Old", "due_on": (today - timedelta(days=100)).isoformat(), "repeat_days": 30}, headers=CSRF).json()
    rolled = client.patch(f"/api/v1/items/{stale['id']}", json={"status": "done"}, headers=CSRF).json()
    assert date.fromisoformat(rolled["due_on"]) > today
    assert client.patch(f"/api/v1/items/{item['id']}", json={"clear_due": True}, headers=CSRF).json()["due_on"] is None


def test_items_that_are_due_are_announced_once_a_day(client: TestClient, monkeypatch) -> None:
    from datetime import date, timedelta

    from app.services import notify
    from app.services import projects as project_service

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "emit", lambda event, title, body="", **kw: sent.append((event, title)))
    setup_admin(client)
    pid = _project(client)["id"]
    today = date.today()
    client.post(f"/api/v1/projects/{pid}/items", json={"title": "Renew the certificate", "due_on": today.isoformat(), "repeat_days": 90}, headers=CSRF)
    client.post(f"/api/v1/projects/{pid}/items", json={"title": "Later", "due_on": (today + timedelta(days=5)).isoformat()}, headers=CSRF)
    client.post(f"/api/v1/projects/{pid}/items", json={"title": "Late", "due_on": (today - timedelta(days=3)).isoformat()}, headers=CSRF)
    with db_session() as db:
        assert project_service.announce_due(db, today) == 2
        assert project_service.announce_due(db, today) == 0, "the same day announces once"
    assert sorted(title for event, title in sent if event == "maintenance_due") == ["Late", "Renew the certificate"]
