"""openHAB: the Things that are offline, and the Items you pick, with a switch.

openHAB's REST API lives under ``/rest``. ``/rest/items`` with ``fields``
gives the Items with their state; ``/rest/things?summary=true`` gives every
Thing with its ``statusInfo`` (``ONLINE``, ``OFFLINE`` with a detail such as
``COMMUNICATION_ERROR``, ``UNINITIALIZED`` with ``DISABLED`` when switched
off). ``/rest/`` itself names the version.

⚠️ Items are readable without a token, as an anonymous user is treated as a
user by default; Things are not, they need an API token made by an
administrator (Profile, Create API token), sent as a Bearer token.

⚠️ A state already carries its unit (``21.4 °C``). ``NULL`` and ``UNDEF``
mean the Item has no state yet, not zero.

⚠️ The switch sends a command, ``ON`` or ``OFF``, to a Switch Item the card
lists, as plain text to ``/rest/items/<name>``.

Checked against openHAB 5.2.3 running locally on 2026-10-13, with ten Items
of eight types (one never set), four Things of the Network binding (one
disabled), an API token, a wrong one and none.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from . import demo as fake
from .base import (
    Action,
    Adapter,
    AdapterError,
    AuthFailed,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
)

ITEM_FIELDS = "name,label,type,state,groupNames"
READ_SECONDS = 15


def shown_state(item: dict[str, Any]) -> str:
    """An Item's state as the card shows it."""
    state = str(item.get("state") if item.get("state") is not None else "NULL")
    if state in ("NULL", "UNDEF", ""):
        return "–"
    kind = str(item.get("type") or "")
    if kind in ("Switch", "Contact"):
        return {"ON": "on", "OFF": "off", "OPEN": "open", "CLOSED": "closed"}.get(state, state.lower())
    if kind in ("Rollershutter", "Dimmer") or kind == "Number:Dimensionless":
        return f"{state} %" if state.replace(".", "", 1).isdigit() else state
    return state


def wanted_items(items: list[dict[str, Any]], names: str) -> list[dict[str, Any]]:
    """The Items named, and the members of a Group named, in the order written."""
    picked: list[dict[str, Any]] = []
    by_name = {str(item.get("name")): item for item in items}
    for name in (part.strip() for part in names.replace("\n", ",").split(",")):
        item = by_name.get(name)
        if item is None:
            continue
        if item.get("type") == "Group":
            picked.extend(one for one in items if name in (one.get("groupNames") or []) and one not in picked)
        elif item not in picked:
            picked.append(item)
    return picked


