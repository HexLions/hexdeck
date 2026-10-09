"""Spoolman: the filament spools in use and the ones about to run out.

API v1, read-only: ``/api/v1/spool`` sorted by what is left, with the total in
the ``x-total-count`` header, and ``/api/v1/info`` for the version.

⚠️ Spoolman has no sign-in. A user name and password are for a proxy in front
of it.

⚠️ A spool knows how much is left only when its weight is known, from the
spool or its filament. Without one, ``remaining_weight`` is absent; such a
spool is listed without a bar and never counted as running out.

⚠️ Archived spools are left out by the API unless asked for, which is what a
card about the shelf wants.

Read from Spoolman's own OpenAPI document (/api/v1/openapi.json) and checked
against Spoolman v0.27.0 running locally on 2026-10-09, verified by the
digest GitHub publishes for its release.
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
    measured,
)

SPOOLS_SECONDS = 120
#: Below this many grams a spool is worth a colour, unless the card says otherwise.
LOW_GRAMS = 100


def _name(spool: dict[str, Any]) -> str:
    filament = spool.get("filament") or {}
    vendor = (filament.get("vendor") or {}).get("name") if isinstance(filament.get("vendor"), dict) else ""
    parts = [str(vendor or ""), str(filament.get("name") or ""), str(filament.get("material") or "")]
    return " ".join(part for part in parts if part) or f"Spool {spool.get('id')}"


def _left(spool: dict[str, Any]) -> float | None:
    value = spool.get("remaining_weight")
    return float(value) if isinstance(value, (int, float)) else None


class SpoolmanAdapter(Adapter):
    kind = "spoolman"
    label = "Spoolman"
    category = "other"
    description = "The filament spools in use with what is left on each, the ones about to run out first."
    icon = "spoolman"
    docs_url = "https://github.com/Donkie/Spoolman/wiki"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://spoolman:7912"),
        Field("username", "User name", help="Only behind basic authentication."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    _LOW = Field("low_grams", "Running out below (g)", type="number", default=LOW_GRAMS)
    widgets = (
        WidgetType(kind="summary", label="Overview", description="How many spools are in use, how much filament is left in all, and how many are running out.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("left", "low"), options=(_LOW,)),
        WidgetType(kind="spools", label="Spools", description="The spools in use with what is left on each, the emptiest first, with material and place.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("location", "Place", placeholder="Printer 1", help="Only the spools in this place. Empty means all of them."),
                            _LOW, Field("limit", "Entries", type="number", default=8))),
    )

    async def _spools(self, config: dict[str, Any], ctx: Context, location: str = "") -> tuple[list[dict[str, Any]], int]:
        auth = (str(config["username"]), str(config.get("password") or "")) if config.get("username") else None
        params: dict[str, Any] = {"sort": "remaining_weight:asc"}
        if location.strip():
            params["location"] = location.strip()
        response = await ctx.request("GET", f"{base_url(config)}/api/v1/spool", params=params, auth=auth,
                                     headers={"Accept": "application/json"}, verify=not config.get("insecure"),
                                     cache_seconds=SPOOLS_SECONDS, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("The proxy in front of Spoolman refused the sign-in.",
                             hint="Spoolman itself asks for nothing; a user name and password are for a proxy's basic authentication.")
        if response.status_code >= 400:
            raise AdapterError(f"Spoolman answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of Spoolman itself, usually on port 7912, without /api.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Spoolman did not answer with JSON.", code="not_json") from failure
        spools = [one for one in (answer if isinstance(answer, list) else []) if isinstance(one, dict)]
        written = response.headers.get("x-total-count")
        return spools, int(written) if written and written.isdigit() else len(spools)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        spools, total = await self._spools(config, ctx)
        info = await ctx.request("GET", f"{base_url(config)}/api/v1/info", verify=not config.get("insecure"), auth_errors=False)
        version = ""
        try:
            version = str(info.json().get("version") or "") if info.status_code == 200 else ""
        except ValueError:
            pass
        return f"Spoolman{' ' + version if version else ''} answers with {total} spool{'s' if total != 1 else ''} in use."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        low = float(options.get("low_grams") or LOW_GRAMS)
        if widget_kind == "spools":
            spools, _ = await self._spools(config, ctx, str(options.get("location") or ""))
            return self._list(spools, low, max(1, int(options.get("limit") or 8)), str(options.get("location") or ""))
        spools, total = await self._spools(config, ctx)
        return self._summary(spools, total, low)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(spools: list[dict[str, Any]], total: int, low: float) -> WidgetData:
        known = [left for left in (_left(one) for one in spools) if left is not None]
        running_out = sum(1 for left in known if left < low)
        kilos = sum(known) / 1000.0
        secondary: list[dict[str, Any]] = [{"label": "Left", "value": f"{kilos:.1f} kg", "metric": "left"}]
        if running_out:
            secondary.append({"label": "Running out", "value": running_out, "metric": "low"})
        return WidgetData(
            status="warn" if running_out else "ok",
            primary={"label": "Spools in use", "value": total},
            secondary=secondary,
            metrics=measured({"left": round(kilos, 2), "low": float(running_out)}),
        )

    @staticmethod
    def _list(spools: list[dict[str, Any]], low: float, limit: int, location: str) -> WidgetData:
        rows = []
        for spool in spools:
            left = _left(spool)
            initial = spool.get("initial_weight")
            filament = spool.get("filament") or {}
            row: dict[str, Any] = {
                "id": spool.get("id"),
                "title": _name(spool),
                "subtitle": " · ".join(part for part in (str(spool.get("location") or ""), f"#{filament.get('color_hex')}" if filament.get("color_hex") else "") if part),
                "value": f"{left:.0f} g" if left is not None else "",
                "status": "warn" if left is not None and left < low else "ok",
            }
            if left is not None and isinstance(initial, (int, float)) and initial > 0:
                row["progress"] = round(max(0.0, min(100.0, 100.0 * left / float(initial))), 1)
            rows.append(row)
        return WidgetData(
            status="warn" if any(row["status"] == "warn" for row in rows) else "ok",
            items=rows[:limit],
            meta={"empty": f"No spool in {location.strip()}" if location.strip() else "No spool in use"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        black = round(fake.walk("spool-black", tick, 40, 90), 0)
        spools = [
            {"id": 1, "location": "Printer 1", "remaining_weight": black, "initial_weight": 1000.0,
             "filament": {"name": "Galaxy Black", "material": "PLA", "color_hex": "1B1B1F", "vendor": {"name": "Prusament"}}},
            {"id": 2, "location": "Dry box", "remaining_weight": 790.0, "initial_weight": 1000.0,
             "filament": {"name": "Jet Black", "material": "PETG", "color_hex": "000000", "vendor": {"name": "Prusament"}}},
            {"id": 3, "location": "Shelf", "remaining_weight": 750.0, "initial_weight": 750.0,
             "filament": {"name": "Generic White", "material": "PLA", "color_hex": "FFFFFF"}},
            {"id": 4, "location": "Printer 2", "remaining_weight": 412.0, "initial_weight": 1000.0,
             "filament": {"name": "Signal Orange", "material": "ASA", "color_hex": "F28C28", "vendor": {"name": "Extrudr"}}},
        ]
        spools.sort(key=lambda one: one["remaining_weight"])
        low = float(options.get("low_grams") or LOW_GRAMS)
        if widget_kind == "spools":
            return self._list(spools, low, max(1, int(options.get("limit") or 8)), "")
        return self._summary(spools, len(spools), low)


ADAPTER = SpoolmanAdapter()
