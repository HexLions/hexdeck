"""Alertmanager: what is firing, what is silenced, and the silences themselves.

API v2, read-only: ``/api/v2/alerts`` for the alerts, ``/api/v2/silences``
for the silences and ``/api/v2/status`` for the version and the cluster.

⚠️ ``/api/v2/status`` carries the whole configuration as ``config.original``,
and that file holds the SMTP password, the Slack and webhook addresses with
their tokens, the PagerDuty keys. Nothing of it is read past the version and
the cluster, and nothing of it leaves this module.

⚠️ Alertmanager has no sign-in of its own. A user name and password are for
basic authentication, which its web configuration file or a proxy in front of
it may ask for; without either, the API answers anyone who can reach it.

⚠️ A severity is a label like any other. ``severity`` with ``critical``,
``warning`` and ``info`` is the convention of the Prometheus community's own
rules, not something Alertmanager enforces, so an alert without it is ranked
as a warning rather than left out.

Read from Alertmanager's own OpenAPI spec (api/v2/openapi.yaml) at v0.34.1.
"""

from __future__ import annotations

from datetime import UTC, datetime
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

#: The order a list of alerts is read in, worst first.
SEVERITY = {"critical": 0, "error": 0, "page": 0, "warning": 1, "warn": 1, "info": 2, "none": 3}
ALERTS_SECONDS = 30
SILENCES_SECONDS = 120
STATUS_SECONDS = 600