class OpenHABAdapter(Adapter):
    kind = "openhab"
    label = "openHAB"
    category = "home"
    description = "openHAB: which Things are offline, the Items you pick with their state, and a switch for the Switch Items."
    icon = "openhab"
    docs_url = "https://www.openhab.org/docs/configuration/restdocs.html"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://openhab:8080"),
        Field("token", "API token", type="password", secret=True,
              help="Made by an administrator under Profile, Create API token. Needed for the Things; the Items are readable without one unless anonymous access is off."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="things", label="Things", description="How many Things are online and which are offline, with openHAB's reason.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60,
                   options=(Field("only_problems", "Only the ones with a problem", type="bool", default=True),)),
        WidgetType(kind="items", label="Items", description="The Items you name with their state, and a switch for the Switch Items.",
                   renderer="list", default_size=(3, 3), refresh_seconds=15,
                   options=(
                       Field("items", "Items", type="text", required=True,
                             help="The names of Items or Groups, separated by commas. A Group stands for its members."),
                       Field("switching", "Offer the switch", type="bool", default=True,
                             help="Off makes the card read-only, which is what a card on a wall tablet usually wants."),
                   )),
    )

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if config.get("token"):
            headers["Authorization"] = f"Bearer {config['token']}"
        return headers

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None,
                   cache: float = READ_SECONDS) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/rest{path}", params=params, headers=self._headers(config),
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        self._refused(response.status_code, config)
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("openHAB did not answer with JSON.", code="not_json",
                               hint="The address of openHAB itself, usually port 8080, without /rest.") from failure

    @staticmethod
    def _refused(status: int, config: dict[str, Any]) -> None:
        if status in (401, 403):
            raise AuthFailed("openHAB refused the API token." if config.get("token") else "openHAB asks for an API token.",
                             hint="An administrator's API token from Profile, Create API token.")
        if status == 404:
            raise AdapterError("openHAB has no REST API at this address.", code="http_error",
                               hint="The address of openHAB itself, usually port 8080, without /rest.")
        if status >= 400:
            raise AdapterError(f"openHAB answered with HTTP {status}.", code="http_error")

    async def _items(self, config: dict[str, Any], ctx: Context, cache: float = READ_SECONDS) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/items", {"fields": ITEM_FIELDS}, cache)
        return [item for item in answer or [] if isinstance(item, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        root = await self._get(config, ctx, "/", cache=0) or {}
        version = str((root.get("runtimeInfo") or {}).get("version") or "")
        items = await self._items(config, ctx, cache=0)
        if config.get("token"):
            things = await self._get(config, ctx, "/things", {"summary": "true"}, cache=0) or []
            return f"openHAB {version} answers with {len(items)} Items and {len(things)} Things."
        return f"openHAB {version} answers with {len(items)} Items; the Things need an API token."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "items":
            return self._item_list(wanted_items(await self._items(config, ctx), str(options.get("items") or "")), options)
        if not config.get("token"):
            raise AuthFailed("openHAB shows its Things only with an API token.", hint="An administrator's API token from Profile, Create API token.")
        things = await self._get(config, ctx, "/things", {"summary": "true"}) or []
        return self._things([thing for thing in things if isinstance(thing, dict)], options)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id not in ("on", "off"):
            raise AdapterError("Unknown action.", code="no_such_action")
        name = str(params.get("item") or "")
        # Only a Switch Item this card lists, never a name the browser made up.
        listed = wanted_items(await self._items(config, ctx, cache=0), str(options.get("items") or ""))
        if not any(item.get("name") == name and item.get("type") == "Switch" for item in listed):
            raise AdapterError("This card has no such switch.", code="no_such_item")
        response = await ctx.request("POST", f"{base_url(config)}/rest/items/{quote(name, safe='')}", content=action_id.upper().encode(),
                                     headers={**self._headers(config), "Content-Type": "text/plain"},
                                     verify=not config.get("insecure"), auth_errors=False)
        self._refused(response.status_code, config)
        ctx.cache.clear()
        return f"{name} switched {action_id}."

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _things(things: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        rows = []
        online = offline = disabled = 0
        for thing in sorted(things, key=lambda one: str(one.get("label") or one.get("UID"))):
            info = thing.get("statusInfo") or {}
            status, detail = str(info.get("status") or "UNKNOWN"), str(info.get("statusDetail") or "NONE")
            if status == "ONLINE":
                online += 1
            elif detail == "DISABLED":
                disabled += 1
            else:
                offline += 1
            if options.get("only_problems", True) and (status == "ONLINE" or detail == "DISABLED"):
                continue
            reason = " · ".join(part for part in (detail.replace("_", " ").lower() if detail != "NONE" else "", str(info.get("description") or "")) if part)
            rows.append({
                "id": str(thing.get("UID")),
                "title": str(thing.get("label") or thing.get("UID")),
                "subtitle": " · ".join(part for part in ("disabled" if detail == "DISABLED" else status.lower(), reason if detail != "DISABLED" else "") if part),
                "status": "ok" if status == "ONLINE" else "unknown" if detail == "DISABLED" else "bad",
            })
        rows.sort(key=lambda row: {"bad": 0, "unknown": 2}.get(row["status"], 1))
        return WidgetData(
            status="bad" if offline else "ok",
            items=rows,
            secondary=[{"label": "Online", "value": online}, {"label": "Offline", "value": offline}],
            metrics={"offline": float(offline)},
            meta={"empty": f"All {online} Things online" if online else "No Thing set up"},
        )

    @staticmethod
    def _item_list(items: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        switching = bool(options.get("switching", True))
        rows = []
        for item in items:
            shown = shown_state(item)
            actions = []
            if switching and item.get("type") == "Switch" and item.get("state") in ("ON", "OFF"):
                on = item["state"] == "ON"
                actions = [Action(id="off" if on else "on", label="Switch off" if on else "Switch on", icon="power",
                                  params={"item": item["name"]}).model_dump()]
            rows.append({
                "id": str(item.get("name")),
                "title": str(item.get("label") or item.get("name")),
                "value": shown,
                "status": "unknown" if shown == "–" else "ok",
                "actions": actions,
            })
        return WidgetData(status="ok", items=rows, meta={"empty": "None of the Items named here exists"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "items":
            items = [
                {"name": "Living_Temperature", "label": "Temperature", "type": "Number:Temperature", "state": f"{fake.walk('oh-t', tick, 20.5, 22.4):.1f} °C"},
                {"name": "Kitchen_Light", "label": "Kitchen light", "type": "Switch", "state": "ON" if fake.flicker("oh-k", tick, 0.4) else "OFF"},
                {"name": "Front_Door", "label": "Front door", "type": "Contact", "state": "CLOSED"},
                {"name": "Grid_Power", "label": "Grid power", "type": "Number:Power", "state": f"{fake.walk('oh-p', tick, 180, 900):.0f} W"},
            ]
            return self._item_list(items, options)
        things = [
            {"UID": "hue:bridge:home", "label": "Hue bridge", "statusInfo": {"status": "ONLINE", "statusDetail": "NONE"}},
            {"UID": "zwave:serial_zstick:stick", "label": "Z-Wave stick", "statusInfo": {"status": "ONLINE", "statusDetail": "NONE"}},
            {"UID": "mqtt:topic:garage", "label": "Garage door", "statusInfo": {"status": "OFFLINE", "statusDetail": "COMMUNICATION_ERROR",
                                                                                "description": "No response from the device"}},
        ]
        return self._things(things, options)


ADAPTER = OpenHABAdapter()
