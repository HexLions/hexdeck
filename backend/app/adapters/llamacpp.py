"""llama.cpp server: which model it serves, how many slots are busy, and how fast it generates.

Checked against llama-server b11734 (commit f3fb2fcdb) running locally on
19.10.2026 with a 19 MB test model, two slots, ``--metrics`` and
``--api-key``; then again without them and with ``--no-slots``.

⚠️ ``/health`` answers without the key, everything else needs it once
``--api-key`` is set: 401 ``{"error": {"message": "Invalid API Key", "type":
"authentication_error"}}`` for a wrong or missing one. So a wrong key is
noticed only on ``/props``, never on the health check.

⚠️ While the model loads, ``/health`` answers 503 ``{"error": {"message":
"Loading model", "type": "unavailable_error"}}``. That is a server on its way
up, not a broken one, so the cards say so in grey instead of failing.

⚠️ ``/slots`` and ``/metrics`` can be switched off (``--no-slots``, and
``--metrics`` is off by default); they then answer 501 with
``not_supported_error``. ``/props`` says which of them are on
(``endpoint_slots``, ``endpoint_metrics``), so neither is asked for in vain.

⚠️ The ``*_tokens_seconds`` gauges are the average since the previous read of
``/metrics``, and they are 0 when no request ended in between: one capture
read 0 while a slot was generating. The speed shown is the counters'
average instead (tokens over seconds, since the server started), which no
reader can reset.

⚠️ Without ``--alias``, ``model_alias`` is the full path of the model file on
the server. Only the file name is shown.
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
)

NOT_LLAMACPP = "This address answers, but not the way llama.cpp's server does."


def _model_name(props: dict[str, Any]) -> str:
    name = str(props.get("model_alias") or props.get("model_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name.removesuffix(".gguf") or "?"


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_metrics(text: str) -> dict[str, float]:
    """``llamacpp:<name> <value>`` lines of the Prometheus text, by name."""
    values: dict[str, float] = {}
    for line in text.splitlines():
        if not line.startswith("llamacpp:"):
            continue
        name, _, value = line[len("llamacpp:"):].partition(" ")
        try:
            values[name] = float(value.strip())
        except ValueError:
            continue
    return values


def _rate(metrics: dict[str, float], tokens: str, seconds: str) -> float | None:
    """Tokens per second since the server started, or None before the first request ended."""
    spent = metrics.get(seconds, 0.0)
    return round(metrics.get(tokens, 0.0) / spent, 1) if spent > 0 else None




class LlamaCppAdapter(Adapter):
    kind = "llamacpp"
    label = "llama.cpp server"
    category = "other"
    description = "The model llama-server serves, how many of its slots are busy, and how fast it generates."
    icon = "lucide:cpu"
    beta = True
    docs_url = "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="http://llama:8080"),
        Field("api_key", "API key", secret=True, help="Only when llama-server was started with --api-key."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False),
    )
    widgets = (
        WidgetType(kind="server", label="Server",
                   description="The model, the busy slots and the generation speed. Speed needs --metrics on the server.",
                   renderer="value", default_size=(3, 2), refresh_seconds=30, metrics=("busy", "tokens_per_second")),
        WidgetType(kind="slots", label="Slots", description="Every slot, and whether it is working on a request.",
                   renderer="list", default_size=(3, 3), refresh_seconds=15),
    )

    def _headers(self, config: dict[str, Any]) -> dict[str, str]:
        key = str(config.get("api_key") or "").strip()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float = 10) -> Any:
        response = await ctx.request("GET", f"{base_url(config)}{path}", headers=self._headers(config),
                                     verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False)
        if response.status_code in (401, 403):
            raise AuthFailed("llama-server refused the API key.",
                             hint="Use one of the keys the server was started with (--api-key or --api-key-file).")
        if response.status_code == 503:
            raise AdapterError("llama-server is still loading its model.", code="loading",
                               hint="This passes by itself once the model is in memory.")
        if response.status_code == 501:
            raise AdapterError("llama-server has this endpoint switched off.", code="not_supported",
                               hint="Start it with --slots (and --metrics for the speed).")
        if response.status_code >= 400:
            raise AdapterError(f"llama-server answered with HTTP {response.status_code}.", code="http_error",
                               hint="Check the URL; it is the server's own address, without /v1.")
        if path == "/metrics":
            return response.text
        try:
            return response.json()
        except ValueError as error:
            raise AdapterError(NOT_LLAMACPP, code="not_llamacpp", hint="The URL probably points at something else.") from error

    async def _props(self, config: dict[str, Any], ctx: Context, cache: float = 30) -> dict[str, Any]:
        await self._get(config, ctx, "/health", cache=cache)
        props = await self._get(config, ctx, "/props", cache=cache)
        if not isinstance(props, dict) or "total_slots" not in props:
            raise AdapterError(NOT_LLAMACPP, code="not_llamacpp", hint="The URL probably points at something else.")
        return props

    async def _slots(self, config: dict[str, Any], ctx: Context, props: dict[str, Any]) -> list[dict[str, Any]] | None:
        if not props.get("endpoint_slots"):
            return None
        slots = await self._get(config, ctx, "/slots", cache=5)
        return [one for one in slots if isinstance(one, dict)] if isinstance(slots, list) else None

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        props = await self._props(config, ctx, cache=0)
        build = str(props.get("build_info") or "").split("-", 1)[0]
        return f"llama-server {build} answers with {_model_name(props)} on {props.get('total_slots')} slots.".replace("  ", " ")

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        try:
            props = await self._props(config, ctx)
        except AdapterError as failure:
            if failure.code != "loading":
                raise
            return WidgetData(status="unknown", primary={"label": "Model", "value": "Loading"},
                              meta={"empty": "The model is still loading."})
        slots = await self._slots(config, ctx, props)
        if widget_kind == "slots":
            return self._slot_list(slots)
        metrics = parse_metrics(await self._get(config, ctx, "/metrics", cache=10)) if props.get("endpoint_metrics") else None
        return self._server(props, slots, metrics)

    # -- the cards -----------------------------------------------------------

    @staticmethod
    def _server(props: dict[str, Any], slots: list[dict[str, Any]] | None, metrics: dict[str, float] | None) -> WidgetData:
        total = int(_number(props.get("total_slots")) or 0)
        busy = sum(1 for one in slots if one.get("is_processing")) if slots is not None else None
        if busy is None and metrics is not None:
            busy = int(metrics.get("requests_processing", 0))
        secondary: list[dict[str, Any]] = [{"label": "Model", "value": _model_name(props)}]
        generation = _rate(metrics, "tokens_predicted_total", "tokens_predicted_seconds_total") if metrics is not None else None
        prompt = _rate(metrics, "prompt_tokens_total", "prompt_seconds_total") if metrics is not None else None
        # Before the first request has ended there is no speed to show yet.
        if generation is not None:
            secondary.append({"label": "Generation", "value": f"{generation:.1f} tok/s"})
        if prompt is not None:
            secondary.append({"label": "Prompt", "value": f"{prompt:.1f} tok/s"})
        if metrics is not None:
            deferred = int(metrics.get("requests_deferred", 0))
            if deferred:
                secondary.append({"label": "Waiting", "value": deferred})
        kept: dict[str, float] = {}
        if busy is not None:
            kept["busy"] = float(busy)
        if generation is not None:
            kept["tokens_per_second"] = generation
        return WidgetData(
            status="ok",
            primary={"label": "Busy slots", "value": f"{busy} / {total}" if busy is not None else total},
            secondary=secondary,
            metrics=kept,
        )

    @staticmethod
    def _slot_list(slots: list[dict[str, Any]] | None) -> WidgetData:
        if slots is None:
            raise AdapterError("llama-server does not list its slots.", code="not_supported",
                               hint="Start it without --no-slots.")
        items = []
        for one in sorted(slots, key=lambda slot: int(_number(slot.get("id")) or 0)):
            working = bool(one.get("is_processing"))
            context = _number(one.get("n_ctx"))
            items.append({
                "title": f"Slot {int(_number(one.get('id')) or 0)}",
                "subtitle": f"{int(context)} tokens of context" if context else "",
                # Busy is what a slot is for, not something to look at.
                "status": "ok",
                "value": "Working" if working else "Idle",
            })
        busy = sum(1 for one in slots if one.get("is_processing"))
        return WidgetData(status="ok", items=items, secondary=[{"label": "Busy", "value": f"{busy} / {len(slots)}"}],
                          meta={"empty": "The server has no slots."})

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        props = {"total_slots": 4, "model_alias": "qwen2.5-7b-instruct-q4_k_m", "endpoint_slots": True, "endpoint_metrics": True}
        busy = int(fake.walk("llamacpp-busy", tick, 0, 3.9))
        slots = [{"id": index, "n_ctx": 8192, "is_processing": index < busy} for index in range(4)]
        if widget_kind == "slots":
            return self._slot_list(slots)
        generated = 180_000.0 + tick * 95
        metrics = {
            "tokens_predicted_total": generated,
            "tokens_predicted_seconds_total": generated / fake.walk("llamacpp-gen", tick, 38, 46),
            "prompt_tokens_total": 950_000.0 + tick * 400,
            "prompt_seconds_total": (950_000.0 + tick * 400) / fake.walk("llamacpp-prompt", tick, 410, 520),
            "requests_deferred": 0.0,
        }
        return self._server(props, slots, metrics)


ADAPTER = LlamaCppAdapter()
