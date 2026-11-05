"""Dahua: a camera or recorder, its model and firmware, its disks, and motion now.

Written from the HACS integration "Dahua" (rroller, MIT: client.py, host.py,
refusals.py and their tests, which record what real devices answered). Not yet
run against a device: none was at hand. No code taken. Its README confirms some
Lorex and Amcrest devices too.

⚠️ The CGI API wants HTTP Digest, not Basic, and answers plain text, one
``key=value`` a line. ``magicBox.cgi?action=getSystemInfo`` gives ``deviceType``;
``getSoftwareVersion`` gives ``version=2.800.0000016.0.R,build:2020-06-05``.

⚠️ A device locks the source IP after repeated failed logins, and every further
try renews the lock. So after a 401 the cards leave the device alone for
``REFUSED_SECONDS``; saving the connection or pressing test asks at once. 403
with "Authority:check failure." is a login that was accepted for a call this
account or device may not make, which is not a wrong password.

⚠️ ``storageDevice.cgi?action=getDeviceAllInfo`` is the only disk read HTTP has:
state, errors and bytes per partition (``list.info[0].Detail[0].UsedBytes``, as
floats). SMART (temperature, wear) is not served at all. A device that will not
answer it has no disks to show, which is no fault of the device.

⚠️ ``eventManager.cgi?action=getEventIndexes&code=VideoMotion`` lists the channels
with motion now as ``channels[0]=5``, counted from nought, and none as an empty
answer. A device that will not answer tells nothing (an SL300 is 404 on these
paths), so the card then leaves motion out rather than saying nought.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

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
    percent,
    percent_text,
)

_DISK = re.compile(r"^list\.info\[(\d+)\]\.(.+)$")
_DETAIL = re.compile(r"^Detail\[\d+\]\.(IsError|TotalBytes|UsedBytes)$")
_CHANNEL = re.compile(r"^channels\[\d+\]=(\d+)$")
#: How long a refused login keeps the cards from asking again.
REFUSED_SECONDS = 900
REFUSED_KEY = "dahua:refused"
REFUSED_HINT = "Too many wrong tries lock this address out on the device for a while; check them before trying again."


def parse_lines(text: str) -> dict[str, str]:
    """``key=value`` a line; a line without one is not part of the answer."""
    found = {}
    for line in text.splitlines():
        key, equals, value = line.strip().partition("=")
        if equals and key:
            found[key] = value.strip()
    return found


def _bytes(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_disks(lines: dict[str, str]) -> list[dict[str, Any]]:
    """One entry per disk, its partitions added up."""
    disks: dict[int, dict[str, Any]] = {}
    for key, value in lines.items():
        found = _DISK.match(key)
        if not found:
            continue
        disk = disks.setdefault(int(found.group(1)), {"name": "", "state": "", "total": 0.0, "used": 0.0, "error": False})
        rest = found.group(2)
        detail = _DETAIL.match(rest)
        if rest == "Name":
            disk["name"] = value
        elif rest == "State":
            disk["state"] = value
        elif detail and detail.group(1) == "IsError":
            disk["error"] = disk["error"] or value.lower() == "true"
        elif detail and detail.group(1) == "TotalBytes":
            disk["total"] += _bytes(value)
        elif detail and detail.group(1) == "UsedBytes":
            disk["used"] += _bytes(value)
    return [disks[index] for index in sorted(disks) if disks[index]["name"]]


def healthy(disk: dict[str, Any]) -> bool:
    return str(disk.get("state")).lower() == "success" and not disk.get("error")


class DahuaAdapter(Adapter):
    kind = "dahua"
    label = "Dahua"
    category = "monitoring"
    description = "A Dahua or Amcrest camera or recorder: model and firmware, whether there is motion now, and its disks."
    icon = "lucide:camera"
    beta = True
    docs_url = "https://github.com/rroller/dahua"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://192.168.1.20"),
        Field("username", "User name", required=True, default="admin"),
        Field("password", "Password", type="password", secret=True, required=True, help="Too many wrong tries lock this address out on the device for a while."),
        Field("insecure", "Ignore TLS errors", type="bool", default=True),
    )
    widgets = (
        WidgetType(kind="device", label="Camera or recorder", description="Motion on how many channels now, the model, the firmware and the state of the disks.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("motion",)),
        WidgetType(kind="disks", label="Disks", description="Every disk or memory card with its state and how full it is. Recorders overwrite the oldest, so full is normal.",
                   renderer="list", default_size=(3, 2), refresh_seconds=300),
    )

    async def _call(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 0) -> httpx.Response:
        if cache and time.time() < float(ctx.cache.get(REFUSED_KEY) or 0):
            raise AuthFailed("The device refused the user name or password.", hint=REFUSED_HINT)
        response = await ctx.request("GET", f"{base_url(config)}/cgi-bin/{path}",
                                     auth=httpx.DigestAuth(str(config.get("username") or ""), str(config.get("password") or "")),
                                     verify=not config.get("insecure", True), cache_seconds=cache, auth_errors=False)
        if response.status_code != 200 and cache:
            # A refusal or a bad moment is not worth keeping for the hour a model is.
            ctx.forget_answers()
        if response.status_code == 401:
            ctx.cache[REFUSED_KEY] = time.time() + REFUSED_SECONDS
            raise AuthFailed("The device refused the user name or password.", hint=REFUSED_HINT)
        return response

    async def _lines(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> dict[str, str]:
        response = await self._call(config, ctx, path, cache)
        if response.status_code == 403:
            raise AuthFailed("This account may not read the device.", hint="Use an account with more rights on the device.")
        if response.status_code >= 400:
            raise AdapterError(f"The device answered with HTTP {response.status_code}.", code="http_error", hint="Check the URL; it is the device's own address.")
        return parse_lines(response.text)

    async def _optional(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> str | None:
        """The answer's text, or None when the device will not give this one."""
        response = await self._call(config, ctx, path, cache)
        return response.text if response.status_code == 200 else None

    async def _system(self, config: dict[str, Any], ctx: Context, cache: float = 3600) -> dict[str, str]:
        found = await self._lines(config, ctx, "magicBox.cgi?action=getSystemInfo", cache)
        if not found.get("deviceType") and not found.get("serialNumber"):
            raise AdapterError("This address answers, but not the way a Dahua device does.", code="not_dahua", hint="Check the URL; it is the device's own address.")
        return found

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        return f"{(await self._system(config, ctx, cache=0)).get('deviceType') or 'The device'} answers."

    async def _disks(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        return parse_disks(parse_lines(await self._optional(config, ctx, "storageDevice.cgi?action=getDeviceAllInfo", 120) or ""))

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "disks":
            return self._list(await self._disks(config, ctx))
        system = await self._system(config, ctx)
        version = parse_lines(await self._optional(config, ctx, "magicBox.cgi?action=getSoftwareVersion", 3600) or "")
        motion = await self._optional(config, ctx, "eventManager.cgi?action=getEventIndexes&code=VideoMotion", 5)
        channels = None if motion is None else {int(found.group(1)) for line in motion.splitlines() if (found := _CHANNEL.match(line.strip()))}
        return self._device(system, version.get("version", ""), channels, await self._disks(config, ctx))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _device(system: dict[str, str], version: str, channels: set[int] | None, disks: list[dict[str, Any]]) -> WidgetData:
        model = system.get("deviceType") or "?"
        secondary: list[dict[str, Any]] = []
        if channels is not None:
            secondary.append({"label": "Model", "value": model})
        if version:
            secondary.append({"label": "Firmware", "value": version.split(",")[0]})
        if channels:
            secondary.append({"label": "Channels", "value": ", ".join(str(one + 1) for one in sorted(channels))})
        bad = [disk["name"] for disk in disks if not healthy(disk)]
        if disks:
            secondary.append({"label": "Disks", "value": f"{len(disks) - len(bad)} / {len(disks)}"})
        primary = {"label": "Model", "value": model} if channels is None else {"label": "Motion now", "value": len(channels)}
        return WidgetData(status="bad" if bad else "ok", primary=primary, secondary=secondary,
                          metrics={} if channels is None else {"motion": float(len(channels))},
                          meta={"status_reason": f"Disk in trouble: {bad[0]}"} if bad else {})

    @staticmethod
    def _list(disks: list[dict[str, Any]]) -> WidgetData:
        items = []
        for disk in disks:
            share = percent(disk["used"], disk["total"]) if disk["total"] > 0 else None
            fine = healthy(disk)
            items.append({
                "title": str(disk["name"]),
                "subtitle": f"{human_bytes(disk['used'])} of {human_bytes(disk['total'])}" if disk["total"] > 0 else str(disk["state"]),
                "status": "ok" if fine else "bad",
                "value": percent_text(share) if fine and share is not None else str(disk["state"] or "?"),
                **({"progress": share} if share is not None else {}),
            })
        return WidgetData(status="ok" if all(item["status"] == "ok" for item in items) else "bad", items=items,
                          meta={"empty": "This device reports no disk or memory card."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        gb = 1024**3
        disks = [{"name": "/dev/sda", "state": "Success", "total": 5516 * gb, "used": 5400 * gb, "error": False},
                 {"name": "/dev/sdb", "state": "Success", "total": 5516 * gb, "used": fake.walk("dahua-sdb", tick // 30, 2700, 2900) * gb, "error": False}]
        if widget_kind == "disks":
            return self._list(disks)
        channels = {2} if tick % 5 == 0 else {0, 3} if tick % 9 == 0 else set()
        return self._device({"deviceType": "DHI-NVR5464-16P-EI"}, "2.800.0000016.0.R,build:2020-06-05", channels, disks)


ADAPTER = DahuaAdapter()
