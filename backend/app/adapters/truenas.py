"""TrueNAS SCALE through its current API, with the REST API v2 behind it.

TrueNAS has two APIs. The REST API under ``/api/v2.0`` is deprecated since
25.04, and on 25.10 it answers a key of a **read-only administrator** with 403
for everything, including ``system/info`` (issue #4, measured on 25.10.7). The
current API is JSON-RPC 2.0 over a WebSocket at ``/api/current``, and there
the same key reads the system, the pools and the alerts.

⚠️ **The key never goes over plain http to the current API.** TrueNAS revokes
a key the moment it arrives over ``ws://``, for good, with "Attempt to use
over an insecure transport". Measured on 25.10.7: one login over ``ws://`` and
the key was dead over ``wss://`` as well. The same kind of key sent to the
REST API over http was not revoked. So an ``http://`` address keeps the REST
API and explains what to change when that is refused; only ``https://``
speaks the current API.

⚠️ A TrueNAS older than 25.04 has no ``/api/current``. Whatever turns the
handshake down, the REST API is asked instead, which those versions accept
from a read-only key. That an older TrueNAS says 404 there is expected, not
measured; 25.10.7 said 404 for ``/api/v99``.

⚠️ **The REST API is for those older versions only.** With a full
administrator's key it still answers on 25.04 and later, and that is the
trouble. Measured on 25.10.7 on 21.09.2026: TrueNAS counts every call that
signs in over REST and raises one alert, "Deprecated REST API usage", with the
count of the last 24 hours and the addresses they came from; eight calls from
the cards read "8 times". TrueNAS 26 removes the API (its list of
deprecations). The alert only goes away at no call at all, so reading the
version over REST first would keep it alive, at 24 calls a day.

So a TrueNAS with the current API is recognised without a key: a plain GET of
``/api/current`` answered 400 ("Can Upgrade only to WebSocket"), a path that
does not exist 404, over http and over https alike, and neither moved the
count. Only a 404 there lets the REST API be asked, and what it says its
version is remains the second line, for a proxy that says 404 on TrueNAS'
behalf.

The disks, apps and tasks cards, the scrub on the pools card and the update
on the system card were written from the middleware's source at TS-25.10.7
(``api/v25_10_0`` and the plugins ``disk_/temperature.py``, ``snapshot.py``,
``replication.py``, ``zettarepl.py``, ``pool_/scrub.py``,
``update_/status.py``), not yet run against a TrueNAS. Every method they call
is open to the Read-Only Administrator role (``role.py``).

The interfaces, services and VMs cards, and the notifications for a hot disk,
a failed task and a waiting update, come from nexdeck 0.46.0 (its issue #49),
which wrote them from the same source; also not yet run against a TrueNAS.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import ssl
import time
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets.exceptions import InvalidStatus, WebSocketException

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Detected,
    Field,
    Status,
    Unreachable,
    WidgetData,
    WidgetType,
    ago,
    base_url,
    duration_short,
    guard_outbound,
    human_bytes,
    human_rate,
    join_parts,
    percent,
    percent_text,
    status_from_percent,
)

#: What each card reads: the JSON-RPC method and the same thing on REST v2.
#: The live numbers TrueNAS publishes as an event, not a method: CPU usage
#: and memory in use. Subscribed to for one event and let go again. Over
#: REST there is nothing like it, so the card falls back to the load average
#: and the memory size there. Shapes read from the middleware's source:
#: 25.x sends ``cpu.cpu.usage`` and ``memory.physical_memory_total`` /
#: ``physical_memory_available``; 24.10 sends ``cpu.average.usage`` and
#: ``memory.classes.unused``.
REALTIME = "reporting.realtime"
REALTIME_WAIT = 8.0

READS = {
    "system.info": "/system/info",
    "pool.query": "/pool",
    "alert.list": "/alert/list",
}

#: The arguments of the methods that take some. ``disk.temperatures`` with
#: no names is every disk, and the second one asks for each disk's critical
#: temperature beside the current one: ``{"sda": [41.0, 70.0]}``.
PARAMS: dict[str, tuple[Any, ...]] = {
    "disk.temperatures": ([], True),
    # Without ``extra.pools`` every disk's ``pool`` is None (plugins/disk.py).
    "disk.query": ([], {"extra": {"pools": True}}),
}

#: Read over the current API only. Over REST these cards say they need it.
CURRENT_ONLY = ("disk.query", "disk.temperatures", "app.query", "pool.snapshottask.query", "replication.query", "update.status",
                "network.general.summary", "service.query", "vm.query", "virt.instance.query")

#: Methods a TrueNAS may not have, or not on this version. An error from one of
#: them is an empty answer; a card that reads several still shows the rest.
#: The Incus instances are gone from some builds. A refused role is not caught
#: here: that is ``auth_failed``.
OPTIONAL = {"virt.instance.query", "vm.query"}

#: How long the interfaces card keeps the addresses; the traffic is read anew.
SUMMARY_SECONDS = 300.0

#: How long what ``update.status`` said is kept; see ``_update``.
UPDATE_SECONDS = 3600.0

#: A disk this warm, in degrees Celsius, is shown in amber; at its own
#: critical temperature, or at the second number when it names none, in red.
DISK_WARM = 50.0
DISK_HOT = 60.0

APP_STATES = {"RUNNING": ("ok", "Running"), "DEPLOYING": ("warn", "Starting"), "STOPPING": ("warn", "Stopping"),
              "STOPPED": ("unknown", "Stopped"), "CRASHED": ("bad", "Crashed")}
TASK_STATES = {"FINISHED": ("ok", "Done"), "RUNNING": ("ok", "Running"), "WAITING": ("unknown", "Waiting"),
               "PENDING": ("unknown", "Not run yet"), "HOLD": ("warn", "On hold"), "ERROR": ("bad", "Failed")}

#: How long a TrueNAS without ``/api/current`` is not asked again.
LEGACY_SECONDS = 3600.0

#: How long the version read over REST is kept, and from which one on REST is refused.
VERSION_SECONDS = 3600.0
REST_DEPRECATED_FROM = (25, 4)

HTTP_REFUSED_HINT = (
    "Over http:// only the old REST API is safe, and TrueNAS 25.04 and later let only a full "
    "administrator's key use it. Change the URL to https:// and a read-only administrator's key "
    "works, because HexDeck then uses the current API. Over plain http TrueNAS would revoke the key."
)


class TruenasAdapter(Adapter):
    kind = "truenas"
    label = "TrueNAS"
    category = "nas"
    description = "Pools, disks, apps, interfaces, services, VMs and snapshot tasks, with load and alerts."
    icon = "truenas"
    docs_url = "https://www.truenas.com/docs/api/"
    #: System, pools and alerts against TrueNAS SCALE 25.10.7 with a read-only and a full key, 18.09.2026.
    beta = False
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://truenas.local",
              help="Use https://. Over http:// only a TrueNAS before 25.04 is read, and only with a full administrator's key."),
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="Top-right user menu > API Keys. A user with the Read-Only Administrator role is enough."),
        Field("insecure", "Ignore TLS errors", type="bool", default=True),
    )
    widgets = (
        WidgetType(kind="system", label="System", description="Load, memory, uptime and open alerts.", renderer="stats", default_size=(4, 2), refresh_seconds=30, metrics=("load",)),
        WidgetType(kind="pools", label="Pools", description="Every pool with usage and health.", renderer="list", default_size=(3, 2), refresh_seconds=120),
        WidgetType(kind="alerts", label="Alerts", description="Open alerts by level.", renderer="list", default_size=(3, 2), refresh_seconds=60),
        WidgetType(kind="disks", label="Disks", description="Every disk with its temperature, model and pool, the hottest first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=12),)),
        WidgetType(kind="apps", label="Apps", description="The apps on TrueNAS with their state, and which have an update.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("limit", "Entries", type="number", default=12),)),
        WidgetType(kind="tasks", label="Snapshots and replication", description="The periodic snapshot and replication tasks with how each last ended.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120,
                   options=(Field("limit", "Entries", type="number", default=12),)),
        WidgetType(kind="interfaces", label="Interfaces", description="Every network interface with its link, address and live traffic. Needs TrueNAS 25.04 or later.",
                   renderer="list", default_size=(3, 2), refresh_seconds=30, metrics=("down", "up"),
                   parts=(("address", "Address"), ("speed", "Link speed"), ("value", "Traffic"))),
        WidgetType(kind="services", label="Services", description="The services that start with TrueNAS, such as SMB, NFS and SSH, and whether they run. Needs TrueNAS 25.04 or later.",
                   renderer="list", default_size=(3, 2), refresh_seconds=60,
                   options=(Field("all_services", "Show services that are switched off", type="bool", default=False),)),
        WidgetType(kind="vms", label="VMs and containers", description="Virtual machines and containers with their state. Needs TrueNAS 25.04 or later.",
                   renderer="list", default_size=(3, 2), refresh_seconds=30),
    )

    # -- the REST API v2 -------------------------------------------------------

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        return {"Authorization": f"Bearer {config.get('api_key', '')}"}

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 10) -> Any:
        return await ctx.get_json(f"{base_url(config)}/api/v2.0{path}", headers=self._headers(config), verify=not config.get("insecure", True), cache_seconds=cache)

    async def _has_current_api(self, config: dict[str, Any], ctx: Context, fresh: bool = False) -> bool:
        """Whether this TrueNAS has ``/api/current``, asked without the key; see the top.

        ⚠️ No ``Authorization`` header here, ever. It is what makes a call count
        for TrueNAS' alert, and over http it would be the key in the clear on
        its way to an API that revokes it for that.

        Only 404 means no. A redirect to https, a proxy's 502 and the 400 of
        TrueNAS itself all belong to somebody who should not get REST calls.
        """
        now = time.monotonic()
        known = ctx.cache.get("truenas:current-api")
        if not fresh and known and known[0] > now:
            return known[1]
        answer = await ctx.request("GET", f"{base_url(config)}/api/current", verify=not config.get("insecure", True), timeout=10, auth_errors=False)
        there = answer.status_code != 404
        ctx.cache["truenas:current-api"] = (now + VERSION_SECONDS, there)
        return there

    async def _rest(self, config: dict[str, Any], ctx: Context, methods: list[str], cache: float, turned_down: int | None = None) -> dict[str, Any]:
        """The REST API, for a TrueNAS that has nothing newer; see the top.

        ``turned_down`` is the status the WebSocket handshake was refused with,
        when that is why REST is asked at all.

        ⚠️ Only the version is kept for the hour, never the answer it came in.
        The system card takes load and uptime from ``system/info`` too, and an
        answer kept for an hour would freeze that card on exactly the older
        versions this fallback is for.
        """
        now = time.monotonic()
        # A handshake that said 404 has asked the question below already.
        if turned_down != 404 and await self._has_current_api(config, ctx, fresh=not cache):
            raise _rest_refused("", base_url(config), turned_down)
        fresh: dict[str, Any] = {}
        known = ctx.cache.get("truenas:version")
        if cache and known and known[0] > now:
            label = known[1]
        else:
            # Never longer than the system card keeps it: the answer is shared by address.
            info = await self._get(config, ctx, READS["system.info"], cache=min(cache, 10))
            label = str((info or {}).get("version") or "")
            ctx.cache["truenas:version"] = (now + VERSION_SECONDS, label)
            fresh["system.info"] = info
        if _version(label) >= REST_DEPRECATED_FROM:
            # Not a TrueNAS without the current API after all, whatever the
            # handshake said: the WebSocket is tried again next time.
            ctx.cache.pop("truenas:legacy", None)
            raise _rest_refused(label, base_url(config), turned_down)
        for method in methods:
            if method not in fresh:
                # The live statistics are an event of the current API; REST has none.
                fresh[method] = None if method == REALTIME or method in CURRENT_ONLY else await self._get(config, ctx, READS[method], cache=cache)
        return {method: fresh[method] for method in methods}

    # -- the current API -------------------------------------------------------

    async def _open_socket(self, url: str, config: dict[str, Any]) -> Any:
        """The WebSocket, as its own method so a test can hand in a fake one."""
        options: dict[str, Any] = {}
        if config.get("insecure", True):
            relaxed = ssl.create_default_context()
            relaxed.check_hostname = False
            relaxed.verify_mode = ssl.CERT_NONE
            options["ssl"] = relaxed
        return await websockets.connect(url, open_timeout=10, max_size=16 * 1024 * 1024, **options)

    async def _rpc(self, config: dict[str, Any], methods: list[str]) -> dict[str, Any]:
        """Log in with the key and call each method once, on one connection."""
        address = base_url(config)
        if not address.startswith("https://"):
            # The one line between a key and its revocation; see the top.
            raise AdapterError("The current TrueNAS API is only spoken over https.", code="bad_scheme")
        url = "wss://" + address.removeprefix("https://") + "/api/current"
        ids = itertools.count(1)

        async def call(socket: Any, method: str, *params: Any) -> Any:
            number = next(ids)
            await socket.send(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": list(params)}))
            while True:
                message = json.loads(await socket.recv())
                # Events and answers to something else are skipped.
                if message.get("id") != number:
                    continue
                if "error" in message:
                    error = message["error"] or {}
                    data = error.get("data") or {}
                    reason = str(data.get("reason") or error.get("message") or "TrueNAS refused the call.")
                    if data.get("errname") in ("EACCES", "ENOTAUTHENTICATED"):
                        raise AdapterError(f"TrueNAS refused {method}: {reason}", code="auth_failed",
                                           hint="The key's user needs at least the Read-Only Administrator role.")
                    raise AdapterError(f"TrueNAS answered {method} with an error: {reason}", code="rpc_error")
                return message.get("result")

        async def realtime(socket: Any) -> dict[str, Any] | None:
            """One event of the live statistics, or None if none came in time.

            ⚠️ The subscription is an event source with an argument, written
            ``name:{json}`` in the event name; the event itself arrives as a
            ``collection_update`` notification with ``msg`` "added" and the
            numbers under ``fields``. The socket is closed right after, which
            ends the subscription on TrueNAS' side.

            ⚠️ The ``collection`` of the event is that whole name, argument
            and all: ``reporting.realtime:{"interval": 2}``. Compared for
            equality with the bare name it never matched, no event was ever
            recognised, and the card quietly fell back to the load average.
            """
            await call(socket, "core.subscribe", f"{REALTIME}:{json.dumps({'interval': 2})}")
            try:
                async with asyncio.timeout(REALTIME_WAIT):
                    while True:
                        message = json.loads(await socket.recv())
                        params = message.get("params") or {}
                        collection = str(params.get("collection") or "")
                        if collection.split(":", 1)[0] != REALTIME:
                            continue
                        if message.get("method") == "collection_update" and params.get("msg") == "added":
                            return params.get("fields") or None
                        if message.get("method") == "notify_unsubscribed":
                            # The key's role may not read the statistics. Waiting
                            # the whole timeout for an event that will not come
                            # would cost the card eight seconds on every refresh.
                            return None
            except TimeoutError:
                return None

        try:
            async with asyncio.timeout(30):
                socket = await self._open_socket(url, config)
                try:
                    if await call(socket, "auth.login_with_api_key", str(config.get("api_key") or "")) is not True:
                        raise AdapterError(
                            "TrueNAS refused the API key.", code="auth_failed",
                            hint="Check the key. TrueNAS revokes a key for good once it was sent over plain http; then only a new one helps.",
                        )
                    answers: dict[str, Any] = {}
                    for method in methods:
                        if method == REALTIME:
                            answers[method] = await realtime(socket)
                            continue
                        try:
                            answers[method] = await call(socket, method, *PARAMS.get(method, ()))
                        except AdapterError as error:
                            if method not in OPTIONAL or error.code != "rpc_error":
                                raise
                            answers[method] = None
                    return answers
                finally:
                    await socket.close()
        except InvalidStatus as error:
            raise _NoCurrentApi(error.response.status_code) from error
        except TimeoutError as error:
            raise Unreachable("TrueNAS did not answer in time.") from error
        except (OSError, WebSocketException) as error:
            raise Unreachable(f"TrueNAS could not be reached: {error.__class__.__name__}.") from error

    async def _read(self, config: dict[str, Any], ctx: Context, methods: list[str], cache: float = 10) -> dict[str, Any]:
        """What the cards need, from whichever API this TrueNAS should be asked."""
        guard_outbound(base_url(config))
        now = time.monotonic()
        found: dict[str, Any] = {}
        missing = []
        for method in methods:
            hit = ctx.cache.get(f"truenas:{method}")
            if cache and hit and hit[0] > now:
                found[method] = hit[1]
            else:
                missing.append(method)
        if not missing:
            return found
        legacy = ctx.cache.get("truenas:legacy")
        if base_url(config).startswith("https://") and not (legacy and legacy > now):
            try:
                fresh = await self._rpc(config, missing)
            except _NoCurrentApi as gone:
                # 404 is a TrueNAS without the current API, and that does not
                # change within the hour. Anything else is more likely a
                # reverse proxy that does not pass WebSockets on, and that
                # gets fixed; it is asked again every time.
                if gone.status == 404:
                    ctx.cache["truenas:legacy"] = now + LEGACY_SECONDS
                try:
                    fresh = await self._rest(config, ctx, missing, cache, turned_down=gone.status)
                except AuthFailed as error:
                    raise AdapterError(
                        f"TrueNAS turned the WebSocket at /api/current down with HTTP {gone.status}, "
                        "and its old REST API refused the key.", code="auth_failed",
                        hint="A reverse proxy in front of TrueNAS has to pass WebSockets on. "
                             "The old REST API accepts only a full administrator's key on TrueNAS 25.04 and later.",
                    ) from error
        else:
            # Over https this branch is only reached within the hour after a
            # handshake that said 404, and that answer still stands.
            remembered = 404 if base_url(config).startswith("https://") else None
            try:
                fresh = await self._rest(config, ctx, missing, cache, turned_down=remembered)
            except AuthFailed as error:
                if base_url(config).startswith("http://"):
                    raise AdapterError("TrueNAS refused the API key on its REST API.", code="auth_failed",
                                       hint=HTTP_REFUSED_HINT) from error
                raise
        for method, value in fresh.items():
            ctx.cache[f"truenas:{method}"] = (now + cache, value)
        return {**found, **fresh}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        info = (await self._read(config, ctx, ["system.info"], cache=0))["system.info"] or {}
        return f"TrueNAS {info.get('version', '?')} on {info.get('hostname', '?')} answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "system":
            read = await self._read(config, ctx, ["system.info", "alert.list", REALTIME])
            info = read["system.info"] or {}
            alerts = [a for a in read["alert.list"] or [] if not a.get("dismissed")]
            memory_total = float(info.get("physmem") or 0)
            cpu, memory_used = _live_numbers(read.get(REALTIME))
            if cpu is None:
                # Over REST, or no event in time: the load average per core stands in.
                load = float((info.get("loadavg") or [0])[0])
                cores = int(info.get("cores") or 1)
                cpu = round(100.0 * load / cores, 1)
                cpu_label = "Load"
            else:
                cpu_label = "CPU"
            if memory_used is not None and memory_total:
                memory_percent = round(100.0 * memory_used / memory_total, 1)
                memory = {"label": "Memory", "value": memory_percent, "unit": "%", "metric": "memory",
                          "text": f"{human_bytes(memory_used)} / {human_bytes(memory_total)}"}
                metrics = {"load": cpu, "memory": memory_percent}
                worst = max(cpu, memory_percent)
            else:
                memory = {"label": "Memory", "value": human_bytes(memory_total)}
                metrics = {"load": cpu}
                worst = cpu
            secondary = [memory, {"label": "Uptime", "value": duration_short(info.get("uptime_seconds"))}, {"label": "Alerts", "value": len(alerts)}]
            update = await self._update(config, ctx)
            if update:
                secondary.append({"label": "Update available", "value": update})
            return WidgetData(
                status="bad" if any(a.get("level") in ("CRITICAL", "ERROR") for a in alerts) else ("warn" if alerts else status_from_percent(worst)),
                primary={"label": cpu_label, "value": cpu, "unit": "%"},
                secondary=secondary,
                metrics=metrics,
            )
        if widget_kind == "pools":
            items = []
            for pool in (await self._read(config, ctx, ["pool.query"], cache=60))["pool.query"] or []:
                used = percent(pool.get("allocated"), pool.get("size"))
                scan, scan_state = _scan(pool.get("scan"))
                state = "unknown" if used is None else ("ok" if pool.get("healthy") and used < 90 else "warn")
                items.append({
                    "title": pool.get("name", "?"),
                    "subtitle": " · ".join(part for part in (f"{human_bytes(pool.get('allocated'))} of {human_bytes(pool.get('size'))}", str(pool.get("status", "?")), scan) if part),
                    "progress": used, "value": percent_text(used),
                    # A pool whose size did not come is not a healthy pool.
                    "status": "warn" if state == "ok" and scan_state == "warn" else state,
                })
            return WidgetData(items=items)
        limit = max(1, int(options.get("limit") or 12))
        if widget_kind == "disks":
            read = await self._read(config, ctx, ["disk.query", "disk.temperatures"], cache=300)
            return _disks(read["disk.query"], read["disk.temperatures"], limit)
        if widget_kind == "apps":
            return _apps((await self._read(config, ctx, ["app.query"], cache=30))["app.query"], limit)
        if widget_kind == "tasks":
            read = await self._read(config, ctx, ["pool.snapshottask.query", "replication.query"], cache=60)
            return _tasks(read["pool.snapshottask.query"], read["replication.query"], limit)
        if widget_kind == "interfaces":
            summary = (await self._read(config, ctx, ["network.general.summary"], cache=SUMMARY_SECONDS))["network.general.summary"]
            _current_only(summary)
            return _interfaces((await self._read(config, ctx, [REALTIME])).get(REALTIME), summary, options)
        if widget_kind == "services":
            services = (await self._read(config, ctx, ["service.query"], cache=30))["service.query"]
            _current_only(services)
            return _services(services, options)
        if widget_kind == "vms":
            read = await self._read(config, ctx, ["vm.query", "virt.instance.query"])
            return _machines(read["vm.query"], read["virt.instance.query"])
        alerts = [a for a in (await self._read(config, ctx, ["alert.list"]))["alert.list"] or [] if not a.get("dismissed")]
        items = [{"title": str(a.get("formatted") or a.get("text", "?"))[:120], "subtitle": _day(a.get("datetime")), "status": "bad" if a.get("level") in ("CRITICAL", "ERROR") else "warn"} for a in alerts]
        return WidgetData(status="bad" if any(i["status"] == "bad" for i in items) else ("warn" if items else "ok"), items=items or [], secondary=[{"label": "Open", "value": len(items)}])

    async def _update(self, config: dict[str, Any], ctx: Context) -> str | None:
        """The version TrueNAS offers to update to, or None.

        ⚠️ ``update.status`` asks the update server, so it is read once an
        hour, on a connection of its own: a TrueNAS that cannot reach that
        server, or a key whose role may not ask, leaves the rest of the card
        as it was, and is not asked again within the hour either.
        """
        now = time.monotonic()
        quiet = ctx.cache.get("truenas:update-quiet")
        if quiet and quiet > now:
            return None
        try:
            status = (await self._read(config, ctx, ["update.status"], cache=UPDATE_SECONDS))["update.status"]
        except AdapterError:
            ctx.cache["truenas:update-quiet"] = now + UPDATE_SECONDS
            return None
        new = ((status or {}).get("status") or {}).get("new_version") if isinstance(status, dict) else None
        return str(new.get("version") or "") or None if isinstance(new, dict) else None

    #: Above this, a pool has stopped being somebody's problem for later.
    FULL_PERCENT = 90.0

    def detect(self, widget_kind: str, before: WidgetData | None, after: WidgetData,
               options: dict[str, Any]) -> list[Detected]:
        """A pool that crossed into the last tenth of itself, and what the newer cards notice.

        ⚠️ Only on the crossing. A pool that sits at 94 per cent for a month
        is not news every five minutes; it became news once.

        ⚠️ A failed task and a waiting update are told only when they appear
        between two fetches, never on the first. A NAS with twenty apps behind
        their releases would otherwise send twenty messages the moment the
        card is put on a board, about nothing that just happened.
        """
        if widget_kind == "disks":
            return [Detected(event="disk_hot", title=f"{item.get('title')} is at {item.get('value')}", body=str(item.get("subtitle") or ""),
                             level=item.get("status", "warn"), key=f"disk_hot:{item.get('title')}:{item.get('status')}", quiet_seconds=86400)
                    for item in _newly(before, after, lambda item: item.get("status") in ("warn", "bad"), first=True,
                                       # From yellow to red is news of its own.
                                       same=lambda old, new: old.get("status") == new.get("status") or new.get("status") == "warn")][:5]
        if widget_kind == "tasks":
            return [Detected(event="backup_failed", title=f"{item.get('title')} failed", body=str(item.get("subtitle") or ""),
                             level="bad", key=f"backup_failed:{item.get('id')}", quiet_seconds=3600)
                    for item in _newly(before, after, lambda item: item.get("status") == "bad")][:5]
        if widget_kind == "apps":
            return [Detected(event="software_update", title=f"{item.get('title')}: update to {item['update']}" if item.get("update") != "image" else f"{item.get('title')}: new image",
                             level="ok", key=f"software_update:app:{item.get('title')}:{item.get('update')}", quiet_seconds=7 * 86400)
                    for item in _newly(before, after, lambda item: bool(item.get("update")), same=lambda a, b: a.get("update") == b.get("update"))][:5]
        if widget_kind == "system":
            now, was = _chip(after, "Update available"), _chip(before, "Update available") if before and not before.error else None
            if not now or before is None or before.error or now == was:
                return []
            return [Detected(event="software_update", title=f"TrueNAS {now} is available", level="ok",
                             key=f"software_update:truenas:{now}", quiet_seconds=7 * 86400)]
        if widget_kind not in ("pools", "volumes"):
            return []
        was = {}
        for item in (before.items if before and not before.error else []):
            was[str(item.get("title"))] = _percent(item)
        found = []
        for item in after.items:
            name = str(item.get("title") or "?")
            now = _percent(item)
            if now is None or now < self.FULL_PERCENT:
                continue
            earlier = was.get(name)
            if earlier is not None and earlier >= self.FULL_PERCENT:
                continue
            found.append(Detected(
                event="disk_filling",
                title=f"{name} is {now:.0f}% full",
                body=str(item.get("subtitle") or ""),
                level="warn",
                key=f"disk_filling:{name}",
                quiet_seconds=86400,
            ))
        return found[:5]

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        load = fake.walk("truenas-load", tick, 5, 30)
        if widget_kind == "system":
            return WidgetData(primary={"label": "Load", "value": load, "unit": "%"}, secondary=[{"label": "Memory", "value": "64.0 GB"}, {"label": "Uptime", "value": duration_short(12 * 86400 + tick)}, {"label": "Alerts", "value": 1}], metrics={"load": load}, status="warn")
        if widget_kind == "pools":
            return WidgetData(items=[{"title": "tank", "subtitle": "41.2 TB of 58.0 TB · ONLINE", "progress": 71.0, "value": "71%", "status": "ok"}, {"title": "fast", "subtitle": "1.2 TB of 1.8 TB · ONLINE", "progress": 66.6, "value": "67%", "status": "ok"}])
        limit = max(1, int(options.get("limit") or 12))
        if widget_kind == "disks":
            warm = round(fake.walk("truenas-disk", tick, 36, 47))
            disks = [{"name": f"sd{letter}", "model": "WDC WD120EFBX", "size": 12_000_138_625_024, "type": "HDD", "pool": "tank"} for letter in "abcd"]
            disks.append({"name": "nvme0n1", "model": "Samsung SSD 980 1TB", "size": 1_000_204_886_016, "type": "SSD", "pool": "fast"})
            heat = {"sda": [warm, 70.0], "sdb": [warm + 2, 70.0], "sdc": [warm - 1, 70.0], "sdd": [warm + 1, 70.0], "nvme0n1": [warm + 9, 84.8]}
            return _disks(disks, heat, limit)
        if widget_kind == "apps":
            return _apps([
                {"name": "jellyfin", "state": "RUNNING", "upgrade_available": tick % 4 == 0, "human_version": "10.10.7_1.2.3"},
                {"name": "immich", "state": "RUNNING", "upgrade_available": False, "human_version": "v1.132.3_1.0.0"},
                {"name": "nextcloud", "state": "DEPLOYING" if tick % 5 == 0 else "RUNNING", "upgrade_available": False, "human_version": "31.0.4_2.0.1"},
                {"name": "adguard-home", "state": "STOPPED", "upgrade_available": False, "human_version": "v0.107.61_1.1.0"},
            ], limit)
        if widget_kind == "tasks":
            moment = {"$date": (time.time() - 1800) * 1000}
            return _tasks(
                [{"dataset": "tank/photos", "enabled": True, "state": {"state": "FINISHED", "datetime": moment}},
                 {"dataset": "tank/documents", "enabled": True, "state": {"state": "FINISHED", "datetime": moment}}],
                [{"name": "tank/photos - offsite/photos", "enabled": True, "state": {"state": "RUNNING" if tick % 3 == 0 else "FINISHED", "datetime": moment}}],
                limit)
        if widget_kind == "interfaces":
            down, up = fake.walk("truenas-down", tick, 2, 60) * 1_000_000, fake.walk("truenas-up", tick, 1, 20) * 1_000_000
            return _interfaces({"interfaces": {
                "enp1s0": {"link_state": "LINK_STATE_UP", "speed": 10000, "received_bytes_rate": down, "sent_bytes_rate": up},
                "enp2s0": {"link_state": "LINK_STATE_DOWN", "speed": 0, "received_bytes_rate": 0, "sent_bytes_rate": 0},
                "br0": {"link_state": "LINK_STATE_UP", "speed": 10000, "received_bytes_rate": down * 0.1, "sent_bytes_rate": up * 0.2},
            }}, {"ips": {"enp1s0": {"IPV4": ["192.168.1.20/24"]}, "br0": {"IPV4": ["192.168.1.21/24"]}}}, options)
        if widget_kind == "services":
            return _services([{"service": "cifs", "enable": True, "state": "RUNNING"}, {"service": "nfs", "enable": True, "state": "RUNNING"},
                              {"service": "ssh", "enable": True, "state": "RUNNING"}, {"service": "ups", "enable": False, "state": "STOPPED"}], options)
        if widget_kind == "vms":
            return _machines([{"name": "homeassistant", "vcpus": 2, "cores": 1, "threads": 1, "memory": 4096, "status": {"state": "RUNNING"}},
                              {"name": "windows", "vcpus": 4, "cores": 1, "threads": 1, "memory": 8192, "status": {"state": "STOPPED"}}],
                             [{"name": "pihole", "type": "CONTAINER", "status": "RUNNING", "cpu": "1", "memory": 536_870_912}])
        return WidgetData(status="warn", items=[{"title": "Scrub of pool tank finished with 0 errors", "subtitle": "2026-09-04", "status": "warn"}], secondary=[{"label": "Open", "value": 1}])


ADAPTER = TruenasAdapter()


class _NoCurrentApi(Exception):
    """The WebSocket at ``/api/current`` was turned down before it opened."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def _live_numbers(event: Any) -> tuple[float | None, float | None]:
    """CPU usage in percent and memory in use in bytes, out of one
    ``reporting.realtime`` event of either shape TrueNAS has sent."""
    if not isinstance(event, dict):
        return None, None
    cpu_block = event.get("cpu") or {}
    cpu = None
    for key in ("cpu", "average"):
        usage = (cpu_block.get(key) or {}).get("usage") if isinstance(cpu_block.get(key), dict) else None
        if isinstance(usage, (int, float)):
            cpu = round(float(usage), 1)
            break
    memory_block = event.get("memory") or {}
    used = None
    total, available = memory_block.get("physical_memory_total"), memory_block.get("physical_memory_available")
    if isinstance(total, (int, float)) and isinstance(available, (int, float)) and total > 0:
        used = float(total - available)
    else:
        classes = memory_block.get("classes") or {}
        # 24.10: what is not unused is in use, the ARC included, which is what the NAS itself shows.
        parts = [classes.get(key) for key in ("apps", "cache", "buffers", "arc", "page_tables", "slab_cache")]
        if any(isinstance(part, (int, float)) for part in parts):
            used = float(sum(part for part in parts if isinstance(part, (int, float))))
    return cpu, used


