"""TP-Link Omada: the access points, switches and gateways of a controller, and
how many clients they carry.

Written from the HACS integration "TP-Link Omada Open API" (bullitt186, MIT,
version 1.11.1: api.py, auth.py, devices.py, config_flow.py, tests/conftest.py).
Not yet run against a controller: none was at hand. No code taken.

⚠️ The Open API has to be switched on and a client made: in the controller,
Settings, Platform Integration, Open API, in client credentials mode. Omada
Cloud's free Essentials tier has no Open API at all, and an OC200 only from
some firmware on, which says itself as a refusal or a 404.

⚠️ A token comes from ``POST /openapi/authorize/token?grant_type=client_credentials``
with ``omadacId``, ``client_id`` and ``client_secret`` as JSON, and goes as
``Authorization: AccessToken=<token>``: not Bearer. ``/api/info`` names the
``omadacId`` without signing in. The refresh grant is left alone (real
controllers want it as a form and answer -1001 otherwise): a new token from
the client credentials does the same.

⚠️ Answers are HTTP 200 with ``errorCode``, 0 being fine. -44112 and -44113 mean
the token ran out or is wrong, which earns one new token; other refusals say
themselves in ``msg``. ``detailStatus`` is the number to read: 14 to 17 are
connected, 30 to 33 heartbeats missed, 0 and 1 gone.

⚠️ ``uptime`` is seconds on some firmware and "4day(s) 17h 26m 57s" on others.
The clients come from v2 as a POST, and v1 as a GET where v2 answers -1600 or
404. Only the count is read (``totalRows`` with one row a page). Controllers
are self-signed as a rule, so TLS errors are ignored unless told otherwise.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

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
)

TOKEN_KEY = "omada:token"
ID_KEY = "omada:id"
MAX_SITES = 10
CONNECTED = {14, 15, 16, 17}
#: The words of ``detailStatus`` that are not simply connected, with the card's colour.
STATES = {0: ("Disconnected", "bad"), 1: ("Disconnected", "bad"), 10: ("Provisioning", "warn"), 11: ("Configuring", "warn"),
          12: ("Upgrading", "warn"), 13: ("Rebooting", "warn"), 20: ("Pending", "warn"), 21: ("Pending", "warn"),
          22: ("Adopting", "warn"), 23: ("Adopting", "warn"), 24: ("Adopt failed", "bad"), 25: ("Adopt failed", "bad"),
          26: ("Managed by others", "unknown"), 27: ("Managed by others", "unknown"), 30: ("Heartbeat missed", "bad"),
          31: ("Heartbeat missed", "bad"), 32: ("Heartbeat missed", "bad"), 33: ("Heartbeat missed", "bad"),
          40: ("Isolated", "warn"), 41: ("Isolated", "warn"), 50: ("Configuring", "warn")}
_UPTIME = re.compile(r"(?:(\d+)day\(s\))?\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")
TYPE_ORDER = {"gateway": 0, "switch": 1, "ap": 2}


def uptime_seconds(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    found = _UPTIME.fullmatch(str(value or "").strip())
    if not found or not any(found.groups()):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in found.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


class OmadaAdapter(Adapter):
    kind = "omada"
    label = "TP-Link Omada"
    category = "network"
    description = "A TP-Link Omada controller: its gateways, switches and access points, whether they are connected, and how many clients they carry."
    icon = "omada"
    beta = True
    docs_url = "https://github.com/bullitt186/ha-omada-open-api"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://omada.example.com:8043",
              help="The controller's own address; for Omada Cloud the address of your region's Open API."),
        Field("client_id", "Client ID", secret=True, required=True,
              help="In the controller: Settings, Platform Integration, Open API. Add a client in client credentials mode."),
        Field("client_secret", "Client secret", type="password", secret=True, required=True),
        Field("omada_id", "Omada ID", help="Empty: asked from the controller. Omada Cloud needs it filled in."),
        Field("site", "Site", help="Only this site, by name. Empty: every site, up to ten."),
        Field("insecure", "Ignore TLS errors", type="bool", default=True),
    )
    widgets = (
        WidgetType(kind="summary", label="Network devices", description="How many devices are connected, how many clients they carry, and the sites.",
                   renderer="value", default_size=(3, 2), refresh_seconds=120, metrics=("offline", "clients")),
        WidgetType(kind="devices", label="Devices", description="Every gateway, switch and access point with its state, clients, processor and uptime, those in trouble first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120,
                   options=(Field("limit", "Entries", type="number", default=12),)),
    )

    async def _omada_id(self, config: dict[str, Any], ctx: Context) -> str:
        given = str(config.get("omada_id") or "").strip()
        if given:
            return given
        if ctx.cache.get(ID_KEY):
            return str(ctx.cache[ID_KEY])
        response = await ctx.request("GET", f"{base_url(config)}/api/info", verify=not config.get("insecure", True), auth_errors=False)
        try:
            found = str(((response.json() or {}).get("result") or {}).get("omadacId") or "")
        except (ValueError, AttributeError):
            found = ""
        if response.status_code >= 400 or not found:
            raise AdapterError("This address does not name an Omada controller.", code="not_omada",
                               hint="Check the URL. Omada Cloud does not say its ID: fill it in.")
        ctx.cache[ID_KEY] = found
        return found

    async def _token(self, config: dict[str, Any], ctx: Context, fresh: bool = False) -> str:
        kept = ctx.cache.get(TOKEN_KEY)
        if kept and not fresh and kept[1] > time.monotonic():
            return str(kept[0])
        response = await ctx.request(
            "POST", f"{base_url(config)}/openapi/authorize/token", params={"grant_type": "client_credentials"},
            json_body={"omadacId": await self._omada_id(config, ctx), "client_id": str(config.get("client_id") or "").strip(),
                       "client_secret": str(config.get("client_secret") or "")},
            verify=not config.get("insecure", True), auth_errors=False)
        if response.status_code == 404:
            raise AdapterError("This controller has no Open API.", code="no_open_api",
                               hint="It is under Settings, Platform Integration; an OC200 needs a recent firmware, and Omada Cloud's free tier has none.")
        try:
            answer = response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way an Omada controller does.", code="not_omada") from error
        result = answer.get("result") if isinstance(answer, dict) else None
        if response.status_code >= 400 or not isinstance(answer, dict) or answer.get("errorCode") != 0 or not isinstance(result, dict):
            reason = str((answer or {}).get("msg") or f"HTTP {response.status_code}") if isinstance(answer, dict) else f"HTTP {response.status_code}"
            raise AuthFailed(f"The controller refused the client: {reason}",
                             hint="Check the client ID and secret under Settings, Platform Integration, Open API; the client needs client credentials mode.")
        token = str(result.get("accessToken") or "")
        # A minute early, rather than one refresh with a dead token.
        ctx.cache[TOKEN_KEY] = (token, time.monotonic() + float(result.get("expiresIn") or 3600) - 60)
        return token

    async def _call(self, config: dict[str, Any], ctx: Context, method: str, path: str, *, params: dict[str, Any] | None = None,
                    json_body: Any = None, cache: float = 0) -> dict[str, Any]:
        """``result`` of one call; a token that ran out earns one new one."""
        omada_id = quote(await self._omada_id(config, ctx), safe="")
        url = f"{base_url(config)}/openapi/{path.format(id=omada_id)}"
        for attempt in (0, 1):
            token = await self._token(config, ctx, fresh=bool(attempt))
            options: dict[str, Any] = {"cache_seconds": cache} if method == "GET" and cache else {}
            response = await ctx.request(method, url, headers={"Authorization": f"AccessToken={token}"}, params=params, json_body=json_body,
                                         verify=not config.get("insecure", True), auth_errors=False, **options)
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code in (401, 403):
                raise AuthFailed("The controller refused the client.", hint="The client needs access to the sites and to viewing them.")
            if response.status_code >= 400:
                raise AdapterError(f"The controller answered with HTTP {response.status_code}.", code="http_error", hint="Check the URL.")
            try:
                answer = response.json()
            except ValueError as error:
                raise AdapterError("This address answers, but not the way an Omada controller does.", code="not_omada") from error
            code = answer.get("errorCode") if isinstance(answer, dict) else None
            if code in (-44112, -44113) and attempt == 0:
                continue
            if code in (-44112, -44113):
                raise AuthFailed("The controller keeps refusing the token.", hint="Make the client anew under Settings, Platform Integration, Open API.")
            if code != 0:
                reason = str(answer.get("msg") or "no reason given") if isinstance(answer, dict) else "no reason given"
                raise AdapterError(f"The controller refused: {reason} ({code})", code="omada_error", hint="The client may lack access to this site.")
            return answer.get("result") if isinstance(answer.get("result"), dict) else {"data": answer.get("result")}
        raise AdapterError("The controller keeps refusing the token.", code="auth_failed")

    async def _sites(self, config: dict[str, Any], ctx: Context, cache: float = 300) -> list[dict[str, Any]]:
        found = await self._call(config, ctx, "GET", "v1/{id}/sites", params={"pageSize": 100, "page": 1}, cache=cache)
        sites = [one for one in found.get("data") or [] if isinstance(one, dict)]
        wanted = str(config.get("site") or "").strip().lower()
        if wanted:
            sites = [one for one in sites if str(one.get("name") or "").lower() == wanted]
            if not sites:
                raise AdapterError(f"The controller has no site called {config.get('site')}.", code="unknown_site", hint="Write it as the controller does, or leave it empty.")
        return sites[:MAX_SITES]

    async def _clients(self, config: dict[str, Any], ctx: Context, site: str) -> int | None:
        where = quote(site, safe="")
        try:
            found = await self._call(config, ctx, "POST", f"v2/{{id}}/sites/{where}/clients", json_body={"page": 1, "pageSize": 1, "scope": 1, "filters": {}})
        except AuthFailed:
            raise
        except AdapterError as failure:
            # Older controllers have only the first version of this call.
            if "-1600" not in str(failure) and "404" not in str(failure):
                raise
            found = await self._call(config, ctx, "GET", f"v1/{{id}}/sites/{where}/clients", params={"page": 1, "pageSize": 1})
        total = found.get("totalRows")
        return int(total) if isinstance(total, int | float) else None

    async def _read(self, config: dict[str, Any], ctx: Context, with_clients: bool) -> tuple[list[dict[str, Any]], int, int | None]:
        sites = await self._sites(config, ctx)
        devices: list[dict[str, Any]] = []
        clients: int | None = None
        for site in sites:
            where = quote(str(site.get("siteId") or ""), safe="")
            found = await self._call(config, ctx, "GET", f"v1/{{id}}/sites/{where}/devices", params={"pageSize": 100, "page": 1}, cache=60)
            devices.extend({**one, "_site": site.get("name") or ""} for one in found.get("data") or [] if isinstance(one, dict))
            if with_clients:
                count = await self._clients(config, ctx, str(site.get("siteId") or ""))
                if count is not None:
                    clients = (clients or 0) + count
        return devices, len(sites), clients

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        sites = await self._sites(config, ctx, cache=0)
        return f"The controller answers with {len(sites)} site{'' if len(sites) == 1 else 's'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        devices, sites, clients = await self._read(config, ctx, with_clients=widget_kind == "summary")
        if widget_kind == "devices":
            return self._devices(devices, max(1, int(options.get("limit") or 12)), sites > 1)
        return self._summary(devices, sites, clients)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _state(device: dict[str, Any]) -> tuple[str, str]:
        code = device.get("detailStatus")
        if code in CONNECTED:
            return "Connected", "ok"
        return STATES.get(code, ("Unknown", "unknown"))

    @classmethod
    def _summary(cls, devices: list[dict[str, Any]], sites: int, clients: int | None) -> WidgetData:
        states = [cls._state(one)[1] for one in devices]
        online = sum(1 for one in devices if one.get("detailStatus") in CONNECTED)
        secondary: list[dict[str, Any]] = []
        if clients is not None:
            secondary.append({"label": "Clients", "value": clients})
        if sites > 1:
            secondary.append({"label": "Sites", "value": sites})
        bad = states.count("bad")
        metrics = {"offline": float(bad), **({"clients": float(clients)} if clients is not None else {})}
        return WidgetData(status="bad" if bad else "warn" if "warn" in states else "ok", primary={"label": "Connected", "value": f"{online} / {len(devices)}"},
                          secondary=secondary, metrics=metrics, meta={"status_reason": f"{bad} device(s) not answering"} if bad else {})

    @classmethod
    def _devices(cls, devices: list[dict[str, Any]], limit: int, many_sites: bool) -> WidgetData:
        order = {"bad": 0, "warn": 1, "unknown": 2, "ok": 3}

        def key(device: dict[str, Any]) -> tuple[int, int, str]:
            return order[cls._state(device)[1]], TYPE_ORDER.get(str(device.get("type") or ""), 3), str(device.get("name") or "").lower()

        items = []
        for device in sorted(devices, key=key)[:limit]:
            word, state = cls._state(device)
            up = uptime_seconds(device.get("uptime"))
            parts = [str(device.get("_site") or "") if many_sites else "", str(device.get("model") or ""),
                     f"{device['clientNum']} clients" if device.get("clientNum") else "",
                     f"CPU {device['cpuUtil']}%" if isinstance(device.get("cpuUtil"), int | float) and state == "ok" else "",
                     f"up {duration_short(up)}" if up is not None and state == "ok" else ""]
            items.append({"title": str(device.get("name") or device.get("mac") or "?"), "subtitle": " · ".join(part for part in parts if part),
                          "status": state, "value": word})
        worst = "bad" if any(item["status"] == "bad" for item in items) else "warn" if any(item["status"] == "warn" for item in items) else "ok"
        return WidgetData(status=worst, items=items, meta={"empty": "The controller has no device yet."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        lost = tick % 8 == 7
        devices = [
            {"name": "Gateway", "type": "gateway", "model": "ER605", "detailStatus": 14, "cpuUtil": int(fake.walk("omada-gw", tick, 4, 22)), "uptime": 860000},
            {"name": "Core switch", "type": "switch", "model": "TL-SG3428", "detailStatus": 14, "cpuUtil": 7, "uptime": "12day(s) 5h 30m 10s"},
            {"name": "AP Living room", "type": "ap", "model": "EAP650", "detailStatus": 14, "clientNum": int(fake.walk("omada-ap1", tick, 8, 19)), "cpuUtil": 12, "uptime": 860000},
            {"name": "AP Garage", "type": "ap", "model": "EAP225-Outdoor", "detailStatus": 30 if lost else 15, "clientNum": 3, "cpuUtil": 5, "uptime": 86000},
        ]
        if widget_kind == "devices":
            return self._devices(devices, max(1, int(options.get("limit") or 12)), False)
        return self._summary(devices, 1, int(fake.walk("omada-clients", tick, 24, 41)))


ADAPTER = OmadaAdapter()
