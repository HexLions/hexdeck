"""Kodi: what is playing, how far along, play and pause, and the library.

Kodi answers JSON-RPC on ``/jsonrpc`` when "Allow remote control via HTTP"
is on (Settings, Services, Control), with the web server's user name and
password as HTTP Basic. ``Player.GetActivePlayers`` says which player runs,
``Player.GetItem`` and ``Player.GetProperties`` what it plays and where it
is, ``Player.PlayPause`` and ``Player.Stop`` act on it; the library counts
come from ``limits.total`` of ``VideoLibrary.GetMovies``, ``GetTVShows``,
``GetEpisodes`` and ``AudioLibrary.GetAlbums``.

⚠️ A time is ``{"hours", "minutes", "seconds", "milliseconds"}``, never
seconds. ``speed`` 0 is paused.

⚠️ An error is a JSON-RPC ``error`` with a code, inside an HTTP 200; a wrong
password is HTTP 401.

Written from Kodi's own JSON-RPC schema (``methods.json`` and
``types.json``, API version 14.1.0). Not yet run against a Kodi.
"""

from __future__ import annotations

from typing import Any

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

NOW_SECONDS = 5
LIBRARY_SECONDS = 3600
ITEM_FIELDS = ["title", "showtitle", "season", "episode", "artist", "album", "year"]


def seconds_of(moment: Any) -> int:
    if not isinstance(moment, dict):
        return 0
    return int(moment.get("hours") or 0) * 3600 + int(moment.get("minutes") or 0) * 60 + int(moment.get("seconds") or 0)


