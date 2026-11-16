"""QNAP: processor, memory and temperature of a NAS, how full its volumes are, and the health of its disks.

QNAP publishes no description of these calls. Written from qnapstats 0.6.2 (MIT, the library Home Assistant's QNAP
integration uses, ``qnapstats/qnap_stats.py``), which reads the same ``cgi-bin`` pages QTS's own interface does.
Not yet run against a NAS.

- ``POST /cgi-bin/authLogin.cgi`` with the form fields ``user`` and ``pwd``, the password in base64, answers XML with
  ``authPassed`` 1 and ``authSid``. Every later page takes the session as ``sid=`` in its address; the password never
  does.
- Every answer is XML under ``QDocRoot``. A page asked with a session that ran out answers 200 with ``authPassed`` 0,
  not 401, so that is read as "sign in again", once.
- ``management/manaRequest.cgi?subfunc=sysinfo&hd=no&multicpu=1``: ``func/ownContent/root`` holds ``cpu_usage``
  (with a percent sign), ``total_memory`` and ``free_memory`` in MB, ``sys_tempc``, ``cpu_tempc`` and ``uptime_day``,
  ``_hour``, ``_min``; ``model/displayModelName`` and ``firmware/version`` name the box.
- ``management/chartReq.cgi?chart_func=disk_usage&disk_select=all&include=all``: ``volumeList/volume`` names each
  volume (``volumeValue``, ``volumeLabel``), ``volumeUseList/volumeUse`` gives ``free_size`` and ``total_size`` in
  bytes. Volumes the system reserves appear in the second list only and are left out.
- ``disk/qsmart.cgi?func=all_hd_data``: ``Disk_Info/entry`` per slot with ``HDNo``, ``Model``, ``Health`` and
  ``Temperature/oC``; an empty slot has no model.

⚠️ The session rides in the address because QTS reads it there; it is a session of the account, not its password.
A user with two-step verification cannot sign in this way; use an account without it, best one with as few rights as
the cards need.
"""

from __future__ import annotations

import base64
import time
from typing import Any
from xml.etree import ElementTree

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
)

SESSION_KEY = "qnap:sid"
#: An answer of a NAS is a few kilobytes; a hundred times that is something else.
MAX_ANSWER = 512 * 1024
HEALTHY = {"OK", "GOOD", "NORMAL"}


def parse(text: str) -> ElementTree.Element:
    """``QDocRoot`` of an answer, refusing anything that declares a document type or entities."""
    if len(text) > MAX_ANSWER or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise AdapterError("The NAS answered with something that is not a QTS page.", code="not_qnap")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise AdapterError("The NAS did not answer with XML.", code="not_qnap",
                           hint="Check the URL; it should be the address of the QTS interface.") from error
    if root.tag != "QDocRoot":
        raise AdapterError("The address did not answer like a QNAP.", code="not_qnap",
                           hint="Check the URL; it should be the address of the QTS interface.")
    return root


def _number(node: ElementTree.Element | None, path: str) -> float | None:
    text = (node.findtext(path) if node is not None else None) or ""
    try:
        return float(text.replace("%", "").strip())
    except ValueError:
        return None


