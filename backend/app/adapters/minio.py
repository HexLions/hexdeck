"""MinIO: the space used and free, the nodes and drives online, and the buckets
by size, from its Prometheus metrics.

Written from MinIO's source, RELEASE.2025-10-15T17-29-55Z (cmd/metrics-v2.go,
cmd/metrics-router.go, cmd/metrics.go, cmd/auth-handler.go). Not yet run
against a MinIO: the project was archived in 2026 and its release binaries
are no longer served, so none could be run here.

⚠️ The metrics, not the admin API: the admin API wants every request signed
with the secret key (AWS Signature V4), and the metrics take a bearer token
that ``mc admin prometheus generate <alias>`` prints, signed with that key
once and holding no more than the right to read metrics. A MinIO started
with ``MINIO_PROMETHEUS_AUTH_TYPE=public`` needs no token at all.

⚠️ The paths sit under ``/minio``: ``/minio/v2/metrics/cluster`` and
``/minio/v2/metrics/bucket``. A refused token is an admin API error
(``toAdminAPIErr(errAuthentication)`` in metrics.go); its status lives in
files not read here, so 401 and 403 are both taken as a refusal.

⚠️ The usage figures (``minio_cluster_usage_*``, ``minio_bucket_usage_*``)
come from MinIO's background scanner. A freshly started MinIO reports none
until its first pass, so the card says so rather than showing zero.

⚠️ Lines carry labels (``{server="..."}``, ``{bucket="..."}``); a cluster
of several nodes repeats the cluster figures once per node, so the card takes
the highest, not the sum.
"""

from __future__ import annotations

import re
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
    human_bytes,
    percent,
    percent_text,
    status_from_percent,
)

_LINE = re.compile(r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)')
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_metrics(text: str) -> list[tuple[str, dict[str, str], float]]:
    """Every sample of a Prometheus text page as (name, labels, value)."""
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        found = _LINE.match(line)
        if not found:
            continue
        try:
            value = float(found.group("value"))
        except ValueError:
            continue
        samples.append((found.group("name"), dict(_LABEL.findall(found.group("labels") or "")), value))
    return samples


def _highest(samples: list[tuple[str, dict[str, str], float]], name: str) -> float | None:
    values = [value for metric, _labels, value in samples if metric == name]
    return max(values) if values else None


