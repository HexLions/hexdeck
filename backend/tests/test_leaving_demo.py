"""Leaving demo mode takes the invented data with it: the demo connections,
the cards that read them, and the boards that were nothing but those.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import CSRF, create_user, login, setup_admin


def _widget(client: TestClient, page_id: int, kind: str, integration_id: int | None = None, options: dict | None = None) -> dict:
    made = client.post(f"/api/v1/pages/{page_id}/widgets", json={"kind": kind, "integration_id": integration_id, "options": options or {}}, headers=CSRF)
    assert made.status_code == 201, made.text
    return made.json()["widget"]


def test_leaving_demo_mode_removes_the_demo_connections_their_cards_and_their_boards(client: TestClient) -> None:
    setup_admin(client, demo=True)
    assert client.get("/api/v1/about").json()["demo"] is True
    boards = client.get("/api/v1/boards").json()
    demo_board = next(b for b in boards if b["slug"] == "home")
    connections = client.get("/api/v1/integrations").json()
    assert any(c["demo"] for c in connections), "the demo made connections"
    demo_radarr = next(c for c in connections if c["demo"] and c["kind"] == "radarr")

    # A board of the administrator's own, with a real connection, a demo card and a plain card.
    real = client.post("/api/v1/integrations", json={"kind": "docker", "name": "Docker", "config": {"host": "unix:///var/run/docker.sock"}}, headers=CSRF).json()
    mine = client.post("/api/v1/boards", json={"name": "Mine"}, headers=CSRF).json()
    page = mine["pages"][0]["id"]
    kept_real = _widget(client, page, "docker.summary", real["id"])
    gone_demo = _widget(client, page, "radarr.queue", demo_radarr["id"])
    kept_plain = _widget(client, page, "core.clock")
    # And a board that is only demo cards, like the one the demo made.
    only = client.post("/api/v1/boards", json={"name": "Only demo"}, headers=CSRF).json()
    _widget(client, only["pages"][0]["id"], "radarr.queue", demo_radarr["id"])
    _widget(client, only["pages"][0]["id"], "core.clock")

    left = client.post("/api/v1/settings/demo/leave", headers=CSRF)
    assert left.status_code == 200, left.text
    summary = left.json()
    assert summary["integrations_removed"] == sum(1 for c in connections if c["demo"])
    assert summary["boards_removed"] == 2, "the demo's own board and the one that was only demo cards"
    assert summary["widgets_removed"] >= 1

    assert client.get("/api/v1/about").json()["demo"] is False
    assert all(not c["demo"] for c in client.get("/api/v1/integrations").json())
    slugs = {b["slug"] for b in client.get("/api/v1/boards").json()}
    assert demo_board["slug"] not in slugs and only["slug"] not in slugs and mine["slug"] in slugs
    kinds = {w["id"] for w in client.get(f"/api/v1/boards/{mine['slug']}").json()["pages"][0]["widgets"]}
    assert kept_real["id"] in kinds and kept_plain["id"] in kinds and gone_demo["id"] not in kinds


def test_leaving_demo_mode_without_a_demo_changes_nothing_but_the_flag(client: TestClient) -> None:
    setup_admin(client)
    client.patch("/api/v1/settings", json={"demo": True}, headers=CSRF)
    before = {b["slug"] for b in client.get("/api/v1/boards").json()}
    summary = client.post("/api/v1/settings/demo/leave", headers=CSRF).json()
    assert summary == {"integrations_removed": 0, "widgets_removed": 0, "boards_removed": 0}
    assert client.get("/api/v1/about").json()["demo"] is False
    assert {b["slug"] for b in client.get("/api/v1/boards").json()} == before


def test_only_an_administrator_leaves_demo_mode(client: TestClient) -> None:
    setup_admin(client, demo=True)
    create_user(client, "kim")
    login(client, "kim", "another-long-password")
    assert client.post("/api/v1/settings/demo/leave", headers=CSRF).status_code == 403