def clock(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def title_of(item: dict[str, Any]) -> tuple[str, str]:
    """What is playing, and the line under it."""
    kind = item.get("type")
    title = str(item.get("title") or item.get("label") or "")
    if kind == "episode" and item.get("showtitle"):
        code = f"S{int(item.get('season') or 0):02d}E{int(item.get('episode') or 0):02d}"
        return str(item["showtitle"]), f"{code} · {title}" if title else code
    if kind == "song":
        artists = ", ".join(str(one) for one in item.get("artist") or [])
        return title, " · ".join(part for part in (artists, str(item.get("album") or "")) if part)
    if kind == "movie" and item.get("year"):
        return title, str(item["year"])
    return title or "Something", str(item.get("label") or "") if item.get("label") != title else ""


class KodiAdapter(Adapter):
    kind = "kodi"
    label = "Kodi"
    category = "media"
    description = "Kodi: what is playing and how far along, a button to pause or stop it, and how big the library is."
    icon = "kodi"
    docs_url = "https://github.com/xbmc/xbmc/tree/master/xbmc/interfaces/json-rpc/schema"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://kodi:8080",
              help="Kodi's web server. Turn on Settings, Services, Control, Allow remote control via HTTP."),
        Field("username", "User name", default="kodi"),
        Field("password", "Password", type="password", secret=True, help="The web server's password from the same page."),
    )
    widgets = (
        WidgetType(kind="now", label="Now playing", description="What plays and how far along it is, with buttons to pause and stop it.",
                   renderer="value", default_size=(3, 2), refresh_seconds=10,
                   options=(Field("controls", "Offer pause and stop", type="bool", default=True,
                                  help="Off makes the card read-only."),)),
        WidgetType(kind="library", label="Library", description="How many films, shows, episodes and albums the library holds.",
                   renderer="value", default_size=(3, 2), refresh_seconds=3600, metrics=("movies",)),
    )

    async def _rpc(self, config: dict[str, Any], ctx: Context, method: str, params: dict[str, Any] | None = None, cache: float = 0) -> Any:
        auth = (str(config.get("username") or "kodi"), str(config["password"])) if config.get("password") else None
        response = await ctx.request("POST", f"{base_url(config)}/jsonrpc", json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                                     auth=auth, cache_seconds=cache, auth_errors=False)
        if response.status_code == 401:
            raise AuthFailed("Kodi refused the user name or password." if auth else "Kodi asks for its web server password.",
                             hint="The user name and password under Settings, Services, Control.")
        if response.status_code >= 400:
            raise AdapterError(f"Kodi answered with HTTP {response.status_code}.", code="http_error",
                               hint="Turn on Allow remote control via HTTP, and use the web server's port, usually 8080.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("Kodi did not answer with JSON-RPC.", code="not_json", hint="Kodi's web server address, usually port 8080.") from failure
        if isinstance(answer, dict) and isinstance(answer.get("error"), dict):
            raise AdapterError(f"Kodi answered: {answer['error'].get('message') or answer['error'].get('code')}.", code="rpc_error")
        return answer.get("result") if isinstance(answer, dict) else None

    async def _active(self, config: dict[str, Any], ctx: Context, cache: float = NOW_SECONDS) -> dict[str, Any] | None:
        players = await self._rpc(config, ctx, "Player.GetActivePlayers", cache=cache) or []
        # Pictures are a slide show, not something that plays.
        return next((one for one in players if isinstance(one, dict) and one.get("type") in ("video", "audio")), None)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        application = await self._rpc(config, ctx, "Application.GetProperties", {"properties": ["name", "version"]}) or {}
        version = application.get("version") or {}
        number = f"{version.get('major')}.{version.get('minor')}" if version.get("major") is not None else ""
        return f"{application.get('name') or 'Kodi'} {number} answers.".replace("  ", " ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "library":
            counts = {}
            for key, method in (("movies", "VideoLibrary.GetMovies"), ("shows", "VideoLibrary.GetTVShows"),
                                ("episodes", "VideoLibrary.GetEpisodes"), ("albums", "AudioLibrary.GetAlbums")):
                result = await self._rpc(config, ctx, method, {"limits": {"start": 0, "end": 1}}, LIBRARY_SECONDS) or {}
                counts[key] = int((result.get("limits") or {}).get("total") or 0)
            return self._library(counts)
        player = await self._active(config, ctx)
        if player is None:
            return self._now(None, {}, {}, options)
        item = (await self._rpc(config, ctx, "Player.GetItem", {"playerid": player["playerid"], "properties": ITEM_FIELDS}, NOW_SECONDS) or {}).get("item") or {}
        where = await self._rpc(config, ctx, "Player.GetProperties", {"playerid": player["playerid"], "properties": ["speed", "time", "totaltime", "percentage"]},
                                NOW_SECONDS) or {}
        return self._now(player, item, where, options)

    async def action(self, widget_kind: str, action_id: str, params: dict[str, Any], config: dict[str, Any],
                     options: dict[str, Any], ctx: Context) -> str:
        if action_id not in ("pause", "play", "stop"):
            raise AdapterError("Unknown action.", code="no_such_action")
        player = await self._active(config, ctx, cache=0)
        if player is None:
            raise AdapterError("Nothing is playing any more.", code="nothing_playing")
        if action_id == "stop":
            await self._rpc(config, ctx, "Player.Stop", {"playerid": player["playerid"]})
        else:
            await self._rpc(config, ctx, "Player.PlayPause", {"playerid": player["playerid"], "play": action_id == "play"})
        ctx.cache.clear()
        return {"pause": "Paused.", "play": "Playing.", "stop": "Stopped."}[action_id]

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _now(player: dict[str, Any] | None, item: dict[str, Any], where: dict[str, Any], options: dict[str, Any]) -> WidgetData:
        if player is None:
            return WidgetData(status="ok", primary={"label": "Now playing", "value": "Nothing"}, meta={"idle": True})
        title, under = title_of(item)
        paused = int(where.get("speed") or 0) == 0
        done, total = seconds_of(where.get("time")), seconds_of(where.get("totaltime"))
        secondary: list[dict[str, Any]] = []
        if under:
            secondary.append({"label": "", "value": under})
        if total:
            secondary.append({"label": "Paused" if paused else "Playing", "value": f"{clock(done)} / {clock(total)}"})
        actions: list[Action] = []
        if options.get("controls", True):
            actions = [Action(id="play" if paused else "pause", label="Play" if paused else "Pause", icon="play" if paused else "pause"),
                       Action(id="stop", label="Stop", icon="square", confirm=True)]
        return WidgetData(
            status="ok",
            primary={"label": "Paused" if paused else "Now playing", "value": title},
            secondary=secondary,
            actions=actions,
            meta={"progress": round(float(where.get("percentage") or 0), 1)},
        )

    @staticmethod
    def _library(counts: dict[str, int]) -> WidgetData:
        return WidgetData(
            status="ok",
            primary={"label": "Movies", "value": counts["movies"], "metric": "movies"},
            secondary=[{"label": "Shows", "value": counts["shows"]}, {"label": "Episodes", "value": counts["episodes"]},
                       {"label": "Albums", "value": counts["albums"]}],
            metrics={"movies": float(counts["movies"])},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "library":
            return self._library({"movies": 412, "shows": 38, "episodes": 2_904, "albums": 186})
        if fake.flicker("kodi-idle", tick, 0.2):
            return self._now(None, {}, {}, options)
        done = (tick * 15) % 2_640
        return self._now({"playerid": 1, "type": "video"},
                         {"type": "episode", "showtitle": "Harbour Lights", "season": 3, "episode": 4, "title": "The Long Tide"},
                         {"speed": 1, "time": {"hours": 0, "minutes": done // 60, "seconds": done % 60},
                          "totaltime": {"hours": 0, "minutes": 44, "seconds": 0}, "percentage": done / 26.4}, options)


ADAPTER = KodiAdapter()