def _version(label: str) -> tuple[int, int]:
    """``25.10.1`` and ``TrueNAS-SCALE-24.10.2`` as (25, 10) and (24, 10).

    (0, 0) when there is no number in it. That reads as "older", so a TrueNAS
    that names itself in some way nobody has seen yet keeps its cards.
    """
    found = re.search(r"(\d+)\.(\d+)", label)
    return (int(found.group(1)), int(found.group(2))) if found else (0, 0)


def _rest_refused(label: str, address: str, turned_down: int | None) -> AdapterError:
    """Why the REST API is not used on this TrueNAS, and what to change.

    ⚠️ The way out depends on the address. Over http:// it is https://. Over
    https:// the address is right already and something in between does not
    pass the WebSocket on; telling that reader to change to https:// would
    send them looking in the wrong place.
    """
    which = f"TrueNAS {label}" if label else "This TrueNAS"
    why = (f"{which} has the current API and has deprecated the old REST API: TrueNAS 25.10 counts every call to it "
           "in an alert on the NAS, and TrueNAS 26 removes it. HexDeck does not use it there.")
    if address.startswith("https://"):
        status = f" with HTTP {turned_down}" if turned_down else ""
        return AdapterError(
            f"TrueNAS turned the WebSocket at /api/current down{status}. {why}", code="deprecated_api",
            hint="A reverse proxy in front of TrueNAS has to pass WebSockets on, that is the Upgrade and "
                 "Connection headers. Or point the URL straight at TrueNAS.",
        )
    return AdapterError(
        why, code="deprecated_api",
        hint="Change the URL to https://. HexDeck then uses the current API, and the key of a read-only "
             "administrator is enough. Over plain http TrueNAS would revoke the key, so that is never tried.",
    )


