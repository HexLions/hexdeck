"""ProxMenux Monitor: the verdict its own health checks reach about a Proxmox node.

ProxMenux is a menu-driven toolkit for Proxmox VE whose monitor is a small Flask
server on the node itself, on port 8008. HexDeck already speaks the Proxmox API;
what this adds is everything that is not in it: ten categories of health check
with a reason in words, the disks with their SMART verdict and temperature, the
node's own sensors, and the guests as the monitor already collects them.

Five read-only calls, all of them GET: ``/api/health/status`` for the verdict,
``/api/health/details`` for the categories behind it, ``/api/system`` for the
node, ``/api/vms`` for the guests and ``/api/storage`` for the disks.

⚠️ Authentication is optional in ProxMenux and a token is the right way in when
it is on. Its own interface, under Settings, mints a long-lived one meant for
exactly this ("API tokens for external integrations"), read-only by default and
good for a year. Rotating the monitor's JWT secret invalidates every one of them
at once, which its log says in so many words.

⚠️ The monitor serves https with a certificate it makes itself, so the TLS check
has to be switched off for this connection unless its certificate was replaced.

⚠️ The verdicts are the monitor's own words, OK, WARNING and CRITICAL, and are
passed through rather than recomputed. Where its thresholds should sit is a
question for its own settings page, not for a card.
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
    base_url,
    duration_short,
    human_bytes,
    measured,
    status_from_percent,
)

#: What the monitor says, and what a card makes of it.
VERDICTS = {"OK": "ok", "WARNING": "warn", "CRITICAL": "bad", "INFO": "ok", "UNKNOWN": "unknown"}
#: The same for a disk, which answers in lower case and from SMART.
DISK_HEALTH = {"passed": "ok", "ok": "ok", "healthy": "ok", "warning": "warn", "critical": "bad", "failed": "bad"}
HEALTH_SECONDS = 20
SYSTEM_SECONDS = 15
GUESTS_SECONDS = 20
DISKS_SECONDS = 120


def _percent(value: Any) -> float | None:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


class ProxMenuxAdapter(Adapter):
    kind = "proxmenux"
    label = "ProxMenux"
    category = "hosts"
    description = "The health checks, disks, sensors and guests of a Proxmox node, as ProxMenux Monitor sees them."
    icon = "proxmenux"
    #: Confirmed against a live instance on 2026-10-06.
    beta = False
    docs_url = "https://macrimi.github.io/ProxMenux/docs/monitor/api"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://proxmox.lan:8008",
              help="ProxMenux Monitor on the node, port 8008 by default."),
        Field("token", "API token", type="password", secret=True,
              help="From the monitor's own Settings: an API token for external integrations, read-only. "
                   "Leave empty when the monitor has no authentication."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False,
              help="The monitor serves https with a certificate it made itself, so this is usually needed."),
    )
    widgets = (
        WidgetType(kind="health", label="Health", description="The monitor's own verdict on the node, with how many checks are unhappy.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("warnings", "criticals")),
        WidgetType(kind="checks", label="Health checks", description="Every category the monitor checks, the unhappy ones first, with its reason.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(
                       Field("troubled_only", "Only what is not well", type="bool", default=True,
                             help="Off lists all ten categories, which is a long and mostly green list."),
                       Field("limit", "Entries", type="number", default=10),
                   )),
        WidgetType(kind="node", label="Node", description="Processor, memory, temperature, load and uptime of the node itself.",
                   renderer="stats", default_size=(4, 2), refresh_seconds=30, metrics=("cpu", "memory", "temperature")),
        WidgetType(kind="guests", label="Guests", description="The virtual machines and containers on this node, stopped ones last.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60, metrics=("running",),
                   options=(
                       Field("kind", "Which", type="select", default="all",
                             options=(("all", "Machines and containers"), ("qemu", "Virtual machines"), ("lxc", "Containers"))),
                       Field("running_only", "Only what is running", type="bool", default=False),
                       Field("limit", "Entries", type="number", default=12),
                   )),
        WidgetType(kind="disks", label="Disks", description="Every physical disk with its SMART verdict and its temperature.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300, metrics=("warm",),
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    # -- talking to the monitor -----------------------------------------------

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        token = str(config.get("token") or "").strip()
        response = await ctx.request(
            "GET", f"{base_url(config)}{path}",
            headers={"Authorization": f"Bearer {token}"} if token else None,
            verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False,
        )
        if response.status_code in (401, 403):
            raise AuthFailed(
                "ProxMenux Monitor rejected the token." if token else "ProxMenux Monitor wants a token.",
                hint="Its Settings page mints an API token for external integrations. One that was made before the "
                     "monitor's JWT secret changed no longer verifies, and a new one has to be made.",
            )
        if response.status_code == 404:
            raise AdapterError(f"This monitor has no {path}.", code="no_such_path",
                               hint="The health checks and the disks came with the monitor's 1.2 releases; "
                                    "an older one answers only some of these.")
        if response.status_code >= 400:
            raise AdapterError(f"ProxMenux Monitor answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("ProxMenux Monitor did not answer with JSON.", code="not_json",
                               hint="Port 8008 is the monitor; 8006 is the Proxmox interface, which this card cannot read.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        system = await self._get(config, ctx, "/api/system", 0)
        if not isinstance(system, dict) or "cpu_usage" not in system:
            raise AdapterError("That address answers, but not the way ProxMenux Monitor does.", code="not_proxmenux",
                               hint="Use the monitor's own address and port, not the Proxmox interface's.")
        verdict = await self._get(config, ctx, "/api/health/status", 0)
        node = str(system.get("proxmox_node") or system.get("hostname") or "a node")
        said = str((verdict or {}).get("status") or "?") if isinstance(verdict, dict) else "?"
        return f"ProxMenux Monitor on {node}, Proxmox {system.get('proxmox_version', '?')}, health {said}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "health":
            return self._health(await self._get(config, ctx, "/api/health/status", HEALTH_SECONDS))
        if widget_kind == "checks":
            return self._checks(await self._get(config, ctx, "/api/health/details", HEALTH_SECONDS), options)
        if widget_kind == "guests":
            return self._guests(await self._get(config, ctx, "/api/vms", GUESTS_SECONDS), options)
        if widget_kind == "disks":
            return self._disks(await self._get(config, ctx, "/api/storage", DISKS_SECONDS), options)
        return self._node(await self._get(config, ctx, "/api/system", SYSTEM_SECONDS))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _health(verdict: Any) -> WidgetData:
        said = verdict if isinstance(verdict, dict) else {}
        word = str(said.get("status") or "UNKNOWN").upper()
        criticals = int(said.get("critical_count") or 0)
        warnings = int(said.get("warning_count") or 0)
        secondary: list[dict[str, Any]] = []
        if criticals:
            secondary.append({"label": "Critical", "value": criticals, "metric": "criticals"})
        if warnings:
            secondary.append({"label": "Warnings", "value": warnings, "metric": "warnings"})
        secondary.append({"label": "Well", "value": int(said.get("ok_count") or 0)})
        return WidgetData(
            status=VERDICTS.get(word, "unknown"),
            primary={"label": "Health", "value": word.capitalize()},
            secondary=secondary,
            metrics=measured({"warnings": float(warnings), "criticals": float(criticals)}),
            # The monitor writes one sentence about why it said that; it is the
            # most useful thing on the card and nothing else carries it.
            meta={"notice": " ".join(str(said.get("summary") or "").split())[:160]},
        )

    @staticmethod
    def _checks(details: Any, options: dict[str, Any]) -> WidgetData:
        found = details.get("details") if isinstance(details, dict) else None
        categories = found if isinstance(found, dict) else {}
        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        rows = []
        for name, data in sorted(categories.items()):
            data = data if isinstance(data, dict) else {}
            colour = VERDICTS.get(str(data.get("status") or "UNKNOWN").upper(), "unknown")
            if options.get("troubled_only", True) and colour == "ok":
                continue
            reason = " ".join(str(data.get("reason") or data.get("details") or "").split())
            rows.append({"colour": colour, "row": {
                # ⚠️ The keys are the monitor's own, in snake case: "storage",
                # "network", "log_errors". A card reads them as words.
                "title": str(name).replace("_", " ").capitalize(),
                "subtitle": reason[:90],
                "value": str(data.get("status") or "").capitalize(),
                "status": colour,
            }})
        ranked = sorted(rows, key=lambda one: (order.get(one["colour"], 4), one["row"]["title"]))
        unhappy = sum(1 for one in rows if one["colour"] in ("bad", "warn"))
        return WidgetData(
            status="bad" if any(one["colour"] == "bad" for one in rows) else "warn" if unhappy else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 10))],
            primary={"label": "Not well", "value": unhappy},
            meta={"empty": "Every check is happy.",
                  "notice": " ".join(str((details or {}).get("summary") or "").split())[:160] if isinstance(details, dict) else ""},
        )

    @staticmethod
    def _node(system: Any) -> WidgetData:
        said = system if isinstance(system, dict) else {}
        cpu = _percent(said.get("cpu_usage"))
        memory = _percent(said.get("memory_usage"))
        temperature = _percent(said.get("temperature"))
        secondary: list[dict[str, Any]] = []
        if memory is not None:
            total, used = said.get("memory_total"), said.get("memory_used")
            # The monitor counts memory in gibibytes already.
            hint = f"{used} of {total} GB" if total else ""
            secondary.append({"label": "Memory", "value": memory, "unit": "%", "metric": "memory", "hint": hint})
        if temperature:
            secondary.append({"label": "Temp", "value": temperature, "unit": "°C", "metric": "temperature"})
        load = said.get("load_average") if isinstance(said.get("load_average"), list) else []
        if len(load) >= 3:
            secondary.append({"label": "Load", "value": " ".join(f"{float(one):.2f}" for one in load[:3]),
                              "hint": f"{said.get('cpu_threads') or said.get('cpu_cores') or '?'} threads"})
        if said.get("uptime"):
            # ⚠️ Uptime arrives as the monitor's own words ("5 days, 3 hours")
            # on some versions and as seconds on others.
            raw = said["uptime"]
            secondary.append({"label": "Up", "value": duration_short(float(raw)) if isinstance(raw, (int, float)) else str(raw)})
        updates = said.get("available_updates")
        if isinstance(updates, (int, float)) and updates:
            secondary.append({"label": "Updates", "value": int(updates)})
        return WidgetData(
            status=status_from_percent(max([one for one in (cpu, memory) if one is not None], default=None)),
            primary={"label": "CPU", "value": cpu if cpu is not None else "-", "unit": "%" if cpu is not None else ""},
            secondary=secondary,
            metrics=measured({"cpu": cpu, "memory": memory, "temperature": temperature or None}),
            meta={"node": str(said.get("proxmox_node") or said.get("hostname") or ""),
                  "version": str(said.get("proxmox_version") or "")},
        )

    @staticmethod
    def _guests(guests: Any, options: dict[str, Any]) -> WidgetData:
        found = [one for one in (guests if isinstance(guests, list) else []) if isinstance(one, dict)]
        wanted = str(options.get("kind") or "all")
        rows = []
        running = 0
        # ⚠️ Counted after the kind filter and before the running filter: a card
        # showing containers says how many containers there are, and one showing
        # only what runs still says how many it is of.
        considered = 0
        for guest in found:
            kind = "lxc" if str(guest.get("type")) == "lxc" else "qemu"
            if wanted in ("qemu", "lxc") and kind != wanted:
                continue
            considered += 1
            alive = str(guest.get("status")) == "running"
            running += 1 if alive else 0
            if options.get("running_only") and not alive:
                continue
            share = _percent(float(guest.get("cpu") or 0) * 100)
            memory = _percent(100.0 * float(guest.get("mem") or 0) / float(guest.get("maxmem") or 0)) \
                if guest.get("maxmem") else None
            parts = [f"{guest.get('vmid')}", "container" if kind == "lxc" else "machine"]
            if alive and share is not None:
                parts.append(f"cpu {share:.0f}%")
            if alive and memory is not None:
                parts.append(f"memory {memory:.0f}%")
            rows.append({"alive": alive, "row": {
                "id": guest.get("vmid"),
                "title": str(guest.get("name") or guest.get("vmid") or "?"),
                "subtitle": " · ".join(parts),
                "value": str(guest.get("status") or ""),
                # A stopped guest is a choice somebody made, not a fault.
                "status": "ok" if alive else "unknown",
                "progress": memory if alive else None,
            }})
        ranked = sorted(rows, key=lambda one: (not one["alive"], str(one["row"]["title"]).lower()))
        return WidgetData(
            status="ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 12))],
            primary={"label": "Running", "value": f"{running} / {considered}"},
            metrics=measured({"running": float(running)}),
            meta={"empty": "This node carries no guests."},
        )

    @staticmethod
    def _disks(storage: Any, options: dict[str, Any]) -> WidgetData:
        said = storage if isinstance(storage, dict) else {}
        disks = [one for one in said.get("disks") or [] if isinstance(one, dict)]
        order = {"bad": 0, "warn": 1, "ok": 2, "unknown": 3}
        rows = []
        warm = 0.0
        for disk in disks:
            colour = DISK_HEALTH.get(str(disk.get("health") or "").lower(), "unknown")
            temperature = _percent(disk.get("temperature"))
            warm = max(warm, temperature or 0.0)
            # ⚠️ The size comes in kilobytes, for the monitor's own formatter.
            size = float(disk.get("size") or 0) * 1024
            parts = [str(disk.get("model") or ""), human_bytes(size) if size else ""]
            rows.append({"colour": colour, "row": {
                "title": str(disk.get("name") or "?"),
                "subtitle": " · ".join(part for part in parts if part)[:90],
                "value": f"{temperature:.0f} °C" if temperature else str(disk.get("smart_status") or "").capitalize(),
                "status": colour,
            }})
        ranked = sorted(rows, key=lambda one: (order.get(one["colour"], 4), one["row"]["title"]))
        return WidgetData(
            status="bad" if any(one["colour"] == "bad" for one in rows) else
                   "warn" if any(one["colour"] == "warn" for one in rows) else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 10))],
            primary={"label": "Disks", "value": len(rows)},
            secondary=[{"label": "Warmest", "value": warm, "unit": "°C"}] if warm else [],
            metrics=measured({"warm": warm or None}),
            meta={"empty": "The monitor reports no physical disks."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "checks":
            return self._checks({"overall": "WARNING", "summary": "Storage nearly full on local-zfs", "details": {
                "cpu": {"status": "OK"},
                "memory": {"status": "OK"},
                "storage": {"status": "WARNING", "reason": "local-zfs at 88 per cent"},
                "disks": {"status": "OK"},
                "network": {"status": "OK"},
                "log_errors": {"status": "WARNING", "reason": "7 errors in the last hour"},
                "updates": {"status": "OK"},
                "vms": {"status": "OK"},
                "security": {"status": "OK"},
                "certificates": {"status": "OK"},
            }}, options)
        if widget_kind == "guests":
            return self._guests([
                {"vmid": 100, "name": "truenas", "status": "running", "type": "qemu", "cpu": 0.12, "mem": 17_179_869_184, "maxmem": 34_359_738_368},
                {"vmid": 101, "name": "docker", "status": "running", "type": "qemu", "cpu": 0.31, "mem": 6_442_450_944, "maxmem": 8_589_934_592},
                {"vmid": 110, "name": "pihole", "status": "running", "type": "lxc", "cpu": 0.02, "mem": 268_435_456, "maxmem": 536_870_912},
                {"vmid": 120, "name": "old-test", "status": "stopped", "type": "lxc", "cpu": 0, "mem": 0, "maxmem": 536_870_912},
            ], options)
        if widget_kind == "disks":
            return self._disks({"disks": [
                {"name": "nvme0n1", "model": "Samsung 990 PRO", "size": 2_000_398_934, "health": "passed",
                 "temperature": round(fake.walk("pmx-nvme", tick, 38, 52)), "smart_status": "passed"},
                {"name": "sda", "model": "WDC WD40EFPX", "size": 4_000_787_030, "health": "passed", "temperature": 34, "smart_status": "passed"},
                {"name": "sdb", "model": "WDC WD40EFPX", "size": 4_000_787_030, "health": "warning", "temperature": 46, "smart_status": "passed"},
            ]}, options)
        if widget_kind == "node":
            return self._node({
                "cpu_usage": round(fake.walk("pmx-cpu", tick, 4, 38), 1), "memory_usage": 46.2,
                "memory_total": 64.0, "memory_used": 29.6, "temperature": round(fake.walk("pmx-temp", tick, 41, 58)),
                "load_average": [0.42, 0.51, 0.60], "cpu_threads": 16, "uptime": 1_140_000,
                "available_updates": 3, "proxmox_node": "pve", "proxmox_version": "9.0.3",
            })
        return self._health({"status": "WARNING" if fake.flicker("pmx-health", tick, 0.6) else "OK",
                             "summary": "Storage nearly full on local-zfs", "critical_count": 0,
                             "warning_count": 2, "ok_count": 8})


ADAPTER = ProxMenuxAdapter()