def _when(written: Any) -> float | None:
    text = str(written or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _matchers(silence: dict[str, Any]) -> str:
    """A silence's matchers as Alertmanager's own interface writes them."""
    parts = []
    for one in silence.get("matchers") or []:
        if not isinstance(one, dict):
            continue
        equal = one.get("isEqual", True)
        operator = ("=~" if equal else "!~") if one.get("isRegex") else ("=" if equal else "!=")
        parts.append(f'{one.get("name")}{operator}"{one.get("value")}"')
    return ", ".join(parts)


class AlertmanagerAdapter(Adapter):
    kind = "alertmanager"
    label = "Alertmanager"
    category = "monitoring"
    description = "The alerts that are firing, worst first, the ones that are silenced, and the silences."
    icon = "alertmanager"
    docs_url = "https://prometheus.io/docs/alerting/latest/alertmanager/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://alertmanager:9093"),
        Field("username", "User name", help="Only behind basic authentication."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Alerts", description="How many alerts are firing and how many are silenced, with the version and the cluster.",
                   renderer="value", default_size=(3, 2), refresh_seconds=60, metrics=("firing", "silenced")),
        WidgetType(kind="alerts", label="Firing alerts", description="The alerts that are firing, the critical ones first, with what they are about and since when.",
                   renderer="list", default_size=(4, 3), refresh_seconds=60, metrics=("firing",),
                   options=(
                       Field("show_silenced", "Show silenced and inhibited alerts", type="bool", default=False),
                       Field("receiver", "Receiver", placeholder="team-ops",
                             help="Only the alerts routed to this receiver, by its name. Empty means all of them."),
                       Field("limit", "Entries", type="number", default=10),
                   )),
        WidgetType(kind="silences", label="Silences", description="The silences that are on or about to be, what they match, who set them and when they end.",
                   renderer="list", default_size=(4, 2), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=8),)),
    )

    # -- talking to Alertmanager ---------------------------------------------

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, params: dict[str, Any] | None = None,
                   cache: float = 0) -> Any:
        auth = (str(config["username"]), str(config.get("password") or "")) if config.get("username") else None
        response = await ctx.request(
            "GET", f"{base_url(config)}/api/v2{path}", params=params, auth=auth,
            headers={"Accept": "application/json"}, verify=not config.get("insecure"),
            cache_seconds=cache, auth_errors=False,
        )
        if response.status_code in (401, 403):
            raise AuthFailed("Alertmanager, or the proxy in front of it, refused the sign-in.",
                             hint="Alertmanager itself asks for nothing. A user name and password are for basic "
                                  "authentication set in its web configuration file or in a proxy.")
        if response.status_code == 404:
            raise AdapterError("This address has no Alertmanager API v2.", code="no_such_path",
                               hint="The address of Alertmanager itself, usually on port 9093, without /api. "
                                    "Behind a path prefix, the prefix belongs in the address.")
        if response.status_code >= 400:
            raise AdapterError(f"Alertmanager answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Alertmanager did not answer with JSON.", code="not_json",
                               hint="The address of Alertmanager itself, usually on port 9093.") from failure

    async def _alerts(self, config: dict[str, Any], ctx: Context, *, silenced: bool, receiver: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"active": "true", "silenced": str(silenced).lower(), "inhibited": str(silenced).lower()}
        if receiver:
            params["receiver"] = receiver
        answer = await self._get(config, ctx, "/alerts", params, ALERTS_SECONDS)
        return [one for one in (answer if isinstance(answer, list) else []) if isinstance(one, dict)]

    async def _status(self, config: dict[str, Any], ctx: Context) -> dict[str, Any]:
        """The version and the cluster, and nothing else of ``/status``."""
        answer = await self._get(config, ctx, "/status", None, STATUS_SECONDS)
        answer = answer if isinstance(answer, dict) else {}
        # ⚠️ config.original is the whole configuration with its secrets in it.
        version = (answer.get("versionInfo") or {}).get("version")
        cluster = (answer.get("cluster") or {})
        return {"version": str(version or ""), "cluster": str(cluster.get("status") or ""),
                "peers": len(cluster.get("peers") or [])}

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        status = await self._status(config, ctx)
        firing = await self._alerts(config, ctx, silenced=False)
        said = f" {status['version']}" if status["version"] else ""
        return f"Alertmanager{said} answers with {len(firing)} firing alert{'s' if len(firing) != 1 else ''}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "silences":
            answer = await self._get(config, ctx, "/silences", None, SILENCES_SECONDS)
            return self._silences([one for one in (answer if isinstance(answer, list) else []) if isinstance(one, dict)], options)
        if widget_kind == "alerts":
            alerts = await self._alerts(config, ctx, silenced=bool(options.get("show_silenced")),
                                        receiver=str(options.get("receiver") or "").strip())
            return self._alert_list(alerts, options)
        everything = await self._alerts(config, ctx, silenced=True)
        return self._summary(everything, await self._status(config, ctx))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _rank(alert: dict[str, Any]) -> int:
        severity = str((alert.get("labels") or {}).get("severity") or "").lower()
        return SEVERITY.get(severity, 1)

    @staticmethod
    def _suppressed(alert: dict[str, Any]) -> bool:
        return str((alert.get("status") or {}).get("state") or "") == "suppressed"

    @classmethod
    def _summary(cls, alerts: list[dict[str, Any]], status: dict[str, Any]) -> WidgetData:
        silenced = sum(1 for one in alerts if cls._suppressed(one))
        firing = [one for one in alerts if not cls._suppressed(one)]
        critical = sum(1 for one in firing if cls._rank(one) == 0)
        secondary: list[dict[str, Any]] = []
        if critical:
            secondary.append({"label": "Critical", "value": critical})
        secondary.append({"label": "Silenced", "value": silenced, "metric": "silenced"})
        if status.get("version"):
            secondary.append({"label": "Version", "value": status["version"]})
        # A single instance reports its cluster as disabled; only a cluster
        # that has not settled is worth a word.
        if status.get("cluster") == "settling":
            secondary.append({"label": "Cluster", "value": "settling"})
        return WidgetData(
            status="bad" if critical else "warn" if firing else "ok",
            primary={"label": "Firing", "value": len(firing)},
            secondary=secondary,
            metrics=measured({"firing": float(len(firing)), "silenced": float(silenced)}),
        )

    @classmethod
    def _alert_list(cls, alerts: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        rows = []
        for alert in sorted(alerts, key=lambda one: (cls._rank(one), -(_when(one.get("startsAt")) or 0))):
            labels = alert.get("labels") or {}
            annotations = alert.get("annotations") or {}
            rank = cls._rank(alert)
            colour = "unknown" if cls._suppressed(alert) else "bad" if rank == 0 else "warn" if rank == 1 else "ok"
            where = str(labels.get("instance") or labels.get("job") or labels.get("namespace") or "")
            said = str(annotations.get("summary") or annotations.get("description") or "")
            rows.append({
                "id": alert.get("fingerprint"),
                "title": str(labels.get("alertname") or "?"),
                "subtitle": " · ".join(part for part in (where, said[:80], ago(alert.get("startsAt"))) if part),
                "value": "silenced" if cls._suppressed(alert) else str(labels.get("severity") or ""),
                "status": colour,
                "url": alert.get("generatorURL"),
            })
        firing = sum(1 for one in alerts if not cls._suppressed(one))
        worst = min((cls._rank(one) for one in alerts if not cls._suppressed(one)), default=None)
        return WidgetData(
            status="ok" if worst is None else "bad" if worst == 0 else "warn",
            items=rows[: max(1, int(options.get("limit") or 10))],
            primary={"label": "Firing", "value": firing},
            metrics=measured({"firing": float(firing)}),
            meta={"empty": "Nothing is firing"},
        )

    @classmethod
    def _silences(cls, silences: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        now = datetime.now(UTC).timestamp()
        rows = []
        for silence in silences:
            state = str((silence.get("status") or {}).get("state") or "")
            # An expired silence silences nothing; the list is of what is on.
            if state not in ("active", "pending"):
                continue
            ends = _when(silence.get("endsAt"))
            starts = _when(silence.get("startsAt"))
            if state == "pending" and starts:
                timing = f"starts in {ago(now - (starts - now))}" if starts > now else ""
            else:
                timing = f"ends in {ago(now - (ends - now))}" if ends and ends > now else ""
            parts = [str(silence.get("createdBy") or ""), str(silence.get("comment") or "")[:60], timing]
            rows.append({
                "id": silence.get("id"),
                "title": _matchers(silence) or "?",
                "subtitle": " · ".join(part for part in parts if part),
                "value": state,
                "status": "ok" if state == "active" else "unknown",
                "ends": ends or 0,
            })
        rows.sort(key=lambda row: (row["value"] != "active", row["ends"]))
        for row in rows:
            row.pop("ends")
        active = sum(1 for row in rows if row["value"] == "active")
        return WidgetData(
            status="ok",
            items=rows[: max(1, int(options.get("limit") or 8))],
            primary={"label": "Active", "value": active},
            meta={"empty": "No silence is on"},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        now = datetime.now(UTC).timestamp()

        def stamp(minutes: float) -> str:
            return datetime.fromtimestamp(now + minutes * 60, UTC).isoformat().replace("+00:00", "Z")

        disk = fake.flicker("am-disk", tick, 0.5)
        alerts = [
            {"fingerprint": "a1", "labels": {"alertname": "HostOutOfDiskSpace", "severity": "critical", "instance": "nas:9100"},
             "annotations": {"summary": "Disk /mnt/tank is 94% full"}, "startsAt": stamp(-42), "status": {"state": "active"}},
            {"fingerprint": "a2", "labels": {"alertname": "HostHighCpuLoad", "severity": "warning", "instance": "pve:9100"},
             "annotations": {"summary": "CPU load is above 80%"}, "startsAt": stamp(-8), "status": {"state": "active"}},
            {"fingerprint": "a3", "labels": {"alertname": "ContainerRestarting", "severity": "warning", "instance": "docker:8080"},
             "annotations": {"summary": "paperless restarted 4 times in 15 minutes"}, "startsAt": stamp(-120),
             "status": {"state": "suppressed", "silencedBy": ["s1"]}},
            {"fingerprint": "a4", "labels": {"alertname": "BackupOlderThanADay", "severity": "info", "job": "kopia"},
             "annotations": {"summary": "The last snapshot is 26 hours old"}, "startsAt": stamp(-300), "status": {"state": "active"}},
        ]
        if not disk:
            alerts = alerts[1:]
        silences = [
            {"id": "s1", "matchers": [{"name": "alertname", "value": "ContainerRestarting", "isRegex": False, "isEqual": True}],
             "createdBy": "alex", "comment": "Paperless migration tonight", "startsAt": stamp(-130), "endsAt": stamp(170),
             "status": {"state": "active"}},
            {"id": "s2", "matchers": [{"name": "instance", "value": "pi.*", "isRegex": True, "isEqual": True}],
             "createdBy": "sam", "comment": "Moving the Pi to the rack", "startsAt": stamp(600), "endsAt": stamp(720),
             "status": {"state": "pending"}},
        ]
        if widget_kind == "silences":
            return self._silences(silences, options)
        if widget_kind == "alerts":
            shown = alerts if options.get("show_silenced") else [one for one in alerts if not self._suppressed(one)]
            return self._alert_list(shown, options)
        return self._summary(alerts, {"version": "0.34.1", "cluster": "disabled", "peers": 0})


ADAPTER = AlertmanagerAdapter()
