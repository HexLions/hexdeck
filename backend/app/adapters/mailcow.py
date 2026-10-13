"""mailcow: whether every container runs, the mail stuck in the queue, the mailboxes.

mailcow's API lives under ``/api/v1`` and takes its key in an ``X-API-Key``
header; a read-only key is enough. ``get/status/containers`` lists the
containers with their state, ``get/status/vmail`` the mail storage,
``get/status/version`` the version, ``get/mailq/all`` the Postfix queue and
``get/mailbox/all`` every mailbox with its quota.

⚠️ A key works only from the addresses listed with it ("Allow API access
from"), unless "Skip IP check" is ticked. From anywhere else mailcow answers
401 with "api access denied for ip ...", which the card repeats.

⚠️ A wrong key is reported to mailcow's own fail2ban (Netfilter) as a failed
login. Left wrong, a card that asks every minute can get HexDeck's address
banned; the card says which of the two refusals it was.

⚠️ ``percent_in_use`` is a number, or ``"- "`` for a mailbox without a
quota. A queued recipient carries its delay reason in brackets after the
address.

Written from mailcow's source (``json_api.php``, ``sessions.inc.php``,
``functions.mailq.inc.php`` and ``functions.mailbox.inc.php``, release
2026-09a). Not yet run against an installation.
"""

from __future__ import annotations

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
    ago,
    base_url,
    human_bytes,
)

STATUS_SECONDS = 60
MAILBOX_SECONDS = 300


def _percent(value: Any) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


