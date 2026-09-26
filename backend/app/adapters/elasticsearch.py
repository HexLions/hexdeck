"""Elasticsearch and OpenSearch: the colour of the cluster, and which index earned it.

Three read-only calls, all of them part of both products because OpenSearch was
forked from Elasticsearch and kept these: ``/`` for the version, ``/_cluster/health``
for the colour and the shards, and ``/_cat/indices`` for the indices themselves.

⚠️ Green, yellow and red are the words the cluster itself uses, and they mean
something precise: yellow is "every index is readable and writable, but a
replica is not placed", which on a one node cluster is the normal state
forever. The cards pass the colour through rather than inventing their own,
and a single node cluster says why it is yellow.

⚠️ Either basic credentials or an API key, never both. An API key is the
better one: it can be given the two cluster privileges these calls need
(``monitor`` and ``view_index_metadata``) and nothing else.
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

#: The cluster's own vocabulary, and what a card makes of it.
COLOURS = {"green": "ok", "yellow": "warn", "red": "bad"}
HEALTH_SECONDS = 15
INDEX_SECONDS = 60


class ElasticsearchAdapter(Adapter):
    kind = "elasticsearch"
    label = "Elasticsearch"
    category = "hosts"
    description = "The colour of the cluster, its shards and its indices. OpenSearch answers the same calls."
    icon = "elasticsearch"
    beta = True
    docs_url = "https://www.elastic.co/guide/en/elasticsearch/reference/current/cluster-health.html"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://elasticsearch:9200"),
        Field("api_key", "API key", type="password", secret=True,
              help="An encoded API key, which is the narrowest way in: the cluster privileges monitor and view_index_metadata are enough."),
        Field("username", "User name", placeholder="elastic",
              help="Only if you have no API key. Leave both empty for a cluster without security."),
        Field("password", "Password", type="password", secret=True),
        Field("insecure", "Ignore TLS errors", type="bool", default=False,
              help="Elasticsearch generates its own certificate on the first start, and nothing outside the cluster trusts it."),
    )
    widgets = (
        WidgetType(kind="cluster", label="Cluster", description="The colour, the nodes, the shards that are not placed and how much is stored.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("documents", "unassigned")),
        WidgetType(kind="indices", label="Indices", description="Every index with its documents and its size, the unhealthy ones first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=120, metrics=("documents",),
                   options=(
                       Field("limit", "Entries", type="number", default=10),
                       Field("hidden", "Include system indices", type="bool", default=False,
                             help="The indices whose name begins with a dot, which the cluster keeps for itself."),
                   )),
    )

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        key = str(config.get("api_key") or "").strip()
        if key:
            return {"Authorization": f"ApiKey {key}"}
        return {}

    @staticmethod
    def _auth(config: dict[str, Any]) -> tuple[str, str] | None:
        user, password = str(config.get("username") or ""), str(config.get("password") or "")
        return (user, password) if user or password else None

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float,
                   params: dict[str, Any] | None = None) -> Any:
        if config.get("api_key") and config.get("username"):
            raise AdapterError("This connection has both an API key and a user name.", code="two_credentials",
                               hint="Elasticsearch takes one or the other. Clear whichever you are not using.")
        response = await ctx.request(
            "GET", f"{base_url(config)}{path}", headers=self._headers(config), params=params,
            auth=self._auth(config), verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False,
        )
        if response.status_code in (401, 403):
            raise AuthFailed("The cluster refused the credentials." if response.status_code == 401
                             else "These credentials may not read the cluster's health.")
        if response.status_code >= 400:
            raise AdapterError(f"The cluster answered with HTTP {response.status_code}.", code="http_error")
        try:
            return response.json()
        except ValueError as failure:
            raise AdapterError("The cluster did not answer with JSON.", code="not_json",
                               hint="Is this the Elasticsearch port (9200) rather than Kibana's?") from failure

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        root = await self._get(config, ctx, "/", 0)
        if not isinstance(root, dict) or not isinstance(root.get("version"), dict):
            raise AdapterError("That address answers, but not the way Elasticsearch does.", code="not_elasticsearch")
        health = await self._get(config, ctx, "/_cluster/health", 0)
        distribution = str(root["version"].get("distribution") or "elasticsearch")
        name = "OpenSearch" if distribution == "opensearch" else "Elasticsearch"
        return (f"{name} {root['version'].get('number', '?')} answers: cluster "
                f"{health.get('cluster_name', '?')} is {health.get('status', '?')}.")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        if widget_kind == "indices":
            return self._indices(await self._catalogue(config, ctx), options)
        health = await self._get(config, ctx, "/_cluster/health", HEALTH_SECONDS)
        if not isinstance(health, dict) or "status" not in health:
            raise AdapterError("That address answers, but not the way Elasticsearch does.", code="not_elasticsearch")
        return self._cluster(health, await self._catalogue(config, ctx))

    async def _catalogue(self, config: dict[str, Any], ctx: Context) -> list[dict[str, Any]]:
        answer = await self._get(config, ctx, "/_cat/indices", INDEX_SECONDS, {
            "format": "json", "bytes": "b", "h": "index,health,status,docs.count,store.size,pri,rep",
        })
        return [one for one in answer or [] if isinstance(one, dict)] if isinstance(answer, list) else []

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _cluster(cls, health: dict[str, Any], indices: list[dict[str, Any]]) -> WidgetData:
        colour = str(health.get("status") or "").lower()
        nodes = cls._int(health.get("number_of_nodes"))
        unassigned = cls._int(health.get("unassigned_shards"))
        documents = sum(cls._int(one.get("docs.count")) for one in indices)
        stored = sum(cls._int(one.get("store.size")) for one in indices)
        secondary: list[dict[str, Any]] = [
            {"label": "Nodes", "value": nodes},
            {"label": "Shards", "value": cls._int(health.get("active_shards"))},
        ]
        if unassigned:
            secondary.append({"label": "Unplaced", "value": unassigned, "metric": "unassigned"})
        moving = cls._int(health.get("relocating_shards")) + cls._int(health.get("initializing_shards"))
        if moving:
            secondary.append({"label": "Moving", "value": moving})
        secondary.append({"label": "Documents", "value": documents, "metric": "documents"})
        if stored:
            secondary.append({"label": "Stored", "value": human_bytes(stored)})
        # ⚠️ One node cannot place a replica anywhere, so it is yellow and stays
        # yellow. Saying nothing there makes a healthy cluster look broken.
        note = "A single node cannot place its replicas, which is why it is yellow." if colour == "yellow" and nodes <= 1 else ""
        return WidgetData(
            status=COLOURS.get(colour, "unknown"),
            primary={"label": "Cluster", "value": colour or "unknown"},
            secondary=secondary,
            metrics=measured({"documents": float(documents), "unassigned": float(unassigned)}),
            meta={"cluster": str(health.get("cluster_name") or ""), "notice": note},
        )

    @classmethod
    def _indices(cls, indices: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        rows = [one for one in indices if options.get("hidden") or not str(one.get("index") or "").startswith(".")]
        order = {"bad": 0, "warn": 1, "ok": 2}
        def rank(one: dict[str, Any]) -> tuple[int, int]:
            colour = COLOURS.get(str(one.get("health") or "").lower(), "unknown")
            return order.get(colour, 3), -cls._int(one.get("store.size"))
        items = []
        for one in sorted(rows, key=rank):
            closed = str(one.get("status") or "") != "open"
            items.append({
                "title": str(one.get("index") or "?"),
                "subtitle": " · ".join(part for part in (
                    "closed" if closed else "",
                    f"{cls._int(one.get('pri'))} primaries, {cls._int(one.get('rep'))} replicas",
                ) if part),
                "value": f"{cls._int(one.get('docs.count')):,}".replace(",", " "),
                "status": "unknown" if closed else COLOURS.get(str(one.get("health") or "").lower(), "unknown"),
                "text": human_bytes(cls._int(one.get("store.size"))),
            })
        documents = sum(cls._int(one.get("docs.count")) for one in rows)
        unhealthy = sum(1 for one in rows if str(one.get("health") or "").lower() in ("red", "yellow"))
        return WidgetData(
            status="bad" if any(str(one.get("health") or "").lower() == "red" for one in rows) else "warn" if unhealthy else "ok",
            items=items[: max(1, int(options.get("limit") or 10))],
            primary={"label": "Documents", "value": documents},
            secondary=[{"label": "Indices", "value": len(rows)}],
            metrics=measured({"documents": float(documents)}),
            meta={"empty": "This cluster holds no indices yet."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        indices = [
            {"index": "paperless", "health": "green", "status": "open", "docs.count": 18932, "store.size": 4_100_000_000, "pri": 1, "rep": 1},
            {"index": "logs-2026.09", "health": "yellow", "status": "open", "docs.count": 4_812_003 + tick, "store.size": 12_400_000_000, "pri": 3, "rep": 1},
            {"index": "immich-clip", "health": "green", "status": "open", "docs.count": 102_884, "store.size": 890_000_000, "pri": 1, "rep": 0},
            {"index": ".kibana_8", "health": "green", "status": "open", "docs.count": 412, "store.size": 2_100_000, "pri": 1, "rep": 0},
        ]
        if widget_kind == "indices":
            return self._indices(indices, options)
        return self._cluster({
            "cluster_name": "homelab", "status": "yellow" if fake.flicker("es-yellow", tick, 0.7) else "green",
            "number_of_nodes": 2, "active_shards": 9, "unassigned_shards": 1, "relocating_shards": 0,
            "initializing_shards": 0,
        }, indices)


ADAPTER = ElasticsearchAdapter()
