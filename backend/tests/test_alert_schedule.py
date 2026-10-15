"""Quiet hours, what they hold back, and the daily summary of a channel."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import db_session
from app.models import Notice, NotificationChannel
from app.services import alert_schedule
from app.services.notify import Message

from .conftest import CSRF, setup_admin

NIGHT = {"quiet_from": "22:30", "quiet_to": "07:00", "quiet_urgent": True, "summary_at": ""}


def _local(day: int, hhmm: str) -> datetime:
    hours, minutes = (int(part) for part in hhmm.split(":"))
    return datetime(2026, 10, day, hours, minutes).astimezone()


@pytest.mark.parametrize(("clock", "quiet"), [("22:29", False), ("22:30", True), ("03:00", True), ("06:59", True), ("07:00", False), ("12:00", False)])
def test_quiet_hours_across_midnight(clock: str, quiet: bool) -> None:
    assert alert_schedule.is_quiet(NIGHT, _local(14, clock)) is quiet


def test_quiet_hours_within_a_day() -> None:
    lunch = {"quiet_from": "12:00", "quiet_to": "14:00"}
    assert alert_schedule.is_quiet(lunch, _local(14, "13:00"))
    assert not alert_schedule.is_quiet(lunch, _local(14, "23:00"))
    assert not alert_schedule.is_quiet({}, _local(14, "03:00"))


def test_the_last_stretch_that_ended() -> None:
    began, ended = alert_schedule.last_window(NIGHT, _local(14, "08:15"))
    assert (began, ended) == (_local(13, "22:30"), _local(14, "07:00"))
    began, ended = alert_schedule.last_window(NIGHT, _local(14, "02:00"))
    assert (began, ended) == (_local(12, "22:30"), _local(13, "07:00"))


def test_an_outage_still_comes_through_unless_told_otherwise() -> None:
    night = _local(14, "03:00")
    assert alert_schedule.holds(NIGHT, "warn", night)
    assert not alert_schedule.holds(NIGHT, "error", night)
    assert alert_schedule.holds({**NIGHT, "quiet_urgent": False}, "error", night)
    assert not alert_schedule.holds(NIGHT, "warn", _local(14, "12:00"))


@pytest.mark.parametrize("bad", [{"quiet_from": "25:00", "quiet_to": "07:00"}, {"quiet_from": "22:00"}, {"quiet_from": "07:00", "quiet_to": "07:00"},
                                 {"summary_at": "8 o'clock"}])
def test_a_schedule_that_makes_no_sense_is_refused(bad: dict) -> None:
    with pytest.raises(alert_schedule.ScheduleError):
        alert_schedule.clean(bad)


def test_the_bookkeeping_cannot_be_set_from_outside() -> None:
    kept = alert_schedule.clean({"_flushed": "2000-01-01T00:00:00+00:00", **NIGHT}, {"_summary_on": "2026-10-13"})
    assert kept["_summary_on"] == "2026-10-13" and "_flushed" not in kept


def _channel(client: TestClient, schedule: dict, events: list[str]) -> int:
    answer = client.post("/api/v1/channels", json={"kind": "ntfy", "name": "Phone", "config": {"url": "https://ntfy.example.com", "topic": "lab"},
                                                   "events": events, "schedule": schedule}, headers=CSRF)
    assert answer.status_code == 201, answer.text
    assert answer.json()["schedule"] == {key: schedule.get(key, "" if key != "quiet_urgent" else True)
                                         for key in ("quiet_from", "quiet_to", "quiet_urgent", "summary_at")}
    return answer.json()["id"]


def _sent(monkeypatch: Any) -> list[tuple[int, Message]]:
    from app.services import channels

    sent: list[tuple[int, Message]] = []

    async def record(channel_id: int, message: Message) -> None:
        sent.append((channel_id, message))

    monkeypatch.setattr(channels, "send_to_channel", record)
    return sent


async def test_a_night_held_back_arrives_as_one_message_in_the_morning(client: TestClient, monkeypatch: Any) -> None:
    setup_admin(client)
    channel_id = _channel(client, NIGHT, ["card_red", "outage", "recovery"])
    sent = _sent(monkeypatch)
    with db_session() as db:
        user_id = db.get(NotificationChannel, channel_id).user_id
        channel = db.get(NotificationChannel, channel_id)
        channel.schedule = {**channel.schedule, "_flushed": _local(13, "07:00").isoformat()}
        for hhmm, event, level, title in (("23:10", "card_red", "warn", "NAS turned red"), ("02:40", "outage", "error", "Plex is down"),
                                          ("03:05", "recovery", "info", "Plex is back"), ("04:00", "download_done", "info", "Not subscribed")):
            db.add(Notice(user_id=user_id, event=event, level=level, title=title, created_at=_local(13 if hhmm > "12:00" else 14, hhmm)))
    assert await alert_schedule.tick(_local(14, "03:30")) == 0, "nothing before the quiet hours end"
    assert await alert_schedule.tick(_local(14, "07:01")) == 1
    (to, message), = sent
    assert to == channel_id and message.title == "While it was quiet (22:30–07:00): 2 messages"
    assert message.body == "• NAS turned red\n• Plex is back", "the outage went out at 02:40 already, and nothing unsubscribed"
    assert await alert_schedule.tick(_local(14, "07:02")) == 0, "a stretch is told once"


async def test_the_daily_summary_once_a_day_at_its_time(client: TestClient, monkeypatch: Any) -> None:
    setup_admin(client)
    channel_id = _channel(client, {"summary_at": "08:00"}, ["outage"])
    sent = _sent(monkeypatch)
    assert await alert_schedule.tick(_local(14, "07:59")) == 0
    assert await alert_schedule.tick(_local(14, "08:00")) == 1
    assert sent[0][0] == channel_id and sent[0][1].title == "Daily summary: all is well"
    assert await alert_schedule.tick(_local(14, "09:00")) == 0
    assert await alert_schedule.tick(_local(15, "08:01")) == 1


def test_a_held_message_is_not_sent_but_kept_as_a_notice(client: TestClient, monkeypatch: Any) -> None:
    from app.services import notify

    setup_admin(client)
    _channel(client, {"quiet_from": "00:00", "quiet_to": "23:59", "quiet_urgent": True}, ["card_red", "outage"])
    sent = _sent(monkeypatch)
    import asyncio

    asyncio.run(notify.dispatch(Message(event="card_red", title="NAS turned red", body="", level="warn"),
                                [notify_user_id()]))
    asyncio.run(notify.dispatch(Message(event="outage", title="Plex is down", body="", level="error"), [notify_user_id()]))
    assert [message.title for _, message in sent] == ["Plex is down"]


def notify_user_id() -> int:
    from sqlalchemy import select

    from app.models import User

    with db_session() as db:
        return db.scalar(select(User.id))


def test_a_bad_time_is_a_readable_400(client: TestClient) -> None:
    setup_admin(client)
    answer = client.post("/api/v1/channels", json={"kind": "ntfy", "name": "Phone", "config": {"url": "https://ntfy.example.com", "topic": "lab"},
                                                   "schedule": {"quiet_from": "22:00"}}, headers=CSRF)
    assert answer.status_code == 400 and answer.json()["detail"]["code"] == "bad_schedule"