def _day(value: Any) -> str:
    """The day of an alert. TrueNAS sends ``{"$date": milliseconds}``.

    ⚠️ The card used to cut the first ten characters off that number and
    showed ``1788181994`` where a date belonged.
    """
    if isinstance(value, dict):
        value = value.get("$date")
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, UTC).strftime("%Y-%m-%d")
    return str(value or "")[:10]


def _percent(item: dict[str, Any]) -> float | None:
    """How full, from whichever field the card put it in."""
    for key in ("progress", "percent", "used_percent"):
        value = item.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    raw = str(item.get("value") or "").rstrip("%")
    try:
        return float(raw)
    except ValueError:
        return None


def _seconds(moment: Any) -> float | None:
    """``{"$date": milliseconds}``, as TrueNAS sends a time, in seconds."""
    value = moment.get("$date") if isinstance(moment, dict) else None
    return value / 1000 if isinstance(value, int | float) else None


def _current_only(answer: Any) -> None:
    """A card of the current API on a TrueNAS read over REST says so."""
    if answer is None:
        raise AdapterError("This card needs the current TrueNAS API, over https://.",
                           code="needs_current_api", hint="Change the URL to https://. A TrueNAS before 25.04 has no such card.")


def _scan(scan: Any) -> tuple[str, str]:
    """The pool's last scrub or running resilver in words, and whether it needs a look.

    The ``scan`` of ``pool.query``: ``function`` SCRUB or RESILVER, ``state``
    SCANNING, FINISHED or CANCELED, ``end_time`` as ``{"$date": ms}``.
    """
    if not isinstance(scan, dict) or not scan.get("function"):
        return "", "ok"
    function, state = str(scan.get("function")), str(scan.get("state") or "")
    done = scan.get("percentage")
    progress = f" {float(done):.0f}%" if isinstance(done, (int, float)) else ""
    if function == "RESILVER":
        return (f"resilvering{progress}", "warn") if state == "SCANNING" else ("", "ok")
    if state == "SCANNING":
        return ("scrub paused" if scan.get("pause") else f"scrubbing{progress}"), "ok"
    if state == "CANCELED":
        return "scrub cancelled", "ok"
    if state == "FINISHED":
        errors = int(scan.get("errors") or 0)
        since = ago(_seconds(scan.get("end_time")))
        words = f"scrubbed {since} ago" if since else "scrubbed"
        return (f"{words}, {errors} error{'' if errors == 1 else 's'}", "warn") if errors else (words, "ok")
    return "", "ok"


