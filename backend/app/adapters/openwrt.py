"""OpenWrt: the router's own numbers, over the bus LuCI itself talks to.

Everything here goes through ``/ubus``, the JSON-RPC 2.0 endpoint the
``uhttpd-mod-ubus`` plugin serves and LuCI's own interface uses. Four calls, all
of them read-only: ``system board`` for what the box is, ``system info`` for
load, memory and uptime, ``network.interface.<name> status`` for the WAN, and
``iwinfo`` for the stations on each radio.

What to expect when it does not work, because all three are ordinary:

⚠️ A session is a token with a timeout, five minutes by default. It is asked
for once and kept a little less than that, and a call that comes back denied
logs in again before giving up.

⚠️ ``{"result": [6]}`` is a valid answer and means permission denied. rpcd
decides that from ``/etc/config/rpcd`` and ``/usr/share/rpcd/acl.d``; a stock
LuCI installation gives root read of everything, and a user made by hand needs
a role that does.

⚠️ Without ``uhttpd-mod-ubus`` the address answers 404. That package comes with
LuCI, so a router with a web interface has it.

⚠️ The load figures are fixed-point, scaled by 65536. Passed through as they
come, a quiet router reads as a load of 6000.
"""

from __future__ import annotations

import time
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    duration_short,
    human_bytes,
    measured,
    status_from_percent,
)

#: The session that may do nothing but log in: thirty-two zeros.
NO_SESSION = "0" * 32
#: ubus status codes worth a word of their own.
PERMISSION_DENIED = 6
NOT_FOUND = 4
#: A session times out after five minutes unless the login asks for longer.
SESSION_SECONDS = 900
SESSION_KEEP = 600
BOARD_SECONDS = 3600
INFO_SECONDS = 15
#: Kernel load averages are fixed-point with sixteen fractional bits.
LOAD_SCALE = 65536.0


