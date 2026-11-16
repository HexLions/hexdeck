"""HDHomeRun: which tuners are busy, on what and with what signal, and the channels it receives.

Written from SiliconDust's HTTP development guide (``info.hdhomerun.com/info/http_api``, last changed 14.07.2026) for
``/discover.json`` and ``/lineup.json``. Not yet run against a tuner.

- ``/discover.json`` names the unit: ``FriendlyName``, ``ModelNumber``, ``FirmwareVersion``, ``DeviceID`` and
  ``TunerCount``. It also hands out ``DeviceAuth``, a credential for SiliconDust's DVR service, which is never read.
- ``/lineup.json`` lists every channel with ``GuideNumber``, ``GuideName`` and ``URL``; ``Tags`` is comma-separated
  (``favorite``, ``drm``) and ``HD`` is 1 for an HD channel.
- The units ask for no password on the local network, so the connection has none.

⚠️ ``/status.json``, one entry per tuner, is not in the guide. Its shape (``VctNumber`` and ``VctName`` while a tuner
is in use, ``SignalStrengthPercent``, ``SignalQualityPercent``, ``SymbolQualityPercent``, ``TargetIP``) is taken from
the HDHomeRun widget of Homepage. A unit that does not answer it leaves the tuner card saying so; the channel card does
not need it.

⚠️ Never a URL of ``lineup.json`` is fetched: each one tunes a channel and holds a tuner while it is open.
"""

from __future__ import annotations

from typing import Any

from . import demo as fake
from .base import Adapter, AdapterError, Context, Field, WidgetData, WidgetType, base_url


class HdhomerunAdapter(Adapter):
    kind = "hdhomerun"
    label = "HDHomeRun"
    category = "media"
    description = "The tuners of an HDHomeRun, what each is tuned to with its signal, and the channels it receives."
    icon = "hdhomerun"
    docs_url = "https://info.hdhomerun.com/info/http_api"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://hdhomerun.local",
              help="The address of the tuner, or its device ID followed by .local."),
    )
    widgets = (
        WidgetType(
            kind="tuners",
            label="Tuners",
            description="How many tuners are in use, and for each the channel and the signal. Yellow when every tuner is busy.",
            renderer="list",
            default_size=(3, 3),
            refresh_seconds=30,
            metrics=("in_use",),
        ),
        WidgetType(
            kind="channels",
            label="Channels",
            description="How many channels the tuner receives, how many in HD, marked as favourites and protected against copying.",
            renderer="value",
            default_size=(2, 2),
            min_size=(1, 1),
            refresh_seconds=3600,
        ),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float) -> Any:
        return await ctx.get_json(f"{base_url(config)}/{path}", cache_seconds=cache)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        unit = await self._get(config, ctx, "discover.json", cache=0)
        if not isinstance(unit, dict) or "TunerCount" not in unit:
            raise AdapterError("The address did not answer like an HDHomeRun.", code="not_json",
                               hint="Use the address of the tuner itself, such as http://hdhomerun.local.")
        name = unit.get("FriendlyName") or unit.get("ModelNumber") or "HDHomeRun"
        return f"{name} {unit.get('ModelNumber') or ''}".strip() + f", firmware {unit.get('FirmwareVersion') or '?'}, {unit.get('TunerCount')} tuners."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "channels":
            lineup = await self._get(config, ctx, "lineup.json", cache=3600)
            return self._channels(lineup if isinstance(lineup, list) else [])
        try:
            tuners = await self._get(config, ctx, "status.json", cache=20)
        except AdapterError as failure:
            if failure.code != "http_error":
                raise
            raise AdapterError("This unit does not say what its tuners are doing.", code="no_status",
                               hint="Older firmware has no status.json. The channel card works without it.") from failure
        return self._tuners(tuners if isinstance(tuners, list) else [])

    @staticmethod
    def _tuners(tuners: list[Any]) -> WidgetData:
        rows = [one for one in tuners if isinstance(one, dict)]
        items = []
        busy = 0
        for number, tuner in enumerate(rows, start=1):
            name = f"Tuner {number}"
            if tuner.get("VctNumber") is None:
                items.append({"title": name, "subtitle": "Free", "status": "ok"})
                continue
            busy += 1
            channel = f"{tuner.get('VctNumber')} {tuner.get('VctName') or ''}".strip()
            strength, quality = tuner.get("SignalStrengthPercent"), tuner.get("SignalQualityPercent")
            weak = isinstance(quality, (int, float)) and quality < 60
            items.append({
                "title": name,
                "subtitle": channel,
                "value": f"{strength}%" if isinstance(strength, (int, float)) else "",
                "status": "warn" if weak else "ok",
            })
        return WidgetData(
            status="warn" if rows and busy == len(rows) else "ok",
            items=items,
            secondary=[{"label": "In use", "value": f"{busy} / {len(rows)}"}],
            metrics={"in_use": float(busy)},
        )

    @staticmethod
    def _channels(lineup: list[Any]) -> WidgetData:
        channels = [one for one in lineup if isinstance(one, dict)]

        def tagged(channel: dict[str, Any], tag: str) -> bool:
            return tag in [part.strip() for part in str(channel.get("Tags") or "").split(",")]

        return WidgetData(
            primary={"label": "Channels", "value": len(channels)},
            secondary=[
                {"label": "HD", "value": sum(1 for channel in channels if channel.get("HD") == 1)},
                {"label": "Favourites", "value": sum(1 for channel in channels if tagged(channel, "favorite"))},
                {"label": "Protected", "value": sum(1 for channel in channels if tagged(channel, "drm"))},
            ],
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        if widget_kind == "channels":
            return self._channels([{"GuideNumber": str(n), "HD": 1 if n % 3 else 0, "Tags": "favorite" if n < 6 else ""} for n in range(1, 58)])
        recording = fake.flicker("hdhomerun-second", tick, 0.5)
        strength = int(fake.walk("hdhomerun-signal", tick, 78, 94))
        tuners = [
            {"VctNumber": "4.1", "VctName": "Channel Four", "SignalStrengthPercent": strength, "SignalQualityPercent": 96},
            {"VctNumber": "7.1", "VctName": "Harbour News", "SignalStrengthPercent": 71, "SignalQualityPercent": 88} if recording else {},
            {}, {},
        ]
        return self._tuners(tuners)


ADAPTER = HdhomerunAdapter()