def _disks(disks: Any, temperatures: Any, limit: int) -> WidgetData:
    """Every disk, the hottest first.

    ⚠️ ``disk.temperatures`` reads each disk through the kernel's drivetemp,
    which sends the disk a command, so TrueNAS itself keeps the number for
    five minutes; the card is not refreshed more often than that either. A
    disk that tells no temperature answers None.
    """
    _current_only(disks)
    heat = temperatures if isinstance(temperatures, dict) else {}
    rows = []
    for disk in disks or []:
        if not isinstance(disk, dict):
            continue
        name = str(disk.get("name") or "?")
        reading = heat.get(name)
        current, critical = (reading[0], reading[1]) if isinstance(reading, list) and len(reading) == 2 else (reading, None)
        current = float(current) if isinstance(current, (int, float)) else None
        top = float(critical) if isinstance(critical, (int, float)) and critical > DISK_WARM else DISK_HOT
        state = "unknown" if current is None else "bad" if current >= top else "warn" if current >= DISK_WARM else "ok"
        kind = str(disk.get("type") or "")
        rows.append((current if current is not None else -1.0, {
            "title": " ".join(part for part in (name, str(disk.get("model") or "").strip()) if part),
            "subtitle": " · ".join(part for part in (str(disk.get("pool") or "not in a pool"), human_bytes(disk.get("size")) if disk.get("size") else "", kind) if part),
            "status": state,
            "value": f"{current:.0f} °C" if current is not None else "",
        }))
    items = [item for _heat, item in sorted(rows, key=lambda row: (-row[0], row[1]["title"]))][:limit]
    hottest = max((row[0] for row in rows), default=-1.0)
    worst = "bad" if any(i["status"] == "bad" for i in items) else "warn" if any(i["status"] == "warn" for i in items) else "ok"
    secondary = [{"label": "Disks", "value": len(rows)}]
    if hottest >= 0:
        secondary.append({"label": "Hottest", "value": f"{hottest:.0f} °C"})
    return WidgetData(status=worst, items=items, secondary=secondary, meta={"empty": "TrueNAS lists no disk."})


