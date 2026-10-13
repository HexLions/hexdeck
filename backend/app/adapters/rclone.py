"""rclone: what the remote control daemon is copying, how fast, and what failed.

``rclone rcd`` (or any rclone started with ``--rc``) answers its remote
control API with POST requests: ``core/stats`` gives the speed, the bytes
and transfers so far, the errors with the last one, and the files being
copied now; ``core/version`` the version.

⚠️ Every call is a POST. With ``Content-Type: application/json`` rclone
wants a body, ``{}`` at least; an empty one is answered with 400 and
"failed to read input JSON: EOF".

⚠️ Sign-in is ``--rc-user`` and ``--rc-pass`` as HTTP Basic; a daemon
started with ``--rc-no-auth`` needs none for these calls.

⚠️ The counters run from the daemon's start (or the last ``core/stats-reset``):
an error stays counted until then. The card turns amber for errors and
red only for a fatal one, and shows the last error.

⚠️ Since 1.75 every remote control call becomes a job, these too, so the job
list is mostly the dashboard asking; the card does not read it.

Checked against rclone v1.75.2 running locally on 2026-10-13 as ``rcd``
with a user and password: a throttled copy of two large files, a copy of a
file that does not exist, one into a read-only folder, and a wrong
password.
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
    duration_short,
    human_bytes,
    human_rate,
)

STATS_SECONDS = 10


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class RcloneAdapter(Adapter):
    kind = "rclone"
    label = "rclone"
    category = "hosts"
    description = "rclone's remote control: what it is copying now, how fast, how far along, and the last error."
    icon = "rclone"
    docs_url = "https://rclone.org/rc/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://rclone:5572",
              help="The address of rclone rcd, or of rclone started with --rc. It listens on localhost:5572 unless --rc-addr says otherwise."),
        Field("username", "User name", help="The --rc-user rclone was started with."),
        Field("password", "Password", type="password", secret=True, help="The --rc-pass rclone was started with."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="status", label="Status", description="The speed now, the files being copied, how far along the whole run is, and the errors.",
                   renderer="value", default_size=(3, 2), refresh_seconds=15, metrics=("speed",)),
        WidgetType(kind="transfers", label="Transfers", description="Each file being copied now, with its progress, speed and time left.",
                   renderer="list", default_size=(3, 3), refresh_seconds=10),
    )

    async def _call(self, config: dict[str, Any], ctx: Context, command: str, cache: float) -> dict[str, Any]:
        auth = (str(config["username"]), str(config.get("password") or "")) if config.get("username") else None
        response = await ctx.request("POST", f"{base_url(config)}/{command}", json_body={}, auth=auth,
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("rclone refused the user name or password." if auth else "rclone asks for a user name and password.",
                             hint="The --rc-user and --rc-pass rclone was started with.")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError(f"rclone answered with HTTP {response.status_code} and no JSON.", code="not_json",
                               hint="The address of rclone's remote control, usually port 5572.") from failure
        if response.status_code >= 400 or not isinstance(answer, dict):
            message = answer.get("error") if isinstance(answer, dict) else ""
            raise AdapterError(f"rclone answered: {message or f'HTTP {response.status_code}'}.", code="http_error",
                               hint="The address of rclone's remote control, usually port 5572.")
        return answer

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        version = await self._call(config, ctx, "core/version", 0)
        stats = await self._call(config, ctx, "core/stats", 0)
        running = len(stats.get("transferring") or [])
        return f"rclone {version.get('version') or ''} answers, {running} file{'s' if running != 1 else ''} being copied.".replace("rclone  ", "rclone ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        stats = await self._call(config, ctx, "core/stats", STATS_SECONDS)
        return self._transfers(stats) if widget_kind == "transfers" else self._status(stats)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _status(stats: dict[str, Any]) -> WidgetData:
        speed = _number(stats.get("speed"))
        moving = [one for one in stats.get("transferring") or [] if isinstance(one, dict)]
        done, total = _number(stats.get("bytes")), _number(stats.get("totalBytes"))
        errors = int(_number(stats.get("errors")))
        secondary: list[dict[str, Any]] = [{"label": "Copying", "value": len(moving)}]
        if total:
            secondary.append({"label": "Done", "value": f"{human_bytes(done)} of {human_bytes(total)}"})
        if moving and stats.get("eta"):
            secondary.append({"label": "Left", "value": duration_short(_number(stats["eta"]))})
        secondary.append({"label": "Errors", "value": errors})
        return WidgetData(
            status="bad" if stats.get("fatalError") else "warn" if errors else "ok",
            primary={"label": "Speed", "value": human_rate(speed) if moving else "idle", "metric": "speed"},
            secondary=secondary,
            metrics={"speed": round(speed / 1024 / 1024, 2)},
            meta={"notice": str(stats.get("lastError") or "")[:200] if errors else ""},
        )

    @staticmethod
    def _transfers(stats: dict[str, Any]) -> WidgetData:
        rows = []
        for one in sorted((one for one in stats.get("transferring") or [] if isinstance(one, dict)), key=lambda one: str(one.get("name"))):
            percentage = _number(one.get("percentage"))
            parts = [human_bytes(_number(one.get("size"))) if one.get("size") else "",
                     human_rate(_number(one.get("speed"))) if one.get("speed") else "",
                     f"{duration_short(_number(one['eta']))} left" if one.get("eta") else "",
                     str(one.get("group") or "") if not str(one.get("group") or "").startswith("job/") else ""]
            rows.append({
                "id": f"{one.get('group')}:{one.get('name')}",
                "title": str(one.get("name") or "?"),
                "subtitle": " · ".join(part for part in parts if part),
                "progress": percentage,
                "value": f"{percentage:.0f}%",
                "status": "ok",
            })
        errors = int(_number(stats.get("errors")))
        return WidgetData(status="warn" if errors else "ok", items=rows,
                          meta={"empty": "Nothing being copied", "notice": str(stats.get("lastError") or "")[:200] if errors else ""})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        progress = (tick * 0.7) % 100
        speed = fake.walk("rclone-speed", tick, 8e6, 24e6)
        stats = {"speed": speed, "bytes": 41e9 * progress / 100, "totalBytes": 41e9, "eta": int((100 - progress) * 30), "errors": 0,
                 "transferring": [
                     {"name": "photos/2026/raw-october.tar", "size": 28e9, "percentage": progress, "speed": speed * 0.7, "eta": 1800, "group": "backup-to-nas"},
                     {"name": "documents/scans.zip", "size": 13e9, "percentage": min(100, progress * 1.6), "speed": speed * 0.3, "eta": 600, "group": "backup-to-nas"},
                 ]}
        return self._transfers(stats) if widget_kind == "transfers" else self._status(stats)


ADAPTER = RcloneAdapter()