class QnapAdapter(Adapter):
    kind = "qnap"
    label = "QNAP"
    category = "hosts"
    description = "A QNAP NAS: processor, memory and temperature, how full its volumes are, and the health of its disks."
    icon = "qnap"
    beta = True
    docs_url = ""
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://nas.local:8080",
              help="The address of the QTS interface, with its port."),
        Field("username", "User name", required=True),
        Field("password", "Password", type="password", secret=True, required=True,
              help="An account without two-step verification."),
        Field("insecure", "Ignore TLS errors", type="bool", default=True, help="Most NAS use a self-signed certificate."),
    )
    widgets = (
        WidgetType(
            kind="system",
            label="System",
            description="Processor and memory use, temperature and uptime.",
            renderer="value",
            default_size=(3, 2),
            min_size=(1, 1),
            refresh_seconds=60,
            metrics=("cpu", "memory", "temperature"),
        ),
        WidgetType(
            kind="volumes",
            label="Volumes",
            description="How full each volume is. Yellow from 85 %, red from 95 %.",
            renderer="list",
            default_size=(3, 3),
            refresh_seconds=600,
        ),
        WidgetType(
            kind="disks",
            label="Disks",
            description="Each disk with its health and temperature. Red when a disk is not healthy.",
            renderer="list",
            default_size=(3, 3),
            refresh_seconds=600,
        ),
    )

    async def _page(self, config: dict[str, Any], ctx: Context, method: str, path: str, *, data: dict[str, str] | None = None,
                    params: dict[str, str] | None = None) -> ElementTree.Element:
        response = await ctx.request(method, f"{base_url(config)}/cgi-bin/{path}", data=data, params=params,
                                     verify=not config.get("insecure", True), auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("The NAS refused the user name or password.")
        if response.status_code >= 400:
            raise AdapterError(f"The NAS answered with HTTP {response.status_code}.", code="http_error")
        return parse(response.text)

    async def _login(self, config: dict[str, Any], ctx: Context) -> str:
        password = base64.b64encode(str(config.get("password") or "").encode()).decode("ascii")
        root = await self._page(config, ctx, "POST", "authLogin.cgi", data={"user": str(config.get("username") or ""), "pwd": password})
        sid = (root.findtext("authSid") or "").strip()
        if root.findtext("authPassed") != "1" or not sid:
            raise AuthFailed("The NAS refused the user name or password.",
                             hint="An account without two-step verification, which this sign-in cannot answer.")
        ctx.cache[SESSION_KEY] = sid
        return sid

    async def _read(self, config: dict[str, Any], ctx: Context, path: str, query: dict[str, str], cache: float) -> ElementTree.Element:
        key = f"qnap:page:{path}"
        kept = ctx.cache.get(key)
        if cache and kept and time.time() - kept[0] < cache:
            return kept[1]
        for attempt in range(2):
            sid = ctx.cache.get(SESSION_KEY) or await self._login(config, ctx)
            root = await self._page(config, ctx, "GET", path, params={**query, "sid": sid})
            if root.findtext("authPassed") != "0":
                ctx.cache[key] = (time.time(), root)
                return root
            # The session ran out: QTS says so in a 200.
            ctx.cache.pop(SESSION_KEY, None)
            if attempt:
                break
        raise AuthFailed("The NAS ended the session and refused a new one.")

    async def _system(self, config: dict[str, Any], ctx: Context, cache: float = 30) -> ElementTree.Element:
        return await self._read(config, ctx, "management/manaRequest.cgi", {"subfunc": "sysinfo", "hd": "no", "multicpu": "1"}, cache)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        ctx.cache.pop(SESSION_KEY, None)
        root = await self._system(config, ctx, cache=0)
        model = root.findtext("model/displayModelName") or "A QNAP"
        return f"{model} with QTS {root.findtext('firmware/version') or '?'} answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "volumes":
            query = {"chart_func": "disk_usage", "disk_select": "all", "include": "all"}
            return self._volumes(await self._read(config, ctx, "management/chartReq.cgi", query, cache=300))
        if widget_kind == "disks":
            return self._disks(await self._read(config, ctx, "disk/qsmart.cgi", {"func": "all_hd_data"}, cache=300))
        return self._system_card(await self._system(config, ctx))

    @staticmethod
    def _system_card(root: ElementTree.Element) -> WidgetData:
        own = root.find("func/ownContent/root")
        cpu = _number(own, "cpu_usage")
        total, free = _number(own, "total_memory"), _number(own, "free_memory")
        memory = round((total - free) / total * 100, 1) if total and free is not None else None
        temperature = _number(own, "cpu_tempc") or _number(own, "sys_tempc")
        secondary: list[dict[str, Any]] = []
        if memory is not None:
            secondary.append({"label": "Memory", "value": round(memory), "unit": "%"})
        if temperature is not None:
            secondary.append({"label": "Temperature", "value": round(temperature), "unit": "°C"})
        days, hours, minutes = (_number(own, f"uptime_{part}") for part in ("day", "hour", "min"))
        if days is not None:
            secondary.append({"label": "Uptime", "value": duration_short(days * 86400 + (hours or 0) * 3600 + (minutes or 0) * 60)})
        model = root.findtext("model/displayModelName")
        if model:
            secondary.append({"label": "Model", "value": model})
        metrics = {name: value for name, value in (("cpu", cpu), ("memory", memory), ("temperature", temperature)) if value is not None}
        hot = temperature is not None and temperature >= 70
        return WidgetData(
            status="warn" if hot or (memory is not None and memory >= 95) else "ok",
            primary={"label": "Processor", "value": round(cpu) if cpu is not None else "?", "unit": "%" if cpu is not None else ""},
            secondary=secondary,
            metrics=metrics,
        )

    @staticmethod
    def _volumes(root: ElementTree.Element) -> WidgetData:
        names = {
            (volume.findtext("volumeValue") or "").strip(): (volume.findtext("volumeLabel") or f"Volume {volume.findtext('volumeValue')}").strip()
            for volume in root.findall("volumeList/volume")
        }
        items = []
        for use in root.findall("volumeUseList/volumeUse"):
            number = (use.findtext("volumeValue") or "").strip()
            total, free = _number(use, "total_size"), _number(use, "free_size")
            if number not in names or not total or free is None:
                continue
            share = (total - free) / total * 100
            items.append({
                "title": names[number],
                "subtitle": f"{human_bytes(free)} free of {human_bytes(total)}",
                "progress": round(share, 1),
                "value": f"{share:.0f}%",
                "status": "bad" if share >= 95 else ("warn" if share >= 85 else "ok"),
            })
        worst = "bad" if any(item["status"] == "bad" for item in items) else ("warn" if any(item["status"] == "warn" for item in items) else "ok")
        return WidgetData(status=worst, items=items, secondary=[{"label": "Volumes", "value": len(items)}])

    @staticmethod
    def _disks(root: ElementTree.Element) -> WidgetData:
        items = []
        for disk in root.findall("Disk_Info/entry"):
            model = (disk.findtext("Model") or "").strip()
            if not model:
                continue
            health = (disk.findtext("Health") or "?").strip()
            temperature = _number(disk, "Temperature/oC")
            items.append({
                "title": f"Disk {(disk.findtext('HDNo') or '?').strip()}",
                "subtitle": " · ".join(part for part in (model, (disk.findtext("Capacity") or "").strip()) if part),
                "value": f"{temperature:g} °C" if temperature is not None else "",
                "status": "ok" if health.upper() in HEALTHY else "bad",
                "hint": health,
            })
        return WidgetData(status="bad" if any(item["status"] == "bad" for item in items) else "ok",
                          items=items, secondary=[{"label": "Disks", "value": len(items)}])

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "volumes":
            used = fake.walk("qnap-volume", tick, 0.62, 0.66)
            items = [{"title": "DataVol1", "subtitle": f"{human_bytes(7.2e12 * (1 - used))} free of {human_bytes(7.2e12)}",
                      "progress": round(used * 100, 1), "value": f"{used * 100:.0f}%", "status": "ok"},
                     {"title": "Backups", "subtitle": f"{human_bytes(0.5e12)} free of {human_bytes(3.6e12)}",
                      "progress": 86.1, "value": "86%", "status": "warn"}]
            return WidgetData(status="warn", items=items, secondary=[{"label": "Volumes", "value": 2}])
        if widget_kind == "disks":
            items = [{"title": f"Disk {slot}", "subtitle": "WDC WD40EFRX · 4.00 TB", "value": f"{36 + slot} °C", "status": "ok", "hint": "OK"} for slot in range(1, 5)]
            return WidgetData(status="ok", items=items, secondary=[{"label": "Disks", "value": 4}])
        cpu = fake.walk("qnap-cpu", tick, 4, 31)
        return WidgetData(
            status="ok",
            primary={"label": "Processor", "value": round(cpu), "unit": "%"},
            secondary=[{"label": "Memory", "value": 41, "unit": "%"}, {"label": "Temperature", "value": 47, "unit": "°C"},
                       {"label": "Uptime", "value": duration_short(19 * 86400 + 4 * 3600)}, {"label": "Model", "value": "TS-464"}],
            metrics={"cpu": cpu, "memory": 41.0, "temperature": 47.0},
        )


ADAPTER = QnapAdapter()

