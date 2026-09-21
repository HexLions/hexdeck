"""Boards from Homepage and Homarr: the files become a plan that says what
it would make, and the plan becomes connections and a board.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.services import imports

from .conftest import CSRF, create_user, login, setup_admin

FIXTURES = Path(__file__).parent / "fixtures" / "imports"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _cards(plan: dict, page: str) -> list[dict]:
    return next(p for p in plan["pages"] if p["name"] == page)["cards"]


def test_homepage_services_become_pages_tiles_connections_and_cards() -> None:
    plan = imports.plan_for("auto", {"services": _read("homepage-services.yaml")})
    assert plan["source"] == "homepage"
    assert [p["name"] for p in plan["pages"]] == ["Media", "Media · Downloads", "Infrastructure"]
    media = _cards(plan, "Media")
    kinds = [(c["kind"], c["title"]) for c in media]
    assert ("sonarr.status", "Sonarr") in kinds and ("core.app", "Sonarr") in kinds, "a service with a widget is a card and a tile"
    assert ("plex.nowplaying", "Plex") in kinds and ("uptimekuma.summary", "Plex") in kinds, "several widgets, several cards"
    sonarr = next(c for c in plan["connections"] if c["kind"] == "sonarr")
    assert sonarr["config"] == {"url": "http://sonarr.lan:8989", "api_key": "sonarr-api-key"} and sonarr["missing"] == []
    plex = next(c for c in plan["connections"] if c["kind"] == "plex")
    assert plex["config"]["token"] == "plex-token", "Homepage's key is the token where the adapter wants one"
    proxmox = next(c for c in plan["connections"] if c["kind"] == "proxmox")
    assert proxmox["config"]["token_id"] == "api@pam!homepage" and proxmox["config"]["token_secret"] == "proxmox-secret"
    pihole = next(c for c in plan["connections"] if c["kind"] == "pihole")
    assert pihole["config"]["password"] == "pihole-app-password"
    tile = next(c for c in media if c["kind"] == "core.app" and c["title"] == "Sonarr")
    assert tile["icon"] == "sonarr" and tile["link"] == "http://sonarr.lan:8989/" and tile["options"] == {"description": "Series management"}
    odd = [c for c in _cards(plan, "Infrastructure") if c["title"] == "Something Odd"]
    assert [c["kind"] for c in odd] == ["core.app"], "an unknown widget is a tile"
    assert any("nonexistent-widget" in w for w in plan["warnings"])
    qbit = next(c for c in plan["connections"] if c["kind"] == "qbittorrent")
    assert qbit["config"]["username"] == "admin" and qbit["config"]["password"] == "adminadmin"


def test_homepage_bookmarks_and_information_widgets_come_along() -> None:
    plan = imports.plan_for("homepage", {"services": _read("homepage-services.yaml"), "bookmarks": _read("homepage-bookmarks.yaml"), "widgets": _read("homepage-widgets.yaml")})
    books = _cards(plan, "Bookmarks")
    assert [c["title"] for c in books] == ["Developer", "Social"]
    assert books[0]["options"]["links"] == "Github | https://github.com/ | lucide:link\nDocs | https://docs.example.com/ | readthedocs"
    first = [c["kind"] for c in _cards(plan, "Media")[:3]]
    assert first == ["core.search", "core.clock", "weather.current"], "the information bar goes on the first page"
    clock = next(c for c in _cards(plan, "Media") if c["kind"] == "core.clock")
    assert clock["options"] == {"format": "12h"}
    weather = next(c for c in _cards(plan, "Media") if c["kind"] == "weather.current")
    assert weather["options"] == {"units": "metric", "latitude": 41.9, "longitude": 12.5, "place": "Rome"}
    assert any("resources" in w for w in plan["warnings"])


def test_homarr_apps_widgets_categories_and_places_come_along() -> None:
    plan = imports.plan_for("auto", {"config": _read("homarr-default.json")})
    assert plan["source"] == "homarr" and plan["name"] == "Lab"
    assert [p["name"] for p in plan["pages"]] == ["Overview", "Media"]
    media = _cards(plan, "Media")
    sonarr_tile = next(c for c in media if c["kind"] == "core.app")
    assert sonarr_tile["link"] == "https://sonarr.example.com" and sonarr_tile["layout"] == {"x": 0, "y": 0, "w": 2, "h": 1}
    assert sonarr_tile["icon"].startswith("https://"), "an icon by address stays an address"
    qbit_tile = next(c for c in _cards(plan, "Overview") if c["title"] == "qBittorrent" and c["kind"] == "core.app")
    assert qbit_tile["icon"] == "qbittorrent", "a path inside Homarr becomes the service's name"
    assert next(c for c in _cards(plan, "Overview") if c["kind"] == "qbittorrent.queue")["label"] == "Queue"
    assert {c["kind"] for c in media} == {"core.app", "weather.current", "rss.headlines", "notepad.pad"}
    assert next(c for c in media if c["kind"] == "notepad.pad")["options"]["content"].startswith("# Rack")
    overview = _cards(plan, "Overview")
    assert all(c["layout"] is None for c in overview), "the wrappers flow into place"
    kinds = {c["kind"] for c in overview}
    assert {"core.app", "core.clock", "qbittorrent.queue", "core.bookmarks"} <= kinds
    assert "plex.nowplaying" not in kinds and any("media-server" in w for w in plan["warnings"]), "no media server app, no card"
    qbit = next(c for c in plan["connections"] if c["kind"] == "qbittorrent")
    assert qbit["config"] == {"url": "http://qbit.lan:8080", "username": "admin", "password": "adminadmin"}
    sonarr = next(c for c in plan["connections"] if c["kind"] == "sonarr")
    assert sonarr["config"]["api_key"] == "sonarr-key"
    assert any("openmediavault" in w for w in plan["warnings"])


def test_a_file_that_is_neither_is_refused() -> None:
    try:
        imports.plan_for("auto", {"config": "hello: world"})
    except imports.DashboardImportError as failure:
        assert "Homepage" in str(failure)
    else:
        raise AssertionError("a plain mapping was accepted")


def test_the_plan_becomes_connections_and_a_board(client: TestClient) -> None:
    setup_admin(client)
    plan = client.post("/api/v1/imports/preview", json={"files": {"services": _read("homepage-services.yaml"), "bookmarks": _read("homepage-bookmarks.yaml")}}, headers=CSRF)
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    # Leave the Proxmox card out; its connection is then not made either.
    for page in plan["pages"]:
        for card in page["cards"]:
            if card["kind"].startswith("proxmox."):
                card["enabled"] = False
    plan["name"] = "From Homepage"
    made = client.post("/api/v1/imports/apply", json={"plan": plan}, headers=CSRF)
    assert made.status_code == 201, made.text
    board = made.json()
    assert board["name"] == "From Homepage" and [p["name"] for p in board["pages"]] == ["Media", "Media · Downloads", "Infrastructure", "Bookmarks"]
    connections = client.get("/api/v1/integrations").json()
    kinds = {c["kind"] for c in connections}
    assert {"sonarr", "plex", "uptimekuma", "qbittorrent", "grafana", "immich", "pihole"} <= kinds and "proxmox" not in kinds
    sonarr = next(c for c in connections if c["kind"] == "sonarr")
    from app.services.integrations import SECRET_PLACEHOLDER

    assert sonarr["config"]["url"] == "http://sonarr.lan:8989" and sonarr["config"]["api_key"] == SECRET_PLACEHOLDER, "the secret is stored, not shown"
    media = board["pages"][0]["widgets"]
    card = next(w for w in media if w["kind"] == "sonarr.status")
    assert card["integration_id"] == sonarr["id"]
    assert len(board["pages"][0]["layouts"]["lg"]) == len(media), "every card has a place"


def test_a_value_that_looks_like_an_environment_reference_stays_text(client: TestClient) -> None:
    setup_admin(client)
    services = "- Media:\n    - Sonarr:\n        href: http://sonarr/\n        widget:\n          type: sonarr\n          url: http://sonarr\n          key: ${HEXDECK_SECRET_KEY}\n"
    plan = client.post("/api/v1/imports/preview", json={"files": {"services": services}}, headers=CSRF).json()
    assert plan["connections"][0]["config"]["api_key"] == "${HEXDECK_SECRET_KEY}"
    made = client.post("/api/v1/imports/apply", json={"plan": plan}, headers=CSRF)
    assert made.status_code == 201, made.text
    from app.db import db_session
    from app.models import Integration
    from app.services.integrations import resolve_config

    with db_session() as db:
        stored = db.query(Integration).filter_by(kind="sonarr").one()
        assert resolve_config(stored)["api_key"] == "${HEXDECK_SECRET_KEY}", "never expanded"


def test_only_an_administrator_imports(client: TestClient) -> None:
    setup_admin(client)
    create_user(client, "kim")
    login(client, "kim", "another-long-password")
    assert client.post("/api/v1/imports/preview", json={"files": {"services": "- A:\n    - B:\n        href: http://b/\n"}}, headers=CSRF).status_code == 403
    assert client.post("/api/v1/imports/apply", json={"plan": {}}, headers=CSRF).status_code == 403