def _apps(apps: Any, limit: int) -> WidgetData:
    """The apps, crashed and starting ones first, then by name."""
    _current_only(apps)
    order = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}
    items = []
    updates = 0
    for app in apps or []:
        if not isinstance(app, dict):
            continue
        state, words = APP_STATES.get(str(app.get("state") or ""), ("unknown", str(app.get("state") or "?").title()))
        update = bool(app.get("upgrade_available") or app.get("image_updates_available"))
        updates += update
        items.append({
            "title": str(app.get("name") or app.get("id") or "?"),
            "subtitle": " · ".join(part for part in (str(app.get("human_version") or app.get("version") or ""), "update available" if update else "") if part),
            "status": state,
            "value": words,
            # Which update, for ``detect``: a newer catalog version, or newer
            # images for the same one (the only kind a custom app has). The
            # renderer leaves the key alone.
            "update": (str(app.get("latest_version") or "") or "a newer version") if app.get("upgrade_available")
                      else ("image" if app.get("image_updates_available") else ""),
        })
    items.sort(key=lambda item: (order.get(item["status"], 2), item["title"].lower()))
    running = sum(1 for item in items if item["value"] == "Running")
    return WidgetData(
        status="bad" if any(i["status"] == "bad" for i in items) else "warn" if any(i["status"] == "warn" for i in items) else "ok",
        items=items[:limit],
        secondary=[{"label": "Running", "value": f"{running} / {len(items)}"}, {"label": "Updates", "value": updates}],
        meta={"empty": "No app is installed on TrueNAS."},
    )


