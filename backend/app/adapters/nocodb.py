"""NocoDB: the bases and their tables, with how many rows each holds.

Written from NocoDB's source and OpenAPI file, release 2026.10.0
(packages/nocodb/src/schema/swagger-v2.json, strategies/authtoken.strategy).
Not yet run against a NocoDB: its release binary was not run here.

⚠️ An API token (Account settings, then Tokens) goes in ``xc-token``. A token
that does not exist, is switched off or has expired is refused alike, with
``{"msg": ...}``. A token made inside one base only works for that base, so
the other bases answer as refused too; the card says to use an account token.

⚠️ ``GET /api/v2/meta/bases/`` lists the bases with ``list`` and ``pageInfo``;
``/meta/bases/{id}/tables`` the tables of one, without the junction tables of
many-to-many links unless asked. Row counts are one request per table
(``/api/v2/tables/{id}/records/count`` answers ``{"count": n}``), so the card
counts at most ``MAX_TABLES`` tables and keeps the answers ten minutes.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

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
)

#: Tables whose rows are counted, one request each.
MAX_TABLES = 40
COUNT_SECONDS = 600


class NocodbAdapter(Adapter):
    kind = "nocodb"
    label = "NocoDB"
    category = "other"
    description = "NocoDB: the bases and their tables, with how many rows each holds, the largest first."
    icon = "nocodb"
    beta = True
    docs_url = "https://nocodb.com/docs/product-docs/developer-resources/rest-apis"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://nocodb.example.com"),
        Field("token", "API token", secret=True, required=True,
              help="In NocoDB: Account settings, then Tokens. A token made inside one base only reads that base."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="summary", label="Rows", description="All the rows across the bases, with the bases and tables they are in.",
                   renderer="value", default_size=(3, 2), refresh_seconds=600, metrics=("rows",)),
        WidgetType(kind="tables", label="Tables", description="Every table with its base and how many rows it holds, the largest first.",
                   renderer="list", default_size=(3, 3), refresh_seconds=600,
                   options=(Field("limit", "Entries", type="number", default=10),)),
    )

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 120) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}/api/v2{path}",
                                     headers={"xc-token": str(config.get("token") or "").strip()},
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("NocoDB refused the API token.",
                             hint="Make an account token in NocoDB under Account settings, then Tokens; one made inside a base only reads that base.")
        if response.status_code >= 400:
            raise AdapterError(f"NocoDB answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is NocoDB's own address, without /api.")
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError("This address answers, but not the way NocoDB does.", code="not_nocodb",
                               hint="Check the URL; it is NocoDB's own address, without /api.") from error

    @staticmethod
    def _list(answer: Any) -> list[dict[str, Any]]:
        rows = answer.get("list") if isinstance(answer, dict) else None
        if not isinstance(rows, list):
            raise AdapterError("This address answers, but not the way NocoDB does.", code="not_nocodb",
                               hint="Check the URL; it is NocoDB's own address, without /api.")
        return [row for row in rows if isinstance(row, dict) and not row.get("deleted")]

    async def _tables(self, config: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], int, int]:
        """Every table with its base's title and row count, the number of bases, and of tables in all."""
        bases = self._list(await self._get(config, ctx, "/meta/bases/"))
        tables: list[dict[str, Any]] = []
        for one in bases:
            for table in self._list(await self._get(config, ctx, f"/meta/bases/{quote(str(one.get('id')), safe='')}/tables")):
                if table.get("enabled") is False or table.get("mm"):
                    continue
                tables.append({"id": table.get("id"), "title": table.get("title") or table.get("table_name") or "?", "base": one.get("title") or ""})
        for table in tables[:MAX_TABLES]:
            answer = await self._get(config, ctx, f"/tables/{quote(str(table['id']), safe='')}/records/count", cache=COUNT_SECONDS)
            count = answer.get("count") if isinstance(answer, dict) else None
            table["rows"] = int(count) if isinstance(count, int | float) else None
        return tables, len(bases), len(tables)

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        bases = self._list(await self._get(config, ctx, "/meta/bases/", cache=0))
        return f"NocoDB answers with {len(bases)} base{'' if len(bases) == 1 else 's'}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        tables, bases, total = await self._tables(config, ctx)
        if widget_kind == "tables":
            return self._list_card(tables, max(1, int(options.get("limit") or 10)))
        return self._summary(tables, bases, total)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _summary(tables: list[dict[str, Any]], bases: int, total: int) -> WidgetData:
        counted = [table["rows"] for table in tables if isinstance(table.get("rows"), int)]
        rows = sum(counted)
        partial = total > len(counted)
        return WidgetData(
            status="ok",
            primary={"label": "Rows", "value": f"{rows}+" if partial else rows},
            secondary=[{"label": "Bases", "value": bases}, {"label": "Tables", "value": total}],
            metrics={"rows": float(rows)},
        )

    @staticmethod
    def _list_card(tables: list[dict[str, Any]], limit: int) -> WidgetData:
        ordered = sorted(tables, key=lambda table: (-(table.get("rows") or -1), str(table["title"]).lower()))
        items = [{"title": str(table["title"]), "subtitle": str(table["base"]), "status": "ok",
                  "value": table["rows"] if isinstance(table.get("rows"), int) else ""} for table in ordered[:limit]]
        return WidgetData(status="ok", items=items, meta={"empty": "NocoDB has no table yet."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        orders = int(fake.walk("nocodb-orders", tick, 1180, 1260))
        tables = [
            {"title": "Orders", "base": "Shop", "rows": orders},
            {"title": "Customers", "base": "Shop", "rows": 342},
            {"title": "Products", "base": "Shop", "rows": 87},
            {"title": "Devices", "base": "Homelab", "rows": 41},
            {"title": "Warranties", "base": "Homelab", "rows": 12},
        ]
        if widget_kind == "tables":
            return self._list_card(tables, max(1, int(options.get("limit") or 10)))
        return self._summary(tables, 2, len(tables))


ADAPTER = NocodbAdapter()