class MailcowAdapter(Adapter):
    kind = "mailcow"
    label = "mailcow"
    category = "network"
    description = "A mailcow server: whether every container runs, the mail waiting in the queue and why, the storage, and the fullest mailboxes."
    icon = "mailcow"
    docs_url = "https://github.com/mailcow/mailcow-dockerized/blob/master/data/web/api/openapi.yaml"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://mail.example.com"),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="Under System, Configuration, Access, API. The read-only key is enough; HexDeck's address has to be in its allowed list."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="How many containers run, the mail in the queue, the storage in use and the version.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("queue",)),
        WidgetType(kind="queue", label="Mail queue", description="The mail waiting in Postfix's queue, with the sender, the recipient and why it waits.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("limit", "Entries", type="number", default=8),)),
        WidgetType(kind="mailboxes", label="Mailboxes", description="The mailboxes by how full they are, with the messages and the last sign-in.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/api/v1/get/{path}",
                                     headers={"Accept": "application/json", "X-API-Key": str(config.get("api_key") or "")},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError(f"mailcow answered with HTTP {response.status_code} and no JSON.", code="not_json",
                               hint="The address of mailcow's web interface.") from failure
        if response.status_code in (401, 403):
            message = str(answer.get("msg") or "") if isinstance(answer, dict) else ""
            if message.startswith("api access denied for ip"):
                raise AuthFailed(f"mailcow refused the key from this address ({message.removeprefix('api access denied for ip ').strip()}).",
                                 hint="Add HexDeck's address to the key's allowed list, or tick Skip IP check.")
            raise AuthFailed("mailcow refused the API key.",
                             hint="A wrong key counts as a failed login in mailcow's fail2ban; fix it before the card asks again and again.")
        if response.status_code >= 400:
            raise AdapterError(f"mailcow answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of mailcow's web interface.")
        return answer

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        version = await self._get(config, ctx, "status/version", 0) or {}
        containers = await self._get(config, ctx, "status/containers", 0) or {}
        return f"mailcow {version.get('version') or ''} answers with {len(containers)} containers.".replace("mailcow  ", "mailcow ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        limit = max(1, int(options.get("limit") or 8))
        if widget_kind == "queue":
            return self._queue(await self._get(config, ctx, "mailq/all", STATUS_SECONDS) or [], limit)
        if widget_kind == "mailboxes":
            mailboxes = await self._get(config, ctx, "mailbox/all", MAILBOX_SECONDS)
            return self._mailboxes(mailboxes if isinstance(mailboxes, list) else [], limit)
        containers = await self._get(config, ctx, "status/containers", STATUS_SECONDS) or {}
        queue = await self._get(config, ctx, "mailq/all", STATUS_SECONDS) or []
        storage = await self._get(config, ctx, "status/vmail", STATUS_SECONDS) or {}
        version = await self._get(config, ctx, "status/version", 3600) or {}
        return self._status(containers, queue, storage, version)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(containers: dict[str, Any], queue: list[Any], storage: dict[str, Any], version: dict[str, Any]) -> WidgetData:
        entries = [one for one in containers.values() if isinstance(one, dict)] if isinstance(containers, dict) else []
        stopped = sorted(str(one.get("container") or "?").removesuffix("-mailcow") for one in entries if one.get("state") != "running")
        deferred = sum(1 for one in queue if isinstance(one, dict) and one.get("queue_name") == "deferred")
        used = _percent(storage.get("used_percent"))
        secondary: list[dict[str, Any]] = [{"label": "Queue", "value": len(queue), "metric": "queue"}]
        if used is not None:
            secondary.append({"label": "Storage", "value": f"{used:.0f} %"})
        if version.get("version"):
            secondary.append({"label": "Version", "value": str(version["version"])})
        notice = f"Not running: {', '.join(stopped[:4])}{' …' if len(stopped) > 4 else ''}" if stopped else ""
        if not notice and deferred:
            notice = f"{deferred} mail{'s' if deferred != 1 else ''} deferred."
        return WidgetData(
            status="bad" if stopped else "warn" if deferred or (used or 0) >= 90 else "ok",
            primary={"label": "Containers", "value": f"{len(entries) - len(stopped)}/{len(entries)}"},
            secondary=secondary,
            metrics={"queue": float(len(queue))},
            meta={"notice": notice},
        )

    @staticmethod
    def _queue(queue: list[Any], limit: int) -> WidgetData:
        rows = []
        for mail in queue:
            if not isinstance(mail, dict):
                continue
            recipients = [str(one) for one in mail.get("recipients") or []]
            first = recipients[0] if recipients else "?"
            address, _, reason = first.partition(" (")
            rows.append({
                "id": str(mail.get("queue_id") or len(rows)),
                "title": f"{mail.get('sender') or '<>'} → {address}" + (f" +{len(recipients) - 1}" if len(recipients) > 1 else ""),
                "subtitle": " · ".join(part for part in (str(mail.get("queue_name") or ""), reason.rstrip(")"),
                                                         f"{ago(mail.get('arrival_time'))} ago" if ago(mail.get("arrival_time")) else "") if part),
                "value": human_bytes(float(mail.get("message_size") or 0)),
                "status": "warn" if mail.get("queue_name") == "deferred" else "bad" if mail.get("queue_name") == "hold" else "ok",
            })
        return WidgetData(status="warn" if rows else "ok", items=rows[:limit], meta={"empty": "The queue is empty"})

    @staticmethod
    def _mailboxes(mailboxes: list[Any], limit: int) -> WidgetData:
        rows = []
        for box in mailboxes:
            if not isinstance(box, dict):
                continue
            percent = _percent(box.get("percent_in_use"))
            quota = float(box.get("quota") or 0)
            used = float(box.get("quota_used") or 0)
            rows.append({
                "id": str(box.get("username")),
                "title": str(box.get("username") or "?"),
                "subtitle": " · ".join(part for part in (
                    "inactive" if str(box.get("active")) in ("0", "False") else "",
                    f"{int(box.get('messages') or 0)} messages",
                    f"signed in {ago(box['last_imap_login'])} ago" if box.get("last_imap_login") else "") if part),
                "value": f"{human_bytes(used)} of {human_bytes(quota)}" if quota else human_bytes(used),
                "progress": round(percent, 1) if percent is not None else None,
                "status": "bad" if (percent or 0) >= 90 else "warn" if (percent or 0) >= 75 else "ok",
            })
        rows.sort(key=lambda row: -(row["progress"] or 0))
        return WidgetData(status="ok", items=rows[:limit], meta={"empty": "No mailbox"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        limit = max(1, int(options.get("limit") or 8))
        names = ("postfix", "dovecot", "rspamd", "sogo", "nginx", "mysql", "redis", "clamd", "netfilter", "unbound")
        containers = {f"{name}-mailcow": {"type": "info", "container": f"{name}-mailcow", "state": "running"} for name in names}
        queue = [{"queue_name": "deferred", "queue_id": "4Q1Zt52kXqz9tFB", "arrival_time": 0, "message_size": 18_442,
                  "sender": "newsletter@example.com", "recipients": ["anna@example.org (connect to mx.example.org[203.0.113.7]:25: Connection timed out)"]}] \
            if fake.flicker("mailcow-q", tick, 0.4) else []
        if widget_kind == "queue":
            return self._queue(queue, limit)
        if widget_kind == "mailboxes":
            boxes = [{"username": f"{name}@example.com", "active": "1", "quota": 5 * 1024 ** 3, "quota_used": int(5 * 1024 ** 3 * share),
                      "percent_in_use": round(share * 100), "messages": int(share * 20_000), "last_imap_login": 0}
                     for name, share in (("anna", 0.82), ("ben", 0.41), ("info", 0.12))]
            return self._mailboxes(boxes, limit)
        return self._status(containers, queue, {"used_percent": f"{fake.walk('mailcow-disk', tick, 31, 33):.0f}%"}, {"version": "2026-09a"})


ADAPTER = MailcowAdapter()