def _tasks(snapshots: Any, replications: Any, limit: int) -> WidgetData:
    """The enabled periodic snapshot and replication tasks, failed ones first.

    ⚠️ Both carry ``state`` as a dict from zettarepl: ``state`` FINISHED,
    RUNNING, WAITING, PENDING (never run since TrueNAS started), HOLD (with a
    ``reason``) or ERROR (with an ``error``), and ``datetime`` as
    ``{"$date": ms}``. A snapshot task is named by its dataset; a replication
    by its own name.
    """
    _current_only(snapshots)
    _current_only(replications)
    order = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}
    items = []
    for task, kind in [(one, "Snapshot") for one in snapshots or []] + [(one, "Replication") for one in replications or []]:
        if not isinstance(task, dict) or not task.get("enabled", True):
            continue
        details = task.get("state") if isinstance(task.get("state"), dict) else {}
        state, words = TASK_STATES.get(str(details.get("state") or ""), ("unknown", str(details.get("state") or "?").title()))
        why = str(details.get("error") or details.get("reason") or "").strip()
        since = ago(_seconds(details.get("datetime")))
        when = f"{since} ago" if since else ""
        name = str((task.get("dataset") if kind == "Snapshot" else task.get("name")) or "?")
        items.append({
            "id": f"{kind.lower()}:{task.get('id')}",
            "title": name,
            "subtitle": " · ".join(part for part in (kind, when, why[:120]) if part),
            "status": state,
            "value": words,
        })
    items.sort(key=lambda item: (order.get(item["status"], 2), item["title"].lower()))
    failed = sum(1 for item in items if item["status"] == "bad")
    return WidgetData(
        status="bad" if failed else "warn" if any(i["status"] == "warn" for i in items) else "ok",
        items=items[:limit],
        secondary=[{"label": "Tasks", "value": len(items)}, {"label": "Failed", "value": failed}],
        meta={"empty": "No snapshot or replication task is enabled."},
    )


