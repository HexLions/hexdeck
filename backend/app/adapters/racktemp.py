"""RackTemp: the temperature and humidity of every rack sensor, and which ones stopped reporting.

Checked against RackTemp 1.0.7 (HexLions/RackTemp, ``backend/src/routes/metrics.ts`` and ``version.ts``) running locally
on 16.11.2026, with three sensors: one over the upper limit set for it, one in range, and one that never reported.

- ``GET /metrics`` is Prometheus text and needs no sign-in, like node_exporter: RackTemp's own advice is to keep it on
  the LAN or behind a reverse proxy. Every sample carries ``sensor_id``, ``sensor`` and ``location``.
  ``rack_temp_celsius`` and ``rack_temp_humidity_percent`` are the latest reading, ``rack_temp_last_seen_seconds`` its
  age, ``rack_temp_online`` 1 or 0 against the sensor's own offline limit, and ``rack_temp_threshold_min_celsius`` /
  ``_max_celsius`` the limits set for it, each only when there is one. A sensor that never reported has
  ``rack_temp_online 0`` and nothing else.
- ``GET /api/version`` answers ``{"version", "commit"}`` without a sign-in.
- RackTemp serves HTTPS only, with a certificate it makes itself on first start; hence "Ignore TLS errors" is on.

⚠️ ``/api/status/<sensor>`` would say the same for one sensor, but wants the sensor's key in the address. This adapter
never uses it: no key leaves for anything RackTemp offers without one.
"""

from __future__ import annotations

import re
from typing import Any

from . import demo as fake
from .base import (
    Adapter,
    AdapterError,
    Context,
    Field,
    WidgetData,
    WidgetType,
    base_url,
    duration_short,
)

