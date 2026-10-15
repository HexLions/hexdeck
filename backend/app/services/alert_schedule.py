"""When a channel speaks: quiet hours, what they held back, and a daily summary.

A channel's ``schedule`` holds what its owner set: ``quiet_from`` and
``quiet_to`` (``"22:30"``, ``"07:00"``; empty means never quiet),
``quiet_urgent`` (an outage still comes through at night) and ``summary_at``
(the time of a daily summary; empty means none). Beside them it keeps its
own bookkeeping, under keys the router never accepts from a browser:
``_flushed`` (the end of the last quiet stretch already told) and
``_summary_on`` (the day of the last summary).

⚠️ Server time. The times are read in the time zone of the HexDeck server
(``TZ`` in the compose file), the same clock the log is written in.

⚠️ What was held back is not stored a second time. Every message is already
a notice in the owner's notice centre; when the quiet stretch ends, the
notices it covered, of the events the channel subscribes to, go out as one
message. A server restarted at night loses nothing.

⚠️ ``tick`` runs every minute, so a summary or the end of a quiet stretch
can come up to a minute late, never early.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select

from ..db import db_session
from ..models import Board, HealthCheck, Notice, NotificationChannel, Page, User, Widget
from .notify import EVENTS, Message

logger = logging.getLogger("hexdeck.schedule")

TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
#: Levels that pass quiet hours when the owner lets urgent messages through.
URGENT_LEVELS = frozenset({"error"})
#: Lines of one held-back message at most; the rest is counted.
MOST_LINES = 12
SETTABLE = ("quiet_from", "quiet_to", "quiet_urgent", "summary_at")


class ScheduleError(ValueError):
    """A time that is not ``HH:MM``; the message says which."""


def clean(incoming: dict[str, Any] | None, stored: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a browser may set, checked, with the bookkeeping kept as it was."""
    result = {key: value for key, value in (stored or {}).items() if key.startswith("_")}
    incoming = incoming or {}
    for key in ("quiet_from", "quiet_to", "summary_at"):
        value = str(incoming.get(key) or "").strip()
        if value and not TIME.match(value):
            raise ScheduleError(f"{value!r} is not a time like 07:30.")
        result[key] = value
    if bool(result["quiet_from"]) != bool(result["quiet_to"]):
        raise ScheduleError("Quiet hours need both a start and an end.")
    if result["quiet_from"] and result["quiet_from"] == result["quiet_to"]:
        raise ScheduleError("Quiet hours cannot start and end at the same time.")
    result["quiet_urgent"] = bool(incoming.get("quiet_urgent", True))
    return result


def public(schedule: dict[str, Any] | None) -> dict[str, Any]:
    schedule = schedule or {}
    return {"quiet_from": str(schedule.get("quiet_from") or ""), "quiet_to": str(schedule.get("quiet_to") or ""),
            "quiet_urgent": bool(schedule.get("quiet_urgent", True)), "summary_at": str(schedule.get("summary_at") or "")}


def _at(day: date, hhmm: str) -> datetime:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes)).astimezone()


def is_quiet(schedule: dict[str, Any] | None, now: datetime) -> bool:
    """Whether ``now`` (local, aware) falls in the channel's quiet hours."""
    schedule = schedule or {}
    start, end = schedule.get("quiet_from"), schedule.get("quiet_to")
    if not start or not end:
        return False
    clock = now.strftime("%H:%M")
    if start < end:
        return start <= clock < end
    return clock >= start or clock < end


def last_window(schedule: dict[str, Any], now: datetime) -> tuple[datetime, datetime] | None:
    """The most recent quiet stretch that has ended by ``now``."""
    start, end = schedule.get("quiet_from"), schedule.get("quiet_to")
    if not start or not end:
        return None
    ended = _at(now.date(), end)
    if ended > now:
        ended = _at(now.date() - timedelta(days=1), end)
    began = _at(ended.date(), start)
    if began >= ended:
        began = _at(ended.date() - timedelta(days=1), start)
    return began, ended


def holds(schedule: dict[str, Any] | None, level: str, now: datetime) -> bool:
    """Whether a message of this level waits for the end of the quiet hours."""
    if not is_quiet(schedule, now):
        return False
    return not ((schedule or {}).get("quiet_urgent", True) and level in URGENT_LEVELS)


def held_message(notices: list[Notice], began: datetime, ended: datetime) -> Message:
    lines = [f"• {notice.title}" for notice in notices[:MOST_LINES]]
    if len(notices) > MOST_LINES:
        lines.append(f"… and {len(notices) - MOST_LINES} more")
    level = "warn" if any(notice.level in ("warn", "error") for notice in notices) else "info"
    title = f"While it was quiet ({began:%H:%M}–{ended:%H:%M}): {len(notices)} message{'s' if len(notices) != 1 else ''}"
    return Message(event="quiet_digest", title=title, body="\n".join(lines), level=level)


