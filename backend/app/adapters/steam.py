"""Steam: what an account has been playing, and whether it is playing now.

The Web API, with a key anybody can take out at steamcommunity.com/dev. Three
calls, all of them read-only and none of them able to do anything to the
account: who the player is, what they played in the last two weeks, and how
large the library is.

⚠️ Steam answers an empty list rather than an error when the profile's game
details are not public. That is the one thing that goes wrong here, and it
looks exactly like an account that owns nothing, so the cards say it in words.

⚠️ The rows carry no game artwork. Steam keeps it on its own CDN, and an
address in a card is fetched by the browser: every board showing the card
would tell Valve who is looking at it and when. The store link on each row
goes there only when somebody clicks it.

⚠️ The account is named either by its 64-bit id or by the custom name in its
profile address. A custom name has to be resolved first, which is one call
more; the answer is kept for a day, because that name changes about never.
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
    measured,
)

API = "https://api.steampowered.com"
#: Steam allows 100,000 calls a day per key; a card asking every five minutes
#: uses 864 of them, and the library changes far more slowly than that.
PLAYER_SECONDS = 300
LIBRARY_SECONDS = 3600
NAME_SECONDS = 86400
#: What the "personastate" numbers mean. 0 is offline, and in a game beats
#: every one of them, because that is what somebody wants to see.
STATES = {
    0: "Offline",
    1: "Online",
    2: "Busy",
    3: "Away",
    4: "Snoozing",
    5: "Looking to trade",
    6: "Looking to play",
}


def hours(minutes: Any) -> float:
    """Steam counts playtime in minutes; a card reads hours."""
    try:
        return round(float(minutes or 0) / 60.0, 1)
    except (TypeError, ValueError):
        return 0.0


class SteamAdapter(Adapter):
    kind = "steam"
    label = "Steam"
    category = "media"
    description = "What an account has been playing, what it is playing now, and how big the library is."
    icon = "steam"
    beta = True
    docs_url = "https://steamcommunity.com/dev"
    fields = (
        Field("api_key", "API key", type="password", secret=True, required=True,
              help="From steamcommunity.com/dev. It is read-only and belongs to the account that takes it out, not to the profile being watched."),
        Field("account", "Account", required=True, placeholder="76561197960287930",
              help="The 64-bit id, or the custom name from the profile address. Steam Profile > Edit Profile shows both."),
    )
    widgets = (
        WidgetType(kind="player", label="Player", description="Online, away or in a game, with the hours of the last two weeks and the size of the library.",
                   renderer="value", default_size=(3, 2), refresh_seconds=300, metrics=("hours",)),
        WidgetType(kind="recent", label="Recently played", description="The games of the last two weeks, with the hours in them.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300, metrics=("hours",),
                   options=(Field("limit", "Entries", type="number", default=8),)),
        WidgetType(kind="library", label="Library", description="How many games the account owns and how many it has ever started.",
                   renderer="value", default_size=(3, 2), refresh_seconds=3600),
    )

    async def _call(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any], cache: float) -> dict[str, Any]:
        key = str(config.get("api_key") or "").strip()
        if not key:
            raise AuthFailed("Steam needs an API key.")
        response = await ctx.request("GET", f"{API}{path}", params={**params, "key": key},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Steam refused the API key.")
        if response.status_code == 429:
            raise AdapterError("Steam is rate limiting this key.", code="rate_limited",
                               hint="A key allows 100,000 calls a day. Raise the refresh interval of the cards.")
        if response.status_code >= 400:
            raise AdapterError(f"Steam answered with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Steam did not answer with JSON.", code="not_json") from failure
        found = answer.get("response") if isinstance(answer, dict) else None
        if not isinstance(found, dict):
            raise AdapterError("This answer did not come from the Steam Web API.", code="bad_answer")
        return found

    async def _account(self, config: dict[str, Any], ctx: Context) -> str:
        """The 64-bit id, resolving a custom profile name if that is what was given."""
        written = str(config.get("account") or "").strip().strip("/")
        if not written:
            raise AdapterError("This connection names no account.", code="no_account")
        # Somebody may paste the whole profile address.
        if "/" in written:
            written = written.rsplit("/", 1)[-1]
        if written.isdigit() and len(written) >= 17:
            return written
        answer = await self._call(config, ctx, "/ISteamUser/ResolveVanityURL/v1/", {"vanityurl": written}, NAME_SECONDS)
        if int(answer.get("success") or 0) != 1 or not answer.get("steamid"):
            raise AdapterError(f"Steam knows no account called {written!r}.", code="no_such_account",
                               hint="Use the 64-bit id instead; a profile without a custom address has only that.")
        return str(answer["steamid"])

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        account = await self._account(config, ctx)
        answer = await self._call(config, ctx, "/ISteamUser/GetPlayerSummaries/v2/", {"steamids": account}, 0)
        players = [one for one in answer.get("players") or [] if isinstance(one, dict)]
        if not players:
            raise AdapterError("Steam answered about no player at all.", code="no_such_account",
                               hint="An account that does not exist and a profile that is private both answer this way.")
        return f"Steam answers about {players[0].get('personaname') or account}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        account = await self._account(config, ctx)
        if widget_kind == "library":
            return self._library(await self._owned(config, ctx, account))
        recent = await self._recent(config, ctx, account)
        if widget_kind == "recent":
            return self._recent_card(recent, options)
        summary = await self._call(config, ctx, "/ISteamUser/GetPlayerSummaries/v2/", {"steamids": account}, PLAYER_SECONDS)
        players = [one for one in summary.get("players") or [] if isinstance(one, dict)]
        return self._player(players[0] if players else {}, recent)

    async def _recent(self, config: dict[str, Any], ctx: Context, account: str) -> dict[str, Any]:
        return await self._call(config, ctx, "/IPlayerService/GetRecentlyPlayedGames/v1/", {"steamid": account}, PLAYER_SECONDS)

    async def _owned(self, config: dict[str, Any], ctx: Context, account: str) -> dict[str, Any]:
        return await self._call(config, ctx, "/IPlayerService/GetOwnedGames/v1/",
                                {"steamid": account, "include_appinfo": 1, "include_played_free_games": 1}, LIBRARY_SECONDS)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _games(answer: dict[str, Any]) -> list[dict[str, Any]]:
        return [one for one in answer.get("games") or [] if isinstance(one, dict)]

    @staticmethod
    def _private() -> str:
        return "Steam answers nothing here while the profile's game details are private."

    @classmethod
    def _player(cls, player: dict[str, Any], recent: dict[str, Any]) -> WidgetData:
        games = cls._games(recent)
        fortnight = round(sum(hours(one.get("playtime_2weeks")) for one in games), 1)
        playing = str(player.get("gameextrainfo") or "")
        state = int(player.get("personastate") or 0)
        secondary: list[dict[str, Any]] = []
        if playing:
            secondary.append({"label": "Playing", "value": playing})
        secondary.append({"label": "Two weeks", "value": fortnight, "unit": "h", "metric": "hours"})
        secondary.append({"label": "Games", "value": len(games)})
        return WidgetData(
            # Green is "in a game", because that is the only state worth a colour;
            # offline is a fact, not a fault.
            status="ok" if playing or state else "unknown",
            primary={"label": "State", "value": "In a game" if playing else STATES.get(state, "Online")},
            secondary=secondary,
            metrics=measured({"hours": fortnight}),
            meta={"name": str(player.get("personaname") or ""), "playing": playing,
                  "empty": cls._private() if not player else ""},
        )

    @classmethod
    def _recent_card(cls, recent: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        games = sorted(cls._games(recent), key=lambda one: -hours(one.get("playtime_2weeks")))
        items = [{
            "title": str(one.get("name") or one.get("appid") or "?"),
            "subtitle": f"{hours(one.get('playtime_forever'))} h in total",
            "value": f"{hours(one.get('playtime_2weeks'))} h",
            "status": "ok",
            "url": f"https://store.steampowered.com/app/{one.get('appid')}" if one.get("appid") else "",
        } for one in games]
        fortnight = round(sum(hours(one.get("playtime_2weeks")) for one in games), 1)
        return WidgetData(
            status="ok",
            items=items[: max(1, int(options.get("limit") or 8))],
            primary={"label": "Two weeks", "value": fortnight, "unit": "h"},
            metrics=measured({"hours": fortnight}),
            # ⚠️ Steam counts only the last two weeks here, so an account that
            # has not played in a fortnight is empty rather than broken.
            meta={"empty": "Nothing played in the last two weeks, or the profile keeps its games private."},
        )

    @classmethod
    def _library(cls, owned: dict[str, Any]) -> WidgetData:
        games = cls._games(owned)
        played = [one for one in games if hours(one.get("playtime_forever"))]
        total = round(sum(hours(one.get("playtime_forever")) for one in games), 1)
        count = int(owned.get("game_count") or len(games))
        return WidgetData(
            status="ok" if games else "unknown",
            primary={"label": "Games", "value": count},
            secondary=[
                {"label": "Started", "value": f"{len(played)} of {count}"},
                {"label": "Hours", "value": total},
            ],
            meta={"empty": cls._private() if not games else ""},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        recent = {"games": [
            {"appid": 1086940, "name": "Baldur's Gate 3", "playtime_2weeks": int(fake.walk("steam-bg3", tick, 60, 420)), "playtime_forever": 9820, "img_icon_url": "a"},
            {"appid": 620, "name": "Portal 2", "playtime_2weeks": 95, "playtime_forever": 1480, "img_icon_url": "b"},
            {"appid": 105600, "name": "Terraria", "playtime_2weeks": 40, "playtime_forever": 5300, "img_icon_url": "c"},
        ]}
        if widget_kind == "recent":
            return self._recent_card(recent, options)
        if widget_kind == "library":
            return self._library({"game_count": 312, "games": [
                {"playtime_forever": 9820}, {"playtime_forever": 1480}, {"playtime_forever": 0},
            ]})
        playing = fake.flicker("steam-playing", tick, 0.5)
        return self._player({"personaname": "hexlions", "personastate": 1,
                             "gameextrainfo": "Baldur's Gate 3" if playing else ""}, recent)


ADAPTER = SteamAdapter()