_LINE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)$")
# Values may hold an escaped quote (\"), which RackTemp writes for a name with one in it.
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_metrics(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Every sample of a Prometheus text page as (name, labels, value)."""
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        found = _LINE.match(line.strip())
        if not found:
            continue
        try:
            value = float(found.group("value"))
        except ValueError:
            continue
        labels = {key: raw.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
                  for key, raw in _LABEL.findall(found.group("labels") or "")}
        samples.append((found.group("name"), labels, value))
    return samples


def sensors_of(text: str) -> list[dict[str, Any]]:
    """One entry per sensor, in the order RackTemp lists them."""
    found: dict[str, dict[str, Any]] = {}
    names = {
        "rack_temp_celsius": "temperature", "rack_temp_humidity_percent": "humidity",
        "rack_temp_last_seen_seconds": "age", "rack_temp_online": "online",
        "rack_temp_threshold_min_celsius": "minimum", "rack_temp_threshold_max_celsius": "maximum",
    }
    for metric, labels, value in parse_metrics(text):
        if metric not in names or not labels.get("sensor_id"):
            continue
        sensor = found.setdefault(labels["sensor_id"], {"id": labels["sensor_id"], "name": labels.get("sensor") or "?",
                                                       "location": labels.get("location") or ""})
        sensor[names[metric]] = value
    return list(found.values())


def judged(sensor: dict[str, Any]) -> tuple[str, str]:
    """``ok``, ``bad`` or ``unknown``, and why."""
    if sensor.get("online") != 1:
        return ("bad", "No reading yet") if "temperature" not in sensor else ("bad", "Offline")
    temperature = sensor.get("temperature")
    if temperature is None:
        return "unknown", ""
    if sensor.get("maximum") is not None and temperature > sensor["maximum"]:
        return "bad", f"Above {sensor['maximum']:g} °C"
    if sensor.get("minimum") is not None and temperature < sensor["minimum"]:
        return "bad", f"Below {sensor['minimum']:g} °C"
    return "ok", ""


class RacktempAdapter(Adapter):
    kind = "racktemp"
    label = "RackTemp"
    category = "hosts"
    description = "The temperature and humidity of every RackTemp sensor, against the limits set for it."
    icon = "lucide:thermometer"
    beta = True
    docs_url = "https://github.com/HexLions/RackTemp"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://racktemp.local:7431",
              help="The address of the RackTemp server, with https and its port."),
        Field("insecure", "Ignore TLS errors", type="bool", default=True,
              help="RackTemp makes its own certificate on first start."),
    )
    widgets = (
        WidgetType(
            kind="sensors",
            label="Sensors",
            description="Every sensor with its temperature and humidity. Red when one is outside its limits or stopped reporting.",
            renderer="list",
            default_size=(3, 3),
            refresh_seconds=60,
        ),
        WidgetType(
            kind="sensor",
            label="Sensor",
            description="One sensor: temperature, humidity, its limits and how old the reading is.",
            renderer="value",
            default_size=(2, 2),
            min_size=(1, 1),
            refresh_seconds=60,
            metrics=("temperature", "humidity"),
            options=(Field("sensor", "Sensor", type="choices", required=True),),
        ),
    )

    async def _sensors(self, config: dict[str, Any], ctx: Context, cache: float = 30) -> list[dict[str, Any]]:
        response = await ctx.request("GET", f"{base_url(config)}/metrics", verify=not config.get("insecure", True),
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code >= 400:
            raise AdapterError(f"RackTemp answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it should be the address of the RackTemp server itself.")
        if "rack_temp_" not in response.text:
            raise AdapterError("The address did not answer like RackTemp.", code="not_racktemp",
                               hint="Check the URL; it should be the address of the RackTemp server itself.")
        return sensors_of(response.text)

    async def choices(self, field: str, config: dict[str, Any], ctx: Context) -> list[tuple[str, str]]:
        if field != "sensor":
            return []
        return [(one["id"], f"{one['name']} ({one['location']})" if one["location"] else one["name"])
                for one in await self._sensors(config, ctx)]

    def demo_choices(self, field: str) -> list[tuple[str, str]]:
        return [("rack-top", "Rack top (Server cabinet)"), ("closet", "Closet (Hall)")] if field == "sensor" else []

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        sensors = await self._sensors(config, ctx, cache=0)
        version = await ctx.get_json(f"{base_url(config)}/api/version", verify=not config.get("insecure", True), cache_seconds=0)
        number = version.get("version") if isinstance(version, dict) else None
        return f"RackTemp {number or '?'} answers with {len(sensors)} sensors."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        sensors = await self._sensors(config, ctx)
        if widget_kind == "sensor":
            wanted = str(options.get("sensor") or "")
            sensor = next((one for one in sensors if one["id"] == wanted), None)
            if sensor is None:
                raise AdapterError("RackTemp has no such sensor.", code="no_such_sensor",
                                   hint="Pick the sensor again in the card's settings.")
            return self._one(sensor)
        return self._list(sensors)

    @staticmethod
    def _reading(sensor: dict[str, Any]) -> str:
        parts = []
        if sensor.get("temperature") is not None:
            parts.append(f"{sensor['temperature']:.1f} °C")
        if sensor.get("humidity") is not None:
            parts.append(f"{sensor['humidity']:.0f} %")
        return " · ".join(parts)

    @staticmethod
    def _list(sensors: list[dict[str, Any]]) -> WidgetData:
        items = []
        for sensor in sensors:
            status, why = judged(sensor)
            items.append({
                "title": sensor["name"],
                "subtitle": " · ".join(part for part in (sensor["location"], why) if part),
                "value": RacktempAdapter._reading(sensor),
                "status": status,
            })
        bad = sum(1 for item in items if item["status"] == "bad")
        return WidgetData(status="bad" if bad else ("ok" if items else "unknown"), items=items,
                          secondary=[{"label": "Sensors", "value": len(items)}, {"label": "Problems", "value": bad}])

    @staticmethod
    def _one(sensor: dict[str, Any]) -> WidgetData:
        status, why = judged(sensor)
        temperature, humidity = sensor.get("temperature"), sensor.get("humidity")
        secondary: list[dict[str, Any]] = []
        if humidity is not None:
            secondary.append({"label": "Humidity", "value": round(humidity), "unit": "%"})
        limits = [f"{sensor[key]:g}" for key in ("minimum", "maximum") if sensor.get(key) is not None]
        if len(limits) == 2:
            secondary.append({"label": "Limits", "value": f"{limits[0]} to {limits[1]}", "unit": "°C"})
        if sensor.get("age") is not None:
            secondary.append({"label": "Last reading", "value": duration_short(sensor["age"])})
        if sensor["location"]:
            secondary.append({"label": "Location", "value": sensor["location"]})
        meta = {"status_reason": why} if why else {}
        metrics = {name: value for name, value in (("temperature", temperature), ("humidity", humidity)) if value is not None}
        return WidgetData(status=status,
                          primary={"label": sensor["name"], "value": round(temperature, 1) if temperature is not None else "?",
                                   "unit": "°C" if temperature is not None else ""},
                          secondary=secondary, meta=meta, metrics=metrics)

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        sensors = [
            {"id": "rack-top", "name": "Rack top", "location": "Server cabinet", "online": 1, "age": 12,
             "temperature": round(fake.walk("racktemp-top", tick, 27.5, 31.0), 1), "humidity": 38.0, "minimum": 15.0, "maximum": 30.0},
            {"id": "closet", "name": "Closet", "location": "Hall", "online": 1, "age": 25,
             "temperature": round(fake.walk("racktemp-closet", tick, 21.0, 23.5), 1), "humidity": 55.0},
        ]
        if widget_kind == "sensor":
            return self._one(next((one for one in sensors if one["id"] == options.get("sensor")), sensors[0]))
        return self._list(sensors)


ADAPTER = RacktempAdapter()