def _held(db: Any, channel: NotificationChannel, began: datetime, ended: datetime) -> list[Notice]:
    events = [subscription.event for subscription in channel.subscriptions]
    notices = list(db.scalars(
        select(Notice).where(Notice.user_id == channel.user_id, Notice.event.in_(events),
                             Notice.created_at >= began, Notice.created_at < ended).order_by(Notice.created_at)))
    if (channel.schedule or {}).get("quiet_urgent", True):
        # Those went out when they happened.
        notices = [notice for notice in notices if notice.level not in URGENT_LEVELS]
    return notices


def summary_message(db: Any, user: User, now: datetime) -> Message:
    """What stands red and amber on the boards the owner may see, what is down, and the day's notices."""
    from ..deps import board_permission
    from .state import live

    visible = [board.id for board in db.scalars(select(Board)) if board_permission(db, board, user)]
    red: list[str] = []
    amber = 0
    for widget in db.scalars(select(Widget).join(Page).where(Page.board_id.in_(visible or [-1]))):
        data = live.get(widget.id)
        if data is None or data.error:
            continue
        if data.status == "bad":
            red.append(widget.title or widget.kind)
        elif data.status == "warn":
            amber += 1
    down = [check for check in db.scalars(select(HealthCheck).where(HealthCheck.enabled.is_(True), HealthCheck.last_ok.is_(False)))
            if check.widget is None or check.widget.page.board_id in visible]
    since = now - timedelta(days=1)
    told = list(db.scalars(select(Notice).where(Notice.user_id == user.id, Notice.created_at >= since)))
    lines = []
    if down:
        lines.append(f"Down: {', '.join(sorted((check.widget.title if check.widget else check.target) or '?' for check in down)[:8])}")
    if red:
        lines.append(f"Red: {', '.join(sorted(red)[:8])}{' …' if len(red) > 8 else ''}")
    if amber:
        lines.append(f"Amber: {amber} card{'s' if amber != 1 else ''}")
    by_event: dict[str, int] = {}
    for notice in told:
        by_event[notice.event] = by_event.get(notice.event, 0) + 1
    if by_event:
        lines.append("Last 24 hours: " + ", ".join(f"{count} × {EVENTS.get(event, event).lower()}" for event, count in sorted(by_event.items(), key=lambda pair: -pair[1])))
    if not lines:
        lines.append("Nothing is down, nothing is red, and nothing happened in the last 24 hours.")
    trouble = bool(down or red)
    title = "Daily summary: " + ("all is well" if not trouble else f"{len(down)} down, {len(red)} red")
    return Message(event="daily_summary", title=title, body="\n".join(lines), level="warn" if trouble else "info")


async def tick(now: datetime | None = None) -> int:
    """Send what is due now: held-back messages and daily summaries. Answers how many went out."""
    from .channels import send_to_channel

    now = now or datetime.now().astimezone()
    due: list[tuple[int, Message]] = []
    with db_session() as db:
        channels = list(db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True))))
        for channel in channels:
            schedule = dict(channel.schedule or {})
            changed = False
            window = last_window(schedule, now)
            if window is not None and not is_quiet(schedule, now):
                began, ended = window
                flushed = schedule.get("_flushed")
                if not flushed or datetime.fromisoformat(flushed) < ended:
                    notices = _held(db, channel, began, ended)
                    if notices:
                        due.append((channel.id, held_message(notices, began, ended)))
                    schedule["_flushed"] = ended.isoformat()
                    changed = True
            summary_at = schedule.get("summary_at")
            if summary_at and now >= _at(now.date(), summary_at) and schedule.get("_summary_on") != now.date().isoformat():
                user = db.get(User, channel.user_id)
                if user is not None and not user.disabled:
                    due.append((channel.id, summary_message(db, user, now)))
                schedule["_summary_on"] = now.date().isoformat()
                changed = True
            if changed:
                channel.schedule = schedule
    for channel_id, message in due:
        try:
            await send_to_channel(channel_id, message)
        except Exception as error:  # noqa: BLE001 - one channel must not stop the others
            logger.warning("Channel %s failed with a scheduled message: %s", channel_id, error)
            with db_session() as db:
                channel = db.get(NotificationChannel, channel_id)
                if channel is not None:
                    channel.last_error = str(error)[:300]
    return len(due)
