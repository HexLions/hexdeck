"""Stalwart: the outgoing mail queue, and how many accounts and domains the server holds.

Checked against Stalwart v0.16.27 running locally on 19.10.2026, set up
through its bootstrap, with three messages submitted to addresses that
cannot be reached, a key with every needed permission, a key with the
queue permissions only, a wrong key and a wrong password.

⚠️ 0.16 has no REST management API any more: ``/api/queue/messages``,
``/api/principal`` and the rest answer 404. Everything goes through JMAP
with the ``urn:stalwart:jmap`` capability, as ``x:QueuedMessage/query`` and
``/get``, ``x:Account/query`` and ``x:Domain/query``.

⚠️ The account id is not fixed: ``/.well-known/jmap`` (a 307 to
``/jmap/session``) names it under ``primaryAccounts["urn:stalwart:jmap"]``;
it was ``d333333`` in bootstrap mode and ``b`` afterwards. The session's
``apiUrl`` is the public address (``https://<hostname>/jmap/``), which a
dashboard inside the network may not reach, so requests go to ``/jmap/`` on
the address configured here.

⚠️ A key without a permission still gets 200: the method's answer is
``["error", {"type": "forbidden"}, id]`` and the others go through. The
summary leaves out what it may not count instead of failing. A wrong key or
password is 401 ``application/problem+json``.

⚠️ Metrics (``x:Metric``) are Enterprise only and answer ``forbidden`` with a
licence message on the community edition, so they are not read.

⚠️ A message that failed for good leaves the queue at once (a bounce is
queued to the sender instead), so the queue shows only what will be tried
again: per recipient, ``status["@type"]`` is ``Scheduled`` before the first
try and ``TemporaryFailure`` with ``errorType`` and ``errorMessage`` after a
failed one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    human_bytes,
)

USING = ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"]
NOT_STALWART = "This address answers, but not the way Stalwart 0.16 or later does."
#: Queued messages read for the list; the total is counted by the server.
LIMIT = 50


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _in(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return ""
    seconds = int((moment - now).total_seconds())
    if seconds <= 0:
        return "now"
    if seconds < 3600:
        return f"in {max(1, seconds // 60)} min"
    if seconds < 86400:
        return f"in {seconds // 3600} h"
    return f"in {seconds // 86400} d"


class StalwartAdapter(Adapter):
    kind = "stalwart"
    label = "Stalwart"
    category = "network"
    description = "Stalwart mail server: the outgoing queue with why each message waits, and its accounts and domains."
    icon = "stalwart"
    beta = True
    docs_url = "https://stalw.art/docs/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://stalwart:8080",
              help="The web address of Stalwart, where its admin interface is."),
        Field("api_key", "API key", secret=True, required=True,
              help="An API key with the permissions to query and get queued messages, and to query accounts and domains."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Mail server", description="Messages waiting in the queue, accounts and domains.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("queued",)),
        WidgetType(kind="queue", label="Mail queue", description="Every message waiting to go out, to whom, why, and when it is tried again.",
                   renderer="list", default_size=(4, 3), refresh_seconds=60,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {str(config.get('api_key') or '').strip()}"}

    def _refused(self, status: int) -> None:
        if status in (401, 403):
            raise AuthFailed("Stalwart refused the API key.",
                             hint="Create a key in Stalwart under the account's API keys, and paste its secret (API_...).")
        if status >= 400:
            raise AdapterError(f"Stalwart answered with HTTP {status}.", code="http_error",
                               hint="Check the URL; it is the address of Stalwart's web interface.")

    async def _account(self, config: dict[str, Any], ctx: Context, cache: float) -> str:
        response = await ctx.request("GET", f"{base_url(config)}/jmap/session", headers=self._headers(config),
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        self._refused(response.status_code)
        try:
            session = response.json()
        except ValueError as error:
            raise AdapterError(NOT_STALWART, code="not_stalwart", hint="The URL probably points at something else.") from error
        account = (session.get("primaryAccounts") or {}).get("urn:stalwart:jmap") if isinstance(session, dict) else None
        if not account:
            raise AdapterError(NOT_STALWART, code="not_stalwart",
                               hint="Stalwart before 0.16 had another management API, which this card does not speak.")
        return str(account)

    async def _call(self, config: dict[str, Any], ctx: Context, calls: list[list[Any]], cache: float = 300) -> dict[str, Any]:
        """The answers by call id; a method refused for want of a permission is None.

        Only the session is kept for a while: JMAP is all POST, which is not cached.
        """
        account = await self._account(config, ctx, cache=cache)
        for call in calls:
            call[1] = {"accountId": account, **call[1]}
        response = await ctx.request("POST", f"{base_url(config)}/jmap/", headers=self._headers(config),
                                     json_body={"using": USING, "methodCalls": calls},
                                     verify=not config.get("insecure"), auth_errors=False)
        self._refused(response.status_code)
        try:
            answers = response.json()["methodResponses"]
        except (ValueError, KeyError, TypeError) as error:
            raise AdapterError(NOT_STALWART, code="not_stalwart") from error
        by_id: dict[str, Any] = {}
        for name, body, call_id in answers:
            if name == "error":
                if body.get("type") != "forbidden":
                    raise AdapterError(f"Stalwart refused a request: {body.get('description') or body.get('type')}.",
                                       code="jmap_error")
                by_id[call_id] = None
            else:
                by_id[call_id] = body
        return by_id

    async def _queue(self, config: dict[str, Any], ctx: Context, *, counts: bool) -> dict[str, Any]:
        calls: list[list[Any]] = [
            ["x:QueuedMessage/query", {"calculateTotal": True, "limit": LIMIT}, "q"],
            ["x:QueuedMessage/get", {"#ids": {"resultOf": "q", "name": "x:QueuedMessage/query", "path": "/ids"},
                                     "properties": ["createdAt", "nextRetry", "recipients", "returnPath", "size"]}, "g"],
        ]
        if counts:
            calls += [["x:Account/query", {"calculateTotal": True, "limit": 0}, "a"],
                      ["x:Domain/query", {"calculateTotal": True, "limit": 0}, "d"]]
        answers = await self._call(config, ctx, calls)
        if answers.get("q") is None or answers.get("g") is None:
            raise AuthFailed("The API key may not read the mail queue.",
                             hint="Give the key the permissions to query and get queued messages.")
        return answers

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        answers = await self._queue(config, ctx, counts=False)
        queued = int(answers["q"].get("total") or 0)
        return f"Stalwart answers with {queued} message{'' if queued == 1 else 's'} in the queue."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        answers = await self._queue(config, ctx, counts=widget_kind == "summary")
        now = datetime.now(UTC)
        if widget_kind == "queue":
            return self._list(answers, options, now)
        return self._summary(answers)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(answers: dict[str, Any]) -> WidgetData:
        queued = int(answers["q"].get("total") or 0)
        failing = sum(1 for message in answers["g"].get("list") or []
                      for one in (message.get("recipients") or {}).values()
                      if (one.get("status") or {}).get("@type") == "TemporaryFailure")
        secondary: list[dict[str, Any]] = []
        if failing:
            secondary.append({"label": "Failing", "value": failing})
        for key, label in (("a", "Accounts"), ("d", "Domains")):
            if answers.get(key) is not None:
                secondary.append({"label": label, "value": int(answers[key].get("total") or 0)})
        return WidgetData(
            status="warn" if failing else "ok",
            primary={"label": "Queued", "value": queued},
            secondary=secondary,
            metrics={"queued": float(queued)},
        )

    @staticmethod
    def _list(answers: dict[str, Any], options: dict[str, Any], now: datetime) -> WidgetData:
        items = []
        for message in answers["g"].get("list") or []:
            recipients = message.get("recipients") or {}
            if not isinstance(recipients, dict) or not recipients:
                continue
            first, state = next(iter(recipients.items()))
            status = state.get("status") or {}
            failing = status.get("@type") == "TemporaryFailure"
            reason = str(status.get("errorMessage") or status.get("responseMessage") or "") if failing else "Waiting for the first try"
            more = f" and {len(recipients) - 1} more" if len(recipients) > 1 else ""
            retry = _in(_when(message.get("nextRetry")), now)
            size = message.get("size")
            items.append({
                "title": f"{first}{more}",
                "subtitle": " · ".join(part for part in (reason, human_bytes(size) if isinstance(size, (int, float)) else "") if part),
                "status": "warn" if failing else "unknown",
                "value": f"Retry {retry}" if retry else "",
                "_order": str(message.get("nextRetry") or ""),
            })
        items.sort(key=lambda row: row.pop("_order"))
        total = int(answers["q"].get("total") or 0)
        return WidgetData(
            status="warn" if any(row["status"] == "warn" for row in items) else "ok",
            items=items[: max(1, int(options.get("limit") or 10))],
            secondary=[{"label": "Queued", "value": total}],
            meta={"empty": "Nothing is waiting to go out."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = datetime.now(UTC)
        stuck = {
            "nextRetry": (now + timedelta(minutes=12)).isoformat(), "size": 48_211,
            "recipients": {"orders@example.net": {"status": {
                "@type": "TemporaryFailure", "errorType": "connectionError",
                "errorMessage": "Connection refused by mx1.example.net"}}},
        }
        fresh = {
            "nextRetry": now.isoformat(), "size": 3_204,
            "recipients": {"anna@example.com": {"status": {"@type": "Scheduled"}},
                           "ben@example.com": {"status": {"@type": "Scheduled"}}},
        }
        messages = [stuck] + ([fresh] if tick % 3 == 0 else [])
        answers = {
            "q": {"total": len(messages)}, "g": {"list": messages},
            "a": {"total": 14 + int(fake.walk("stalwart-accounts", tick, 0, 0.9))}, "d": {"total": 3},
        }
        if widget_kind == "queue":
            return self._list(answers, options, now)
        return self._summary(answers)


ADAPTER = StalwartAdapter()
