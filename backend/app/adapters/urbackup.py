"""UrBackup: which machine was backed up when, and which one has stopped being.

UrBackup's web interface is also its API: every action is ``/x?a=<action>``,
answered as JSON. Three of them are read-only and carry everything a card
wants: ``status`` for the clients, ``usage`` for what they occupy, ``progress``
for what is running right now, and one starts a backup.

The way in is the one the server's own interface uses, and it is unusual:

1. ``a=login`` with nothing at all succeeds when the server has no users yet.
2. Otherwise ``a=salt`` hands out a salt, a random value and a session, and the
   password is folded into md5, then PBKDF2-SHA256 over the given number of
   rounds, then md5 again together with the random value.
3. ``a=login`` with that, and the session is carried in every later call.

⚠️ The md5 in there is UrBackup's protocol, not a choice made here: the server
compares exactly that. The password itself never leaves this process, which is
the point of the exchange.

⚠️ A session expires and the server then answers a document without the key
that was asked for, not an error. Every call therefore logs in again once and
retries before it gives up, or the cards would go blank until a restart.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from . import demo as fake
from .base import (
    Action,
    Adapter,
    AdapterError,
    Context,
    Field,
    WidgetData,
    WidgetType,
    ago,
    base_url,
    human_bytes,
    measured,
)

#: A session lasts far longer, but re-reading it costs one call and this way a
#: server restart is noticed within the hour.
SESSION_SECONDS = 3600
STATUS_SECONDS = 30
USAGE_SECONDS = 600
#: What ``progress`` calls the thing it is doing. The numbers are the server's.
ACTIONS = {
    0: "Indexing", 1: "File backup", 2: "Full file backup", 3: "Image backup",
    4: "Full image backup", 5: "Resumed file backup", 6: "Resumed full file backup",
    7: "Restoring file backup", 8: "Restoring image backup", 9: "Updating",
}
#: What the buttons offer. The server's own names for a backup somebody asks for.
STARTS = {
    "incr_file": "Back up files",
    "full_file": "Full file backup",
    "incr_image": "Back up image",
    "full_image": "Full image backup",
}


def when(value: Any) -> float | None:
    """The moment a backup finished, if the server gave one as a number.

    ⚠️ ``lastbackup`` is an epoch second on a current server and a formatted
    date on an older one, and "-" when there has never been a backup. Only a
    number can be turned into an age; the rest is shown as it came.
    """
    if isinstance(value, bool) or value in (None, "", "-"):
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    text = str(value).strip()
    return float(text) if text.isdigit() and text != "0" else None


class UrBackupAdapter(Adapter):
    kind = "urbackup"
    label = "UrBackup"
    category = "hosts"
    description = "Which machine was backed up when, which one has fallen behind, and what is running now."
    icon = "urbackup"
    beta = True
    docs_url = "https://www.urbackup.org/administration_manual.html"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://urbackup:55414",
              help="The web interface. Its API lives under /x, which is added for you."),
        Field("username", "User name", placeholder="admin",
              help="Leave empty for a server that has no users yet; it lets anybody in until the first one is made."),
        Field("password", "Password", type="password", secret=True,
              help="Folded into a hash before it is sent, the way the server's own interface does it."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="clients", label="Clients", description="Every machine with its last file and image backup, the ones in trouble first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120, metrics=("behind",),
                   options=(
                       Field("stale_days", "Call it late after (days)", type="number", default=3,
                             help="A machine that has not been backed up for this long turns yellow. A laptop that is away for a week is not a fault, so keep it generous."),
                       Field("images", "Count image backups too", type="bool", default=True,
                             help="Off judges a client by its file backups alone, which is what a server without image backups wants."),
                       Field("starting", "Offer a backup button", type="bool", default=False,
                             help="Adds a button per machine that asks the server for an incremental file backup."),
                       Field("limit", "Entries", type="number", default=12),
                   )),
        WidgetType(kind="summary", label="Backups", description="How many machines are backed up, how many are late, and how much storage they occupy.",
                   renderer="value", default_size=(3, 2), refresh_seconds=300, metrics=("behind", "used"),
                   options=(Field("stale_days", "Call it late after (days)", type="number", default=3),)),
        WidgetType(kind="running", label="Running now", description="The backups, restores and index runs in progress, with how far along they are.",
                   renderer="list", default_size=(3, 2), refresh_seconds=30),
    )

    # -- talking to the server ------------------------------------------------

    @staticmethod
    def _api(config: dict[str, Any]) -> str:
        """The API address. Somebody will paste the interface's own address."""
        address = base_url(config)
        return address if address.endswith("/x") else f"{address}/x"

    async def _post(self, config: dict[str, Any], ctx: Context, action: str, params: dict[str, Any]) -> dict[str, Any]:
        response = await ctx.request(
            "POST", f"{self._api(config)}?a={action}", data=params,
            verify=not config.get("insecure"), auth_errors=False,
        )
        if response.status_code >= 400:
            raise AdapterError(f"UrBackup answered with HTTP {response.status_code}.", code="http_error",
                               hint="Is this the web interface's port, 55414 by default?")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("UrBackup did not answer with JSON.", code="not_json",
                               hint="The address has to be the web interface, whose API is under /x.") from failure
        if not isinstance(answer, dict):
            raise AdapterError("That address answers, but not the way UrBackup does.", code="not_urbackup")
        return answer

    async def _login(self, config: dict[str, Any], ctx: Context) -> str:
        """A session id, by the exchange the server's own interface uses."""
        user = str(config.get("username") or "")
        password = str(config.get("password") or "")
        # A server without users answers this, and nothing else is needed.
        anonymous = await self._post(config, ctx, "login", {})
        if anonymous.get("success") and anonymous.get("session"):
            return str(anonymous["session"])
        if not user:
            raise AdapterError("This server wants a user name.", code="auth_failed",
                               hint="Anonymous access only works until the first user is created.")
        salted = await self._post(config, ctx, "salt", {"username": user})
        if not salted.get("ses"):
            raise AdapterError(f"UrBackup knows no user called {user!r}.", code="auth_failed")
        session = str(salted["ses"])
        if not salted.get("salt"):
            raise AdapterError("UrBackup offered no salt for this user.", code="auth_failed",
                               hint="An LDAP user cannot be used here; make a local one for HexDeck.")
        salt = str(salted["salt"])
        # ⚠️ md5 and the rounds below are the server's protocol, not a choice.
        digest = hashlib.md5((salt + password).encode(), usedforsecurity=False).digest()
        folded = digest.hex()
        rounds = int(salted.get("pbkdf2_rounds") or 0)
        if rounds > 0:
            folded = hashlib.pbkdf2_hmac("sha256", digest, salt.encode(), rounds).hex()
        answer = await self._post(config, ctx, "login", {
            "username": user, "password": hashlib.md5((str(salted.get("rnd") or "") + folded).encode(), usedforsecurity=False).hexdigest(),
            "ses": session,
        })
        if not answer.get("success"):
            raise AdapterError("UrBackup refused the password.", code="auth_failed")
        return session

    async def _session(self, config: dict[str, Any], ctx: Context) -> str:
        kept = ctx.cache.get("urbackup:session")
        if kept and kept[0] > time.monotonic():
            return str(kept[1])
        session = await self._login(config, ctx)
        ctx.cache["urbackup:session"] = (time.monotonic() + SESSION_SECONDS, session)
        return session

    async def _call(self, config: dict[str, Any], ctx: Context, action: str, expect: str,
                    params: dict[str, Any] | None = None, again: bool = True) -> Any:
        session = await self._session(config, ctx)
        answer = await self._post(config, ctx, action, {**(params or {}), "ses": session})
        if expect not in answer:
            # An expired session is answered with a document that simply lacks
            # what was asked for, so there is nothing else to test here.
            if again:
                ctx.cache.pop("urbackup:session", None)
                return await self._call(config, ctx, action, expect, params, again=False)
            raise AdapterError(f"UrBackup answered without the {expect} it was asked for.", code="bad_answer",
                               hint="Has this user the rights to see every client?")
        return answer[expect]

    async def _clients(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        found = await self._call(config, ctx, "status", "status")
        return [one for one in found or [] if isinstance(one, dict)]

    async def _usage(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        found = await self._call(config, ctx, "usage", "usage")
        return [one for one in found or [] if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        clients = await self._clients(config, ctx)
        online = sum(1 for one in clients if one.get("online"))
        return f"UrBackup answers with {len(clients)} clients, {online} of them online."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "running":
            return self._running(await self._call(config, ctx, "progress", "progress"))
        clients = await self._clients(config, ctx)
        if widget_kind == "summary":
            return self._summary(clients, await self._usage(config, ctx), options)
        return self._client_list(clients, options)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id not in STARTS:
            raise AdapterError("Unknown action.", code="no_such_action")
        name = str(params.get("client") or "")
        if not name:
            raise AdapterError("No client was named.", code="missing_param")
        session = await self._session(config, ctx)
        answer = await self._post(config, ctx, "start_backup", {"start_client": name, "start_type": action_id, "ses": session})
        started = [one for one in answer.get("result") or [] if isinstance(one, dict)]
        if not started or not started[0].get("start_ok"):
            raise AdapterError(f"UrBackup did not start a backup of {name}.", code="not_started",
                               hint="A client that is offline cannot be backed up, and a backup may already be running.")
        return f"{STARTS[action_id]} of {name} started."

    # -- the cards -----------------------------------------------------------

    @classmethod
    def _judge(cls, client: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
        """One client, as a row and a verdict.

        ⚠️ A client the server has disabled image backups for has no image
        backup and never will; counting that as missing would paint half a
        normal server yellow.
        """
        stale = max(0.0, float(options.get("stale_days") or 3)) * 86400
        files, images = when(client.get("lastbackup")), when(client.get("lastbackup_image"))
        wants_images = bool(options.get("images", True)) and not client.get("image_disabled") and not client.get("image_not_supported")
        wants_files = not client.get("file_disabled")
        newest = max([one for one in (files, images if wants_images else None) if one], default=None)
        behind = (time.time() - newest) if newest else None
        trouble = ""
        if client.get("last_filebackup_issues"):
            trouble = "Last file backup had issues"
        elif wants_files and not client.get("file_ok"):
            trouble = "No file backup"
        elif wants_images and not client.get("image_ok"):
            trouble = "No image backup"
        elif client.get("no_backup_paths"):
            trouble = "Nothing is set to be backed up"
        if trouble:
            state = "bad"
        elif behind is None or behind > stale:
            state = "warn"
        else:
            state = "ok"
        parts = [trouble] if trouble else []
        if wants_images and images:
            parts.append(f"image {ago(images)}")
        if not client.get("online"):
            parts.append("offline")
        return {
            "state": state,
            "behind": behind,
            "row": {
                "id": client.get("id"),
                "title": str(client.get("name") or "?"),
                "subtitle": " · ".join(parts),
                # "-" is how the server writes "there has never been one".
                "value": ago(files) if files else (str(client.get("lastbackup")) if str(client.get("lastbackup") or "-") not in ("-", "0") else "never"),
                "status": state,
            },
        }

    @classmethod
    def _client_list(cls, clients: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        judged = [cls._judge(one, options) for one in clients]
        order = {"bad": 0, "warn": 1, "ok": 2}
        # Worst first, and inside a state the one that has waited longest.
        ranked = sorted(judged, key=lambda one: (order.get(one["state"], 3), -(one["behind"] or 0)))
        rows = []
        for one in ranked:
            row = dict(one["row"])
            if options.get("starting"):
                row["actions"] = [Action(id="incr_file", label=STARTS["incr_file"], icon="download",
                                         params={"client": row["title"]}).model_dump()]
            rows.append(row)
        behind = sum(1 for one in judged if one["state"] in ("bad", "warn"))
        return WidgetData(
            status="bad" if any(one["state"] == "bad" for one in judged) else "warn" if behind else "ok",
            items=rows[: max(1, int(options.get("limit") or 12))],
            primary={"label": "Late", "value": behind},
            secondary=[{"label": "Clients", "value": len(judged)}],
            metrics=measured({"behind": float(behind)}),
            meta={"empty": "This server has no clients yet."},
        )

    @classmethod
    def _summary(cls, clients: list[dict[str, Any]], usage: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        judged = [cls._judge(one, options) for one in clients]
        behind = sum(1 for one in judged if one["state"] in ("bad", "warn"))
        failing = sum(1 for one in judged if one["state"] == "bad")
        used = 0.0
        for one in usage:
            try:
                used += float(one.get("used") or 0)
            except (TypeError, ValueError):
                continue
        oldest = max([one["behind"] for one in judged if one["behind"] is not None], default=None)
        secondary: list[dict[str, Any]] = [{"label": "Online", "value": sum(1 for one in clients if one.get("online"))}]
        if behind:
            secondary.append({"label": "Late", "value": behind, "metric": "behind"})
        if oldest is not None:
            secondary.append({"label": "Oldest", "value": ago(time.time() - oldest)})
        if used:
            secondary.append({"label": "Stored", "value": human_bytes(used), "metric": "used"})
        return WidgetData(
            status="bad" if failing else "warn" if behind else "ok",
            primary={"label": "Clients", "value": len(clients)},
            secondary=secondary,
            metrics=measured({"behind": float(behind), "used": used or None}),
            meta={"empty": "This server has no clients yet."},
        )

    @staticmethod
    def _running(progress: list[dict[str, Any]] | None) -> WidgetData:
        running = [one for one in progress or [] if isinstance(one, dict)]
        items = []
        for one in running:
            try:
                done = float(one.get("pcdone") or 0)
            except (TypeError, ValueError):
                done = 0.0
            details = str(one.get("details") or "")
            items.append({
                "title": str(one.get("name") or "?"),
                "subtitle": " · ".join(part for part in (ACTIONS.get(int(one.get("action") or 0), "Working"),
                                                         "paused" if one.get("paused") else "", details[:40]) if part),
                "value": f"{done:.0f}%",
                "progress": done,
                "status": "warn" if one.get("paused") else "ok",
            })
        return WidgetData(
            status="ok",
            items=items,
            primary={"label": "Running", "value": len(items)},
            meta={"empty": "Nothing is running."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = time.time()
        clients = [
            {"id": 1, "name": "nas", "lastbackup": now - 3600, "lastbackup_image": now - 7200, "file_ok": True, "image_ok": True, "online": True},
            {"id": 2, "name": "desk", "lastbackup": now - 90000, "lastbackup_image": now - 90000, "file_ok": True, "image_ok": True, "online": True},
            {"id": 3, "name": "laptop", "lastbackup": now - 900_000, "lastbackup_image": "-", "file_ok": True, "image_ok": False,
             "image_disabled": True, "online": False},
            {"id": 4, "name": "pi", "lastbackup": "-", "lastbackup_image": "-", "file_ok": False, "image_ok": False,
             "image_not_supported": True, "online": True},
        ]
        if widget_kind == "running":
            done = fake.walk("urbackup-progress", tick, 4, 96)
            return self._running([{"name": "desk", "action": 1, "pcdone": done, "details": "C:\\Users", "paused": False}])
        if widget_kind == "summary":
            return self._summary(clients, [{"name": "nas", "used": 1_412_000_000_000}, {"name": "desk", "used": 318_000_000_000}], options)
        return self._client_list(clients, options)


ADAPTER = UrBackupAdapter()
