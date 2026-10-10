"""MySpeed: the last speed test, how today went, and the tests before.

MySpeed's API, read-only, with its password in a ``password`` header when
one is set: ``/api/speedtests`` for the tests, ``/api/speedtests/statistics``
for the day's figures, ``/api/speedtests/status`` for whether tests are
paused, ``/api/config`` for the speeds MySpeed is told to expect.

⚠️ The password goes in a header called ``password``, as it is. With
MySpeed's "read" level set, reading needs no password at all.

⚠️ Speeds are in Mbit/s and the ping in ms, as MySpeed shows them. A failed
test is kept with -1 everywhere and its reason in ``error``.

⚠️ The expected speeds (``ping``, ``download``, ``upload`` in the config,
25, 100 and 50 unless changed) are what MySpeed colours its own gauges by;
the card turns amber the same way when the last test falls short.

Checked against MySpeed 1.0.9 running locally on 2026-10-10, with real tests
over LibreSpeed, one test failed over Cloudflare, with no password, with a
password and with the "read" level.
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
    ago,
    base_url,
    measured,
)

TESTS_SECONDS = 300


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _failed(test: dict[str, Any]) -> bool:
    return bool(test.get("error")) or (_number(test.get("download")) or 0) < 0


class MySpeedAdapter(Adapter):
    kind = "myspeed"
    label = "MySpeed"
    category = "network"
    description = "MySpeed's speed tests: the last download, upload and ping against what you expect, how many failed today, and the tests before."
    icon = "myspeed"
    docs_url = "https://docs.myspeed.dev/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://myspeed:5216"),
        Field("password", "Password", type="password", secret=True,
              help="MySpeed's password, if one is set. Not needed when its password level lets everyone read."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="latest", label="Last test", description="The last download, upload and ping, against the speeds MySpeed expects, and the tests that failed today.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("download", "upload", "ping")),
        WidgetType(kind="tests", label="Tests", description="The latest speed tests with their results, and the reason of those that failed.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float, params: dict[str, Any] | None = None) -> Any:
        headers = {"Accept": "application/json"}
        if config.get("password"):
            headers["password"] = str(config["password"])
        response = await ctx.request("GET", f"{base_url(config)}/api{path}", verify=not config.get("insecure"), params=params,
                                     headers=headers, cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("MySpeed refused the password." if config.get("password") else "MySpeed asks for its password.",
                             hint="The password set in MySpeed's settings.")
        if response.status_code >= 400:
            raise AdapterError(f"MySpeed answered with HTTP {response.status_code}.", code="http_error",
                               hint="The address of MySpeed itself, usually port 5216.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("MySpeed did not answer with JSON.", code="not_json",
                               hint="The address of MySpeed itself, usually port 5216.") from failure

    async def _tests(self, config: dict[str, Any], ctx: Context, limit: int) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/speedtests", TESTS_SECONDS, {"hours": 168, "limit": limit})
        return [one for one in (answer or []) if isinstance(one, dict)]

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        tests = await self._tests(config, ctx, 1)
        if not tests:
            return "MySpeed answers, with no test in the last week."
        return f"MySpeed answers; the last test was {ago(tests[0].get('created')) or 'just now'} ago."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "tests":
            return self._list(await self._tests(config, ctx, max(1, int(options.get("limit") or 8))), max(1, int(options.get("limit") or 8)))
        tests = await self._tests(config, ctx, 10)
        statistics = await self._get(config, ctx, "/speedtests/statistics", TESTS_SECONDS, {"days": 1}) or {}
        expected = await self._get(config, ctx, "/config", 3600) or {}
        status = await self._get(config, ctx, "/speedtests/status", 60) or {}
        return self._latest(tests, statistics, expected, status)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _latest(tests: list[dict[str, Any]], statistics: dict[str, Any], expected: dict[str, Any], status: dict[str, Any]) -> WidgetData:
        failed_today = int((statistics.get("tests") or {}).get("failed") or 0)
        last = tests[0] if tests else None
        good = next((one for one in tests if not _failed(one)), None)
        notice = "Speed tests are paused." if status.get("paused") else ""
        if good is None:
            return WidgetData(status="bad" if last else "unknown", primary={"label": "Download", "value": "–"},
                              secondary=[{"label": "Failed today", "value": failed_today}],
                              meta={"notice": notice or (f"The last test failed: {last.get('error')}" if last else "No test in the last week.")})
        down, up, ping = (_number(good.get(key)) for key in ("download", "upload", "ping"))
        short = any(value is not None and floor is not None and (value > floor if key == "ping" else value < floor)
                    for key, value, floor in (("download", down, _number(expected.get("download"))),
                                              ("upload", up, _number(expected.get("upload"))),
                                              ("ping", ping, _number(expected.get("ping")))))
        if last is not None and last is not good:
            notice = notice or f"The last test failed: {last.get('error') or 'no reason given'}"
        secondary: list[dict[str, Any]] = [
            {"label": "Upload", "value": f"{up:g} Mbit/s" if up is not None else "–", "metric": "upload"},
            {"label": "Ping", "value": f"{ping:g} ms" if ping is not None else "–", "metric": "ping"},
            {"label": "Failed today", "value": failed_today},
        ]
        return WidgetData(
            status="bad" if last is not good else "warn" if short or failed_today else "ok",
            primary={"label": "Download", "value": f"{down:g} Mbit/s" if down is not None else "–", "metric": "download"},
            secondary=secondary,
            metrics=measured({"download": down, "upload": up, "ping": ping}),
            meta={"notice": notice, "since": ago(good.get("created"))},
        )

    @staticmethod
    def _list(tests: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        for test in tests:
            when = ago(test.get("created"))
            if _failed(test):
                rows.append({"id": test.get("id"), "title": "failed", "subtitle": " · ".join(part for part in (when, str(test.get("error") or "")) if part),
                             "value": "", "status": "bad"})
                continue
            rows.append({
                "id": test.get("id"),
                "title": f"↓ {_number(test.get('download')) or 0:g}  ↑ {_number(test.get('upload')) or 0:g} Mbit/s",
                "subtitle": " · ".join(part for part in (when, str(test.get("type") or "")) if part),
                "value": f"{_number(test.get('ping')) or 0:g} ms",
                "status": "ok",
            })
        return WidgetData(status="warn" if any(row["status"] == "bad" for row in rows) else "ok", items=rows[:limit],
                          meta={"empty": "No test in the last week"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        down = round(fake.walk("myspeed-down", tick, 820, 940), 2)
        up = round(fake.walk("myspeed-up", tick, 190, 240), 2)
        tests = [{"id": 30 - index, "download": round(down - index * 7.3, 2), "upload": round(up - index * 2.1, 2), "ping": 14 + index % 3,
                  "type": "auto", "created": ""} for index in range(6)]
        tests.insert(3, {"id": 99, "download": -1, "upload": -1, "ping": -1, "error": "Network unreachable", "type": "auto", "created": ""})
        if widget_kind == "tests":
            return self._list(tests, max(1, int(options.get("limit") or 8)))
        return self._latest(tests, {"tests": {"total": 24, "failed": 1}}, {"download": "500", "upload": "100", "ping": "25"}, {"paused": False})


ADAPTER = MySpeedAdapter()