# -- the cards and notifications taken from nexdeck 0.46.0 --------------------

#: What TrueNAS' own Services page calls them; ``service.query`` has the old names.
SERVICE_NAMES = {
    "cifs": "SMB", "nfs": "NFS", "ssh": "SSH", "ftp": "FTP", "iscsitarget": "iSCSI", "nvmet": "NVMe-oF",
    "snmp": "SNMP", "ups": "UPS", "smartd": "S.M.A.R.T.",
}
#: A VM in ``vm.query`` and an instance in ``virt.instance.query``, as a colour and a word.
MACHINE_STATES: dict[str, tuple[Status, str]] = {
    "RUNNING": ("ok", "Running"), "STARTING": ("ok", "Starting"), "STOPPING": ("ok", "Stopping"),
    "STOPPED": ("unknown", "Stopped"), "SUSPENDED": ("warn", "Paused"), "FROZEN": ("warn", "Paused"),
    "FREEZING": ("warn", "Paused"), "THAWED": ("ok", "Running"), "ERROR": ("bad", "Error"),
}
#: Which rows come first: the ones somebody has to look at.
ROW_ORDER = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _overall(items: list[dict[str, Any]]) -> Status:
    states = {item.get("status") for item in items}
    return "bad" if "bad" in states else ("warn" if "warn" in states else "ok")


def _sorted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (ROW_ORDER.get(str(item.get("status")), 3), str(item.get("title") or "").lower()))