class OpenWrtAdapter(Adapter):
    kind = "openwrt"
    label = "OpenWrt"
    category = "network"
    description = "Load, memory and uptime of the router, its WAN address, and who is on the wireless."
    icon = "openwrt"
    beta = True
    docs_url = "https://openwrt.org/docs/techref/ubus"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://192.168.1.1",
              help="The router's web interface. Its bus is at /ubus, which is added for you."),
        Field("username", "User name", default="root", placeholder="root",
              help="A stock LuCI installation lets root read everything. A user made by hand needs a read role in /etc/config/rpcd."),
        Field("password", "Password", type="password", secret=True, required=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False,
              help="A router serving https usually does it with a certificate it made itself."),
    )
    widgets = (
        WidgetType(kind="system", label="Router", description="Load, memory, uptime and what the box calls itself.",
                   renderer="stats", default_size=(4, 2), refresh_seconds=30, metrics=("load", "memory")),
        WidgetType(kind="wan", label="WAN", description="Whether the uplink is up, the address it has and how long it has held it.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60,
                   options=(Field("interface", "Interface", default="wan", placeholder="wan",
                                  help="The name in /etc/config/network. wan6 for the IPv6 side of the same line."),)),
        WidgetType(kind="wireless", label="Wireless", description="Every radio with the stations associated to it.",
                   renderer="list", default_size=(3, 2), refresh_seconds=60, metrics=("clients",)),
    )

    # -- the bus --------------------------------------------------------------

    @staticmethod
    def _bus(config: dict[str, Any]) -> str:
        address = base_url(config)
        return address if address.endswith("/ubus") else f"{address}/ubus"

    async def _rpc(self, config: dict[str, Any], ctx: Context, session: str, obj: str, method: str,
                   arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """One ubus call. Returns the payload, or raises with the reason."""
        response = await ctx.request(
            "POST", self._bus(config), json_body={
                "jsonrpc": "2.0", "id": 1, "method": "call",
                "params": [session, obj, method, arguments or {}],
            },
            verify=not config.get("insecure"), auth_errors=False,
        )
        if response.status_code == 404:
            raise AdapterError("The router has no bus at this address.", code="no_ubus",
                               hint="Install uhttpd-mod-ubus, which comes with LuCI, or point the connection at the router's web interface.")
        if response.status_code >= 400:
            raise AdapterError(f"The router answered with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("The router did not answer with JSON.", code="not_json",
                               hint="This looks like the web interface itself rather than /ubus.") from failure
        if not isinstance(answer, dict) or "result" not in answer:
            reason = (answer or {}).get("error", {}).get("message") if isinstance(answer, dict) else ""
            raise AdapterError(f"The bus refused the call: {reason or 'no result'}.", code="bad_answer")
        result = answer["result"]
        if not isinstance(result, list) or not result:
            raise AdapterError("The bus answered with nothing at all.", code="bad_answer")
        code = int(result[0] or 0)
        if code == PERMISSION_DENIED:
            raise AdapterError(f"This user may not call {obj} {method}.", code="denied",
                               hint="rpcd decides that. Give the user a read role in /etc/config/rpcd, or use root.")
        if code == NOT_FOUND:
            raise AdapterError(f"The router has no {obj} on its bus.", code="no_such_object",
                               hint="iwinfo needs rpcd-mod-iwinfo, and an interface has to exist under the name it is asked for.")
        if code:
            raise AdapterError(f"{obj} {method} answered with ubus status {code}.", code="ubus_error")
        payload = result[1] if len(result) > 1 else {}
        return payload if isinstance(payload, dict) else {}

    async def _session(self, config: dict[str, Any], ctx: Context) -> str:
        kept = ctx.cache.get("openwrt:session")
        if kept and kept[0] > time.monotonic():
            return str(kept[1])
        answer = await self._rpc(config, ctx, NO_SESSION, "session", "login", {
            "username": str(config.get("username") or "root"),
            "password": str(config.get("password") or ""),
            "timeout": SESSION_SECONDS,
        })
        token = str(answer.get("ubus_rpc_session") or "")
        if not token:
            raise AdapterError("The router refused these credentials.", code="auth_failed",
                               hint="The user and password are the router's own, the ones LuCI asks for.")
        # A little less than the timeout the router granted, so a call does not
        # land on a token that expired between asking and using it.
        lasts = float(answer.get("timeout") or SESSION_SECONDS)
        ctx.cache["openwrt:session"] = (time.monotonic() + max(30.0, min(SESSION_KEEP, lasts * 0.8)), token)
        return token

    async def _call(self, config: dict[str, Any], ctx: Context, obj: str, method: str,
                    arguments: dict[str, Any] | None = None, again: bool = True) -> dict[str, Any]:
        try:
            return await self._rpc(config, ctx, await self._session(config, ctx), obj, method, arguments)
        except AdapterError as failure:
            # A token that has just expired is denied, not reported as expired.
            if failure.code == "denied" and again:
                ctx.cache.pop("openwrt:session", None)
                return await self._call(config, ctx, obj, method, arguments, again=False)
            raise

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        board = await self._call(config, ctx, "system", "board")
        release = board.get("release") if isinstance(board.get("release"), dict) else {}
        what = str(release.get("description") or f"{release.get('distribution', 'OpenWrt')} {release.get('version', '')}").strip()
        return f"{board.get('hostname') or 'The router'} answers: {board.get('model') or 'an unknown board'}, {what or 'unknown release'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "wan":
            name = str(options.get("interface") or "wan").strip() or "wan"
            return self._wan(await self._call(config, ctx, f"network.interface.{name}", "status"), name)
        if widget_kind == "wireless":
            return self._wireless(await self._stations(config, ctx))
        return self._system(
            await self._call(config, ctx, "system", "info"),
            await self._call(config, ctx, "system", "board"),
        )

    async def _stations(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        """Every radio, with the stations on it.

        One call for the list of radios and one per radio; a router has two or
        three of them, so this is cheaper than it reads.
        """
        devices = await self._call(config, ctx, "iwinfo", "devices")
        radios = [str(one) for one in devices.get("devices") or [] if one]
        found = []
        for radio in radios:
            associated = await self._call(config, ctx, "iwinfo", "assoclist", {"device": radio})
            stations = [one for one in associated.get("results") or [] if isinstance(one, dict)]
            found.append({"device": radio, "stations": stations})
        return found

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _system(info: dict[str, Any], board: dict[str, Any]) -> WidgetData:
        load = [float(one) / LOAD_SCALE for one in info.get("load") or [] if isinstance(one, (int, float))]
        memory = info.get("memory") if isinstance(info.get("memory"), dict) else {}
        total = float(memory.get("total") or 0)
        # ⚠️ "free" on a router counts the page cache as used; "available" is
        # what a program could still get, and it is the honest one.
        available = float(memory.get("available") or memory.get("free") or 0)
        used_share = round(100.0 * (total - available) / total, 1) if total else None
        swap = info.get("swap") if isinstance(info.get("swap"), dict) else {}
        release = board.get("release") if isinstance(board.get("release"), dict) else {}
        secondary: list[dict[str, Any]] = []
        if used_share is not None:
            secondary.append({"label": "Memory", "value": used_share, "unit": "%", "metric": "memory",
                              "hint": f"{human_bytes(total - available)} of {human_bytes(total)}"})
        if len(load) >= 3:
            secondary.append({"label": "Load", "value": f"{load[0]:.2f} {load[1]:.2f} {load[2]:.2f}"})
        if swap.get("total"):
            share = 100.0 * (float(swap["total"]) - float(swap.get("free") or 0)) / float(swap["total"])
            secondary.append({"label": "Swap", "value": round(share, 1), "unit": "%"})
        if info.get("uptime"):
            secondary.append({"label": "Up", "value": duration_short(float(info["uptime"]))})
        if release.get("version"):
            secondary.append({"label": "Release", "value": str(release["version"])})
        return WidgetData(
            status=status_from_percent(used_share),
            primary={"label": "Load", "value": round(load[0], 2) if load else "-"},
            secondary=secondary,
            metrics=measured({"load": round(load[0], 2) if load else None, "memory": used_share}),
            meta={"model": str(board.get("model") or ""), "hostname": str(board.get("hostname") or "")},
        )

    @staticmethod
    def _wan(status: dict[str, Any], name: str) -> WidgetData:
        up = bool(status.get("up"))
        addresses = [str(one.get("address") or "") for one in status.get("ipv4-address") or status.get("address") or []
                     if isinstance(one, dict)]
        addresses += [str(one.get("address") or "") for one in status.get("ipv6-address") or [] if isinstance(one, dict)]
        secondary: list[dict[str, Any]] = []
        if addresses:
            secondary.append({"label": "Address", "value": addresses[0]})
        if status.get("l3_device") or status.get("device"):
            secondary.append({"label": "Device", "value": str(status.get("l3_device") or status.get("device"))})
        if status.get("uptime"):
            secondary.append({"label": "Up", "value": duration_short(float(status["uptime"]))})
        if status.get("proto"):
            secondary.append({"label": "Protocol", "value": str(status["proto"])})
        # An interface that is administratively down is not a fault; one that is
        # meant to come up and has not is.
        pending = bool(status.get("pending"))
        return WidgetData(
            status="ok" if up else "warn" if pending or not status.get("autostart", True) else "bad",
            primary={"label": name, "value": "up" if up else "pending" if pending else "down"},
            secondary=secondary,
            meta={"addresses": addresses},
        )

    @staticmethod
    def _wireless(radios: list[dict[str, Any]]) -> WidgetData:
        items = []
        for radio in radios:
            stations = radio["stations"]
            signals = [float(one.get("signal")) for one in stations if isinstance(one.get("signal"), (int, float))]
            items.append({
                "title": radio["device"],
                "subtitle": f"weakest {int(min(signals))} dBm" if signals else "",
                "value": str(len(stations)),
                "status": "ok" if stations else "unknown",
            })
        total = sum(len(radio["stations"]) for radio in radios)
        return WidgetData(
            status="ok",
            items=items,
            primary={"label": "Stations", "value": total},
            metrics=measured({"clients": float(total)}),
            meta={"empty": "This router reports no radios. A wired one has none."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "wan":
            return self._wan({"up": True, "pending": False, "uptime": 812_344, "l3_device": "eth1", "proto": "dhcp",
                              "ipv4-address": [{"address": "84.112.7.19", "mask": 22}]}, "wan")
        if widget_kind == "wireless":
            return self._wireless([
                {"device": "phy0-ap0", "stations": [{"signal": -47}, {"signal": -61}, {"signal": -72}]},
                {"device": "phy1-ap0", "stations": [{"signal": -55}]},
            ])
        load = fake.walk("owrt-load", tick, 0.08, 0.9)
        return self._system({
            "load": [int(load * LOAD_SCALE), int(load * 0.8 * LOAD_SCALE), int(load * 0.6 * LOAD_SCALE)],
            "memory": {"total": 536_870_912, "free": 210_000_000, "available": 268_000_000},
            "swap": {"total": 0, "free": 0},
            "uptime": 812_344,
        }, {"model": "GL.iNet GL-MT6000", "hostname": "router", "release": {"distribution": "OpenWrt", "version": "24.10.1"}})


ADAPTER = OpenWrtAdapter()
