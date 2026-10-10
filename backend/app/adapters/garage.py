"""Garage: the health of the S3 cluster, its nodes, and its buckets.

Garage's admin API v2, read-only, with an admin token as Bearer:
``/v2/GetClusterHealth`` for the health, ``/v2/GetClusterStatus`` for the
nodes, ``/v2/GetClusterStatistics`` for the objects and the free space,
``/v2/ListBuckets`` and ``/v2/GetBucketInfo`` for the buckets.

⚠️ The admin API listens on its own port, 3903 by default, not on the S3
port. The token is ``admin_token`` from garage.toml, or since 2.0 a token
made with ``garage admin-token create``, which can be limited to the calls
read here.

⚠️ The object count, the bytes and the free space came into the statistics
in 2.4; before that they were only a text for people, which is not parsed.
The overview then shows the health alone.

⚠️ The health is Garage's own: healthy, degraded (a node down but quorum
kept) or unavailable (some partitions without quorum).

⚠️ A bucket's answer lists the access keys that may use it; only the
counts are shown.

Checked against Garage 2.4.1 running locally on 2026-10-10 as one node with
two buckets and three objects; downloaded from garagehq.deuxfleurs.fr, which
publishes no checksums. The API is that of the OpenAPI spec in Garage's
repository at v2.4.1.
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
    human_bytes,
    measured,
)

HEALTH = {"healthy": ("ok", "healthy"), "degraded": ("warn", "degraded"), "unavailable": ("bad", "unavailable")}
CLUSTER_SECONDS = 60
BUCKETS_SECONDS = 600


class GarageAdapter(Adapter):
    kind = "garage"
    label = "Garage"
    category = "nas"
    description = "The Garage S3 cluster: its health, the nodes up and their free space, and each bucket's objects and size."
    icon = "garage"
    docs_url = "https://garagehq.deuxfleurs.fr/documentation/reference-manual/admin-api/"
    fields = (
        Field("url", "Admin API URL", type="url", required=True, placeholder="http://garage:3903",
              help="The admin API, port 3903 by default, not the S3 port."),
        Field("token", "Admin token", type="password", secret=True, required=True,
              help="admin_token from garage.toml, or a token made with garage admin-token create."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="cluster", label="Cluster", description="The cluster's health, the storage nodes up, the objects stored and the space left.",
                   renderer="value", default_size=(3, 2), refresh_seconds=120, metrics=("nodes_down", "bytes")),
        WidgetType(kind="nodes", label="Nodes", description="Every node with its zone, capacity and free space, the ones down first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120,
                   options=(Field("limit", "Entries", type="number", default=10),)),
        WidgetType(kind="buckets", label="Buckets", description="The buckets with their objects and size, the biggest first, against their quota when one is set.",
                   renderer="list", default_size=(3, 3), refresh_seconds=900,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, call: str, cache: float, params: dict[str, Any] | None = None) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/v2/{call}", verify=not config.get("insecure"), params=params,
                                     headers={"Authorization": f"Bearer {str(config.get('token') or '').strip()}", "Accept": "application/json"},
                                     cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("Garage refused the admin token.", hint="admin_token from garage.toml, or a token made with garage admin-token create.")
        if response.status_code == 404:
            raise AdapterError("Garage has no admin API v2 at this address.", code="http_error",
                               hint="The admin API port, 3903 by default, on Garage 2.0 or newer.")
        if response.status_code >= 400:
            raise AdapterError(f"Garage answered with HTTP {response.status_code}.", code="http_error",
                               hint="The admin API port, 3903 by default; the S3 port answers 400 here.")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("Garage did not answer with JSON.", code="not_json", hint="The admin API port, 3903 by default.") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        health = await self._get(config, ctx, "GetClusterHealth", 0) or {}
        return f"Garage answers; the cluster is {health.get('status') or 'in an unknown state'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        limit = max(1, int(options.get("limit") or 10))
        if widget_kind == "nodes":
            status = await self._get(config, ctx, "GetClusterStatus", CLUSTER_SECONDS) or {}
            return self._nodes([one for one in (status.get("nodes") or []) if isinstance(one, dict)], limit)
        if widget_kind == "buckets":
            listed = [one for one in (await self._get(config, ctx, "ListBuckets", BUCKETS_SECONDS) or []) if isinstance(one, dict)]
            infos = []
            for bucket in listed[:50]:
                infos.append(await self._get(config, ctx, "GetBucketInfo", BUCKETS_SECONDS, {"id": bucket.get("id")}) or {})
            return self._buckets(infos, limit)
        health = await self._get(config, ctx, "GetClusterHealth", CLUSTER_SECONDS) or {}
        statistics = await self._get(config, ctx, "GetClusterStatistics", CLUSTER_SECONDS) or {}
        return self._cluster(health, statistics)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _cluster(health: dict[str, Any], statistics: dict[str, Any]) -> WidgetData:
        colour, word = HEALTH.get(str(health.get("status") or ""), ("unknown", str(health.get("status") or "unknown")))
        nodes, up = int(health.get("storageNodes") or 0), int(health.get("storageNodesUp") or 0)
        secondary: list[dict[str, Any]] = [{"label": "Nodes up", "value": f"{up} / {nodes}"}]
        stored = statistics.get("totalObjectBytes")
        if isinstance(statistics.get("totalObjectCount"), int):
            secondary.append({"label": "Objects", "value": statistics["totalObjectCount"]})
        if isinstance(stored, int):
            secondary.append({"label": "Stored", "value": human_bytes(stored), "metric": "bytes"})
        if isinstance(statistics.get("dataAvail"), int):
            secondary.append({"label": "Free", "value": human_bytes(statistics["dataAvail"])})
        partitions, quorum = health.get("partitions"), health.get("partitionsQuorum")
        notice = f"{int(partitions) - int(quorum)} partitions without quorum." if isinstance(partitions, int) and isinstance(quorum, int) and quorum < partitions else ""
        return WidgetData(
            status=colour,
            primary={"label": "Cluster", "value": word},
            secondary=secondary,
            metrics=measured({"nodes_down": float(nodes - up), "bytes": float(stored) if isinstance(stored, int) else None}),
            meta={"notice": notice},
        )

    @staticmethod
    def _nodes(nodes: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        for node in nodes:
            role = node.get("role") or {}
            data = node.get("dataPartition") or {}
            up = bool(node.get("isUp"))
            parts = [str(role.get("zone") or "no role")]
            if role.get("capacity"):
                parts.append(human_bytes(role["capacity"]))
            if node.get("draining"):
                parts.append("draining")
            row: dict[str, Any] = {
                "id": str(node.get("id") or "")[:16],
                "title": str(node.get("hostname") or str(node.get("id") or "?")[:16]),
                "subtitle": " · ".join(parts),
                "value": f"{human_bytes(data['available'])} free" if up and isinstance(data.get("available"), int) else "up" if up else "down",
                "status": "ok" if up else "bad",
            }
            if up and isinstance(data.get("available"), int) and data.get("total"):
                row["progress"] = round(100 * (1 - data["available"] / data["total"]), 1)
            rows.append(row)
        rows.sort(key=lambda row: (row["status"] != "bad", row["title"].lower()))
        return WidgetData(status="bad" if any(row["status"] == "bad" for row in rows) else "ok", items=rows[:limit])

    @staticmethod
    def _buckets(buckets: list[dict[str, Any]], limit: int) -> WidgetData:
        rows = []
        for bucket in buckets:
            size = int(bucket.get("bytes") or 0)
            quota = (bucket.get("quotas") or {}).get("maxSize")
            aliases = bucket.get("globalAliases") or []
            row: dict[str, Any] = {
                "id": bucket.get("id"),
                "title": str(aliases[0] if aliases else str(bucket.get("id") or "?")[:16]),
                "subtitle": f"{int(bucket.get('objects') or 0)} objects",
                "value": human_bytes(size) + (f" / {human_bytes(quota)}" if isinstance(quota, int) and quota else ""),
                "status": "ok",
                "_size": size,
            }
            if isinstance(quota, int) and quota:
                row["progress"] = round(100 * size / quota, 1)
                row["status"] = "warn" if size >= 0.9 * quota else "ok"
            rows.append(row)
        rows.sort(key=lambda row: -row["_size"])
        for row in rows:
            row.pop("_size")
        return WidgetData(status="warn" if any(row["status"] == "warn" for row in rows) else "ok", items=rows[:limit],
                          meta={"empty": "No bucket yet"})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        down = fake.flicker("garage-node", tick, 0.15)
        stored = int(fake.walk("garage-bytes", tick, 410e9, 430e9))
        if widget_kind == "nodes":
            nodes = [{"id": f"{index}a1b2c3d4e5f6a7b8", "hostname": f"garage-{index}", "isUp": not (down and index == 3),
                      "role": {"zone": f"dc{1 + index % 2}", "capacity": 2_000_000_000_000},
                      "dataPartition": {"available": 1_400_000_000_000 - index * 90_000_000_000, "total": 2_000_000_000_000}}
                     for index in (1, 2, 3)]
            return self._nodes(nodes, max(1, int(options.get("limit") or 10)))
        if widget_kind == "buckets":
            buckets = [{"id": "1", "globalAliases": ["backups"], "objects": 48_211, "bytes": 301_000_000_000, "quotas": {"maxSize": 400_000_000_000}},
                       {"id": "2", "globalAliases": ["photos"], "objects": 92_377, "bytes": 108_000_000_000, "quotas": {}},
                       {"id": "3", "globalAliases": ["nextcloud"], "objects": 15_034, "bytes": 11_200_000_000, "quotas": {}}]
            return self._buckets(buckets, max(1, int(options.get("limit") or 10)))
        return self._cluster({"status": "degraded" if down else "healthy", "storageNodes": 3, "storageNodesUp": 2 if down else 3,
                              "partitions": 256, "partitionsQuorum": 256},
                             {"totalObjectCount": 155_622, "totalObjectBytes": stored, "dataAvail": 3_900_000_000_000})


ADAPTER = GarageAdapter()