def _interfaces(realtime: Any, summary: Any, options: dict[str, Any]) -> WidgetData:
    """Every interface from the realtime event, with its address from ``network.general.summary``.

    ⚠️ The traffic is the event's, not ``reporting.netdata_get_data``: the
    event the system card subscribes to anyway carries every interface's rate
    in bytes per second (``get_interface_stats``, 25.04 and 25.10), so the
    card costs TrueNAS nothing new. The link speed is in Mbit/s there, and the
    names are those of ``interface.query``, so no container's veth turns up.

    A link that is down is grey, not red. A NAS with four ports and one cable
    is not broken.
    """
    interfaces = realtime.get("interfaces") if isinstance(realtime, dict) else None
    if not isinstance(interfaces, dict):
        raise AdapterError("TrueNAS sent no live figures for its interfaces.", code="no_realtime",
                           hint="The key's user needs the Read-Only Administrator role, which may read the reporting.")
    if not interfaces:
        # ⚠️ Not measured. The one event on record, from a test VM on
        # 25.10.7, had an empty ``interfaces``; whether that VM had none
        # configured or the event leaves them out is open. An empty card would
        # hide which, so it says so instead.
        raise AdapterError("TrueNAS listed no interfaces in its live figures.", code="no_interfaces",
                           hint="TrueNAS takes them from Network > Interfaces; one set up there should appear here.")
    addresses = summary.get("ips") if isinstance(summary, dict) and isinstance(summary.get("ips"), dict) else {}
    items: list[dict[str, Any]] = []
    down = up = 0.0
    for name in sorted(interfaces):
        stats = interfaces[name] if isinstance(interfaces[name], dict) else {}
        linked = stats.get("link_state") == "LINK_STATE_UP"
        received, sent = _number(stats.get("received_bytes_rate")), _number(stats.get("sent_bytes_rate"))
        down, up = down + received, up + sent
        ips = addresses.get(name) if isinstance(addresses.get(name), dict) else {}
        first = next(iter([*(ips.get("IPV4") or []), *(ips.get("IPV6") or [])]), "")
        speed = _number(stats.get("speed"))
        items.append({
            "title": name,
            "subtitle": join_parts(options, ("address", first), ("speed", f"{speed:.0f} Mbit/s" if linked and speed else ""),
                                   ("link", "" if linked else "No link")),
            "value": f"↓ {human_rate(received)} ↑ {human_rate(sent)}" if linked else "",
            "status": "ok" if linked else "unknown",
        })
    return WidgetData(items=items, metrics={"down": down, "up": up},
                      secondary=[{"label": "Download", "metric": "down", "value": human_rate(down)},
                                 {"label": "Upload", "metric": "up", "value": human_rate(up)}])


def _services(services: Any, options: dict[str, Any]) -> WidgetData:
    """The services meant to start with TrueNAS, and whether they run.

    ``enable`` is "start on boot". One that should start and is stopped is
    red; one that runs without being set to start is shown as well, since it
    runs. The rest only with the option, or the card would list eight
    services most people never switched on.
    """
    every = bool(options.get("all_services"))
    items: list[dict[str, Any]] = []
    for service in services or []:
        if not isinstance(service, dict):
            continue
        state = str(service.get("state") or "").upper()
        running = state == "RUNNING"
        if not (every or service.get("enable") or running):
            continue
        name = str(service.get("service") or "?")
        items.append({
            "title": SERVICE_NAMES.get(name, name.upper()),
            "value": "Running" if running else ("Stopped" if state == "STOPPED" else "Unknown"),
            "status": "ok" if running else ("bad" if service.get("enable") and state == "STOPPED" else "unknown"),
        })
    return WidgetData(status=_overall(items), items=_sorted(items))


def _machines(vms: Any, instances: Any) -> WidgetData:
    """Virtual machines from ``vm.query`` and containers from ``virt.instance.query``.

    25.10 makes VMs with ``vm.*`` again and keeps Incus for containers; 25.04
    could make VMs with Incus too. So both are read, and an Incus VM with the
    name of a ``vm.query`` one is the same machine, counted once. ``vm.query``
    gives memory in MiB, Incus in bytes.
    """
    if vms is None and instances is None:
        raise AdapterError("TrueNAS answered neither vm.query nor virt.instance.query.", code="rpc_error",
                           hint="Virtual machines and containers need the current TrueNAS API, over https://, and TrueNAS 25.04 or later.")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for vm in vms or []:
        if not isinstance(vm, dict):
            continue
        name = str(vm.get("name") or "?")
        seen.add(name)
        status = vm.get("status") if isinstance(vm.get("status"), dict) else {}
        cpus = int(_number(vm.get("vcpus")) or 1) * int(_number(vm.get("cores")) or 1) * int(_number(vm.get("threads")) or 1)
        memory = _number(vm.get("memory")) * 1024 * 1024
        items.append(_machine_row(name, "VM", f"{cpus} vCPU", human_bytes(memory) if memory else "", str(status.get("state") or "").upper()))
    for instance in instances or []:
        if not isinstance(instance, dict):
            continue
        name = str(instance.get("name") or "?")
        kind = "VM" if str(instance.get("type") or "").upper() == "VM" else "Container"
        if kind == "VM" and name in seen:
            continue
        # Incus takes a count or a set of cores ("0-3"); only a count is a number of vCPUs.
        cpu = str(instance.get("cpu") or "")
        memory = _number(instance.get("memory"))
        items.append(_machine_row(name, kind, (f"{cpu} vCPU" if cpu.isdigit() else f"CPU {cpu}") if cpu else "", human_bytes(memory) if memory else "", str(instance.get("status") or "").upper()))
    running = sum(1 for item in items if item["value"] == "Running")
    return WidgetData(status=_overall(items), items=_sorted(items), secondary=[{"label": "Running", "value": running}])


def _machine_row(name: str, kind: str, cpu: str, memory: str, state: str) -> dict[str, Any]:
    status, word = MACHINE_STATES.get(state, ("unknown", state.title() or "Unknown"))
    return {"title": name, "subtitle": " · ".join(part for part in (kind, cpu, memory) if part), "value": word, "status": status}


def _newly(before: WidgetData | None, after: WidgetData, test: Any, *, first: bool = False, same: Any = None) -> list[dict[str, Any]]:
    """The rows of ``after`` for which ``test`` holds and did not before.

    Matched by ``id``, else by title. ``same`` says whether a row that held
    before is still the same news: an app whose waiting update moved on to a
    newer version is news again. ``first`` lets a card speak that has nothing
    before it; see ``detect``.
    """
    if after.error:
        return []
    # ⚠️ A card in error keeps its last good rows (``collector._failure``).
    # Compared against those, a task that failed while TrueNAS rebooted is
    # still news afterwards; treated as "nothing before", it was lost.
    if before is None or (before.error and not before.items):
        return [item for item in after.items if test(item)] if first else []

    def key(item: dict[str, Any]) -> str:
        return str(item.get("id") or item.get("title") or "")

    earlier = {key(item): item for item in before.items}
    found = []
    for item in after.items:
        if not test(item):
            continue
        old = earlier.get(key(item))
        if old is not None and test(old) and (same is None or same(old, item)):
            continue
        found.append(item)
    return found


def _chip(data: WidgetData | None, label: str) -> str | None:
    """The value of the secondary row with this label, or None."""
    for row in (data.secondary if data else []):
        if row.get("label") == label:
            return str(row.get("value"))
    return None
