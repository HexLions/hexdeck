"""The address the house shows to the world, and who it belongs to.

One call to a public service that needs no account, kept for an hour: a home
address does not move every minute, and the card is not worth a request per
refresh. When it does move, the card says what it was before, because that is
the moment a dynamic DNS entry and half the port forwards need looking at.

⚠️ The only card here that talks to a service outside the house by itself,
without a connection somebody set up. It is an ordinary GET to one address,
it sends nothing but the request, and the whole answer is a handful of public
facts about the line. Whoever does not want it does not add the card.
"""

from __future__ import annotations

import time
from typing import Any

from .base import Adapter, AdapterError, Context, Field, WidgetData, WidgetType

#: Free, no account, answers JSON with the address and the network behind it.
SERVICES = {
    "ipapi": ("https://ipapi.co/json/", "ipapi.co"),
    "ipwho": ("https://ipwho.is/", "ipwho.is"),
}


class PublicIpAdapter(Adapter):
    kind = "publicip"
    label = "Public address"
    category = "network"
    description = "The address your line shows to the world, the provider behind it, and a word when it changes."
    icon = "lucide:globe"
    beta = False
    needs_integration = False
    widgets = (
        WidgetType(
            kind="address",
            label="Public address",
            description="The address the house is seen at, with the provider and the place the service names.",
            renderer="value",
            default_size=(3, 2),
            min_size=(2, 2),
            refresh_seconds=900,
            options=(
                Field("service", "Asked of", type="select", default="ipapi",
                      options=(("ipapi", "ipapi.co"), ("ipwho", "ipwho.is")),
                      help="Both are free and need no account. Only the address is asked for; nothing about this installation is sent."),
                Field("cache_minutes", "Ask again after (minutes)", type="number", default=60,
                      help="A home address rarely moves. 0 asks on every refresh."),
                Field("hide_address", "Hide the address itself", type="bool", default=False,
                      help="For a board on a wall: the provider and the place are shown, the number is not."),
            ),
        ),
    )

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        answer = await self._ask(ctx, "ipapi", 0)
        return f"The line answers as {answer.get('ip') or 'an unknown address'}."

    @staticmethod
    async def _ask(ctx: Context, service: str, cache_minutes: float) -> dict[str, Any]:
        url, name = SERVICES.get(service, SERVICES["ipapi"])
        response = await ctx.request("GET", url, cache_seconds=max(0.0, cache_minutes) * 60, timeout=15, auth_errors=False)
        if response.status_code >= 400:
            raise AdapterError(f"{name} answered with HTTP {response.status_code}.", code="http_error",
                               hint="These services turn away a caller that asks too often. Leave the interval as it is, or pick the other one.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError(f"{name} did not answer with JSON.", code="bad_answer") from failure
        if not isinstance(answer, dict) or not answer.get("ip"):
            raise AdapterError(f"{name} named no address.", code="bad_answer")
        return answer

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        # ⚠️ ``or 60`` would make an explicit 0 mean an hour, and a card set to
        # ask every time would quietly not.
        wanted = options.get("cache_minutes", 60)
        minutes = 60.0 if wanted in (None, "") else float(wanted)
        answer = await self._ask(ctx, str(options.get("service") or "ipapi"), minutes)
        address = str(answer.get("ip") or "")
        # ipapi.co and ipwho.is name the same things differently.
        provider = str(answer.get("org") or answer.get("connection", {}).get("isp") or answer.get("asn") or "")
        city = str(answer.get("city") or "")
        country = str(answer.get("country_name") or answer.get("country") or "")
        place = ", ".join(part for part in (city, country) if part)
        known = ctx.cache.get("publicip:last")
        changed = known and known[1] != address
        ctx.cache["publicip:last"] = (time.time(), address)
        secondary = []
        if provider:
            secondary.append({"label": "Provider", "value": provider})
        if place:
            secondary.append({"label": "Place", "value": place})
        return WidgetData(
            status="warn" if changed else "ok",
            primary={"label": "Public address", "value": "hidden" if options.get("hide_address") else address},
            secondary=secondary,
            meta={"changed_from": known[1] if changed else "", "address": "" if options.get("hide_address") else address},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        return WidgetData(
            status="ok",
            primary={"label": "Public address", "value": "203.0.113.9"},
            secondary=[{"label": "Provider", "value": "Example Telecom"}, {"label": "Place", "value": "Rome, Italy"}],
            meta={"changed_from": "", "address": "203.0.113.9", "demo": True},
        )


ADAPTER = PublicIpAdapter()