class MinioAdapter(Adapter):
    kind = "minio"
    label = "MinIO"
    category = "nas"
    description = "MinIO object storage: space used and free, nodes and drives online, and the buckets by size."
    icon = "minio"
    beta = True
    docs_url = "https://github.com/minio/minio/blob/master/docs/metrics/prometheus/README.md"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://minio.example.com:9000",
              help="The S3 address of MinIO, not its console."),
        Field("token", "Metrics token", type="password", secret=True,
              help="What mc admin prometheus generate <alias> prints as bearer_token. Empty when MinIO runs with MINIO_PROMETHEUS_AUTH_TYPE=public."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="cluster", label="Storage", description="Space used of the usable capacity, buckets and objects, nodes and drives online.",
                   renderer="value", default_size=(3, 2), refresh_seconds=120, metrics=("used_percent", "drives_offline")),
        WidgetType(kind="buckets", label="Buckets", description="Every bucket with its size and objects, the largest first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=300,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    async def _metrics(self, config: dict[str, Any], ctx: Context, which: str, cache: float = 60) -> list[tuple[str, dict[str, str], float]]:
        token = str(config.get("token") or "").strip()
        response = await ctx.request("GET", f"{base_url(config)}/minio/v2/metrics/{which}",
                                     headers={"Authorization": f"Bearer {token}"} if token else {},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("MinIO refused the metrics token." if token else "MinIO wants a token for its metrics.",
                             hint="Run mc admin prometheus generate <alias> and paste its bearer_token.")
        if response.status_code == 404:
            raise AdapterError("This address has no MinIO metrics.", code="not_minio",
                               hint="Use the S3 address of MinIO (port 9000 by default), not the console.")
        if response.status_code >= 400:
            raise AdapterError(f"MinIO answered with HTTP {response.status_code}.", code="http_error",
                               hint="Use the S3 address of MinIO (port 9000 by default), not the console.")
        samples = parse_metrics(response.text)
        if not any(name.startswith("minio_") for name, _labels, _value in samples):
            raise AdapterError("This address answers, but not the way MinIO does.", code="not_minio",
                               hint="Use the S3 address of MinIO (port 9000 by default), not the console.")
        return samples

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        samples = await self._metrics(config, ctx, "cluster", cache=0)
        online = _highest(samples, "minio_cluster_nodes_online_total")
        return f"MinIO answers with {int(online)} node{'' if online == 1 else 's'} online." if online is not None else "MinIO answers."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "buckets":
            return self._buckets(await self._metrics(config, ctx, "bucket", cache=240), max(1, int(options.get("limit") or 10)))
        return self._cluster(await self._metrics(config, ctx, "cluster"))

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _cluster(samples: list[tuple[str, dict[str, str], float]]) -> WidgetData:
        total = _highest(samples, "minio_cluster_capacity_usable_total_bytes")
        free = _highest(samples, "minio_cluster_capacity_usable_free_bytes")
        nodes_on = _highest(samples, "minio_cluster_nodes_online_total")
        nodes_off = _highest(samples, "minio_cluster_nodes_offline_total")
        drives_on = _highest(samples, "minio_cluster_drive_online_total")
        drives_off = _highest(samples, "minio_cluster_drive_offline_total")
        buckets = _highest(samples, "minio_cluster_bucket_total")
        objects = _highest(samples, "minio_cluster_usage_object_total")
        used = total - free if total is not None and free is not None else None
        share = percent(used, total) if used is not None and total else None
        secondary: list[dict[str, Any]] = []
        if used is not None and total:
            secondary.append({"label": "Used", "value": f"{human_bytes(used)} of {human_bytes(total)}"})
        # A figure MinIO did not report is left out, not shown as nought.
        if nodes_on is not None:
            secondary.append({"label": "Nodes", "value": f"{int(nodes_on)} / {int(nodes_on + (nodes_off or 0))}"})
        if drives_on is not None:
            secondary.append({"label": "Drives", "value": f"{int(drives_on)} / {int(drives_on + (drives_off or 0))}"})
        if buckets is not None:
            secondary.append({"label": "Buckets", "value": int(buckets)})
        if objects is not None:
            secondary.append({"label": "Objects", "value": int(objects)})
        offline = [f"{int(count)} {word} offline" for count, word in ((nodes_off, "node(s)"), (drives_off, "drive(s)")) if count]
        status = "bad" if offline else status_from_percent(share)
        return WidgetData(
            status=status,
            primary={"label": "Used", "value": percent_text(share) if share is not None else "?"},
            secondary=secondary,
            metrics={key: value for key, value in (("used_percent", share), ("drives_offline", drives_off)) if value is not None},
            meta={"status_reason": ", ".join(offline)} if offline else {},
        )

    @staticmethod
    def _buckets(samples: list[tuple[str, dict[str, str], float]], limit: int) -> WidgetData:
        sizes: dict[str, float] = {}
        objects: dict[str, float] = {}
        for name, labels, value in samples:
            bucket = labels.get("bucket")
            if not bucket:
                continue
            if name == "minio_bucket_usage_total_bytes":
                sizes[bucket] = max(value, sizes.get(bucket, 0))
            elif name == "minio_bucket_usage_object_total":
                objects[bucket] = max(value, objects.get(bucket, 0))
        names = sorted(set(sizes) | set(objects), key=lambda bucket: (-sizes.get(bucket, 0), bucket))
        items = [{"title": bucket, "subtitle": (f"{int(objects[bucket])} object{'' if objects[bucket] == 1 else 's'}" if bucket in objects else ""),
                  "status": "ok", "value": human_bytes(sizes[bucket]) if bucket in sizes else ""} for bucket in names[:limit]]
        return WidgetData(status="ok", items=items,
                          meta={"empty": "MinIO has not counted its buckets yet; its scanner reports them after its first pass."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        gb = 1024**3
        photos = fake.walk("minio-photos", tick, 410, 418) * gb
        if widget_kind == "buckets":
            return self._buckets([
                ("minio_bucket_usage_total_bytes", {"bucket": "photos"}, photos),
                ("minio_bucket_usage_object_total", {"bucket": "photos"}, 48211),
                ("minio_bucket_usage_total_bytes", {"bucket": "backups"}, 260 * gb),
                ("minio_bucket_usage_object_total", {"bucket": "backups"}, 312),
                ("minio_bucket_usage_total_bytes", {"bucket": "loki"}, 38 * gb),
                ("minio_bucket_usage_object_total", {"bucket": "loki"}, 90420),
            ], max(1, int(options.get("limit") or 10)))
        total = 1800 * gb
        return self._cluster([
            ("minio_cluster_capacity_usable_total_bytes", {}, total),
            ("minio_cluster_capacity_usable_free_bytes", {}, total - photos - 298 * gb),
            ("minio_cluster_nodes_online_total", {}, 1), ("minio_cluster_nodes_offline_total", {}, 0),
            ("minio_cluster_drive_online_total", {}, 4), ("minio_cluster_drive_offline_total", {}, 0),
            ("minio_cluster_bucket_total", {}, 3), ("minio_cluster_usage_object_total", {}, 138943),
        ])


ADAPTER = MinioAdapter()
