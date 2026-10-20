"""``/api/metrics``: HexDeck's own state in the Prometheus text format.

For somebody who already watches the rest of the house with Prometheus and
Grafana: which cards fail, how long their reads take, how many browsers
follow a board, and how old the last backup and its copy off the machine
are. Off unless ``HEXDECK_METRICS_TOKEN`` is set, and then only for a request
that carries that token, because the labels name the boards and the kinds of
service the installation talks to.
"""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from .. import __version__
from ..config import get_settings
from ..db import db_session
from ..deps import error
from ..models import Board, Page, Widget
from ..services import backup
from ..services.collector import collector
from ..services.sse import hub
from ..services.state import live

router = APIRouter(tags=["system"])

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _label(value: object) -> str:
    """A label value as the text format wants it: backslash, quote and newline escaped."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _metric(lines: list[str], name: str, kind: str, text: str, samples: list[tuple[dict[str, object], float]]) -> None:
    lines.append(f"# HELP {name} {text}")
    lines.append(f"# TYPE {name} {kind}")
    for labels, value in samples:
        inside = ",".join(f'{key}="{_label(one)}"' for key, one in labels.items())
        lines.append(f"{name}{{{inside}}} {value:g}" if inside else f"{name} {value:g}")


def _allowed(request: Request) -> None:
    wanted = get_settings().metrics_token
    if not wanted:
        # Not there at all, rather than "forbidden": nothing to find for a scanner.
        raise error("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
    header = request.headers.get("authorization", "")
    given = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not given or not secrets.compare_digest(given.encode("utf-8"), wanted.encode("utf-8")):
        raise error("unauthenticated", "The metrics need the token from HEXDECK_METRICS_TOKEN.", status.HTTP_401_UNAUTHORIZED)


def render() -> str:
    runs = collector.runs()
    with db_session() as db:
        rows = db.execute(
            select(Widget.id, Widget.kind, Board.slug)
            .join(Page, Widget.page_id == Page.id)
            .join(Board, Page.board_id == Board.id)
            .where(Widget.id.in_(list(runs)))
        ).all() if runs else []
    cards: list[tuple[dict[str, object], float]] = []
    seconds: list[tuple[dict[str, object], float]] = []
    failures: list[tuple[dict[str, object], float]] = []
    by_status = {"ok": 0, "warn": 0, "bad": 0, "unknown": 0}
    for widget_id, kind, board in rows:
        run = runs[widget_id]
        data = live.get(widget_id)
        labels = {"widget": widget_id, "kind": kind, "board": board}
        up = data is not None and not data.error
        state = data.status if data is not None else "unknown"
        by_status[state if state in by_status else "unknown"] += 1
        cards.append((labels, 1.0 if up else 0.0))
        seconds.append((labels, run.seconds))
        failures.append((labels, float(run.failures)))

    lines: list[str] = []
    _metric(lines, "hexdeck_info", "gauge", "The running version.", [({"version": __version__}, 1.0)])
    _metric(lines, "hexdeck_cards", "gauge", "Cards being read, by the colour they show.",
            [({"status": state}, float(count)) for state, count in by_status.items()])
    _metric(lines, "hexdeck_card_up", "gauge", "1 when the card's last read worked, 0 when it shows an error.", cards)
    _metric(lines, "hexdeck_card_read_seconds", "gauge", "How long the card's last read took.", seconds)
    _metric(lines, "hexdeck_card_failures", "gauge", "Failed reads of the card in a row; above 0 it is read less often.", failures)
    _metric(lines, "hexdeck_stream_connections", "gauge", "Browsers and displays following a board live.", [({}, float(hub.connections))])

    snapshots = [path.stat().st_mtime for path in backup.folder().glob("*.db")] if backup.folder().is_dir() else []
    if snapshots:
        _metric(lines, "hexdeck_backup_last_timestamp_seconds", "gauge", "When the newest snapshot was written.", [({}, max(snapshots))])
    if backup.copy_folder() is not None:
        _metric(lines, "hexdeck_backup_copy_ok", "gauge", "1 when the last copy off the machine worked, 0 when it failed or none was made yet.",
                [({}, 1.0 if backup.last_copy.get("name") and not backup.last_copy.get("error") else 0.0)])
    _metric(lines, "hexdeck_scrape_timestamp_seconds", "gauge", "When this answer was made.", [({}, round(time.time(), 3))])
    return "\n".join(lines) + "\n"


@router.get("/api/metrics", summary="HexDeck's own state for Prometheus", include_in_schema=False)
def metrics(request: Request) -> Response:
    _allowed(request)
    return Response(render(), media_type=CONTENT_TYPE)
