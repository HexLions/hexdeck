"""Board templates: ready-made boards whose placeholder connections are mapped
to the real ones on installation, or left out with their cards.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters import split_widget_kind
from app.services import templates as template_service
from app.services.layout import scale_size

from .conftest import CSRF, create_user, login, setup_admin


@pytest.mark.parametrize("template_id", [t["id"] for t in template_service.list_templates()])
def test_every_template_is_sound(template_id: str) -> None:
    """Every card is a known kind with known options, big enough for its
    renderer, inside the grid, on a connection the template declares."""
    document = template_service.get_template(template_id)
    meta = document["template"]
    assert meta["id"] == template_id and meta["name"] and meta["description"] and meta["tags"]
    columns = document["board"]["settings"]["columns"]
    declared = {entry["name"]: entry["kind"] for entry in document["integrations"]}
    assert len(declared) == len(document["integrations"]), "connection names are unique"
    for page in document["pages"]:
        cells: set[tuple[int, int]] = set()
        for widget in page["widgets"]:
            adapter, widget_kind = split_widget_kind(widget["kind"])
            widget_type = adapter.widget(widget_kind)
            known = {field.name for field in widget_type.options}
            unknown = set(widget.get("options") or {}) - known
            assert not unknown, f"{widget['kind']} has no option {sorted(unknown)}"
            if adapter.needs_integration:
                assert widget.get("integration") in declared, f"{widget['kind']} needs a declared connection"
                assert declared[widget["integration"]] == adapter.kind
            spot = widget["layout"]["lg"]
            min_w, min_h = scale_size(widget_type.min_size, columns)
            assert spot["w"] >= min_w and spot["h"] >= min_h, f"{widget['kind']} is smaller than its floor"
            assert spot["x"] + spot["w"] <= columns, f"{widget['kind']} sticks out of the grid"
            for x in range(spot["x"], spot["x"] + spot["w"]):
                for y in range(spot["y"], spot["y"] + spot["h"]):
                    assert (x, y) not in cells, f"{widget['kind']} overlaps another card"
                    cells.add((x, y))


def test_the_templates_are_listed_with_what_they_need(client: TestClient) -> None:
    setup_admin(client)
    rows = client.get("/api/v1/templates").json()
    assert {t["id"] for t in rows} >= {"homelab", "media", "proxmox", "network", "projects"}
    media = next(t for t in rows if t["id"] == "media")
    assert media["cards"] > 5 and {c["kind"] for c in media["integrations"]} >= {"plex", "sonarr", "radarr"}
    assert media["integrations"][0]["label"] == "Plex"


def test_a_template_becomes_a_board_with_its_connections_mapped(client: TestClient) -> None:
    setup_admin(client)
    plex = client.post("/api/v1/integrations", json={"kind": "plex", "name": "Living room", "config": {"url": "http://plex:32400", "token": "t"}}, headers=CSRF).json()
    one = client.get("/api/v1/templates/media").json()
    assert [a["name"] for a in one["available"]] == ["Living room"], "only connections of the kinds the template asks for"
    made = client.post("/api/v1/templates/media", json={"name": "Cinema", "connections": {"Plex": plex["id"]}}, headers=CSRF)
    assert made.status_code == 201, made.text
    board = made.json()
    assert board["name"] == "Cinema" and board["settings"]["columns"] == 24
    kinds = [w["kind"] for w in board["pages"][0]["widgets"]]
    assert "plex.nowplaying" in kinds and "plex.recent" in kinds
    assert not any(k.startswith(("sonarr.", "radarr.", "overseerr.", "qbittorrent.", "tautulli.")) for k in kinds), "unmapped connections take their cards with them"
    layouts = board["pages"][0]["layouts"]
    assert len(layouts["lg"]) == len(kinds) == len(layouts["md"]) == len(layouts["sm"])
    assert layouts["lg"][1]["x"] > 0 and layouts["lg"][1]["y"] == 0, "with cards left out the rest flow into place, no holes"
    assert board["pages"][0]["widgets"][0]["integration_id"] == plex["id"]


def test_a_whole_template_keeps_its_drawn_layout(client: TestClient) -> None:
    setup_admin(client)
    board = client.post("/api/v1/templates/projects", json={}, headers=CSRF).json()
    layouts = board["pages"][0]["layouts"]
    document = template_service.get_template("projects")
    drawn = [w["layout"]["lg"] for w in document["pages"][0]["widgets"]]
    assert [(i["x"], i["y"], i["w"], i["h"]) for i in layouts["lg"]] == [(d["x"], d["y"], d["w"], d["h"]) for d in drawn]
    assert all(item["w"] == 4 and item["x"] == 0 for item in layouts["sm"]), "the phone stacks the cards"
    assert [i["y"] for i in layouts["sm"]] == sorted(i["y"] for i in layouts["sm"])


def test_a_template_without_connections_still_makes_a_board(client: TestClient) -> None:
    setup_admin(client)
    board = client.post("/api/v1/templates/projects", json={}, headers=CSRF).json()
    assert [w["kind"] for w in board["pages"][0]["widgets"]][:2] == ["projects.roadmap", "core.clock"]
    assert board["slug"] == "projects"


def test_a_connection_of_the_wrong_kind_or_an_unknown_placeholder_is_refused(client: TestClient) -> None:
    setup_admin(client)
    radarr = client.post("/api/v1/integrations", json={"kind": "radarr", "name": "Movies", "config": {"url": "http://r", "api_key": "k"}}, headers=CSRF).json()
    wrong = client.post("/api/v1/templates/media", json={"connections": {"Plex": radarr["id"]}}, headers=CSRF)
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "bad_template"
    unknown = client.post("/api/v1/templates/media", json={"connections": {"Emby": radarr["id"]}}, headers=CSRF)
    assert unknown.status_code == 400
    assert client.post("/api/v1/templates/nope", json={}, headers=CSRF).status_code == 400
    assert client.get("/api/v1/templates/nope").status_code == 404


def test_a_guest_sees_the_templates_and_may_not_install_one(client: TestClient) -> None:
    setup_admin(client)
    create_user(client, "guest", role="guest")
    login(client, "guest", "another-long-password")
    assert client.get("/api/v1/templates").status_code == 200
    assert client.post("/api/v1/templates/projects", json={}, headers=CSRF).status_code == 403
