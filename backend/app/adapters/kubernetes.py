"""Kubernetes: which node is out, which pod is not running, what is degraded.

The cluster's own API with a bearer token, read-only throughout: nodes, pods,
deployments, the version, and the node metrics when metrics-server is there.
A ``ClusterRole`` of ``view`` covers all of it; nothing here writes.

    kubectl create serviceaccount hexdeck -n kube-system
    kubectl create clusterrolebinding hexdeck --clusterrole=view \\
        --serviceaccount=kube-system:hexdeck
    kubectl create token hexdeck -n kube-system --duration=87600h

⚠️ The API server's certificate is signed by the cluster's own authority, which
nothing outside trusts. Either hand HexDeck that authority or switch the TLS
check off for this connection, knowingly.

⚠️ Quantities are not numbers. CPU arrives as ``250m`` or ``1`` or
``123456789n``, memory as ``2Gi`` or ``1024Ki`` or ``1000M``, and the suffixes
are binary and decimal side by side. Reading them as floats gives a cluster
using a quarter of a core a load of 250.

⚠️ Metrics are optional. Without metrics-server the cluster answers 404 on the
metrics API, which is not a fault, so the cards drop the usage rows and say
nothing is measured instead of drawing zeroes.
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
    status_from_percent,
)

#: CPU suffixes, as multiples of one core.
CPU_UNITS = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3}
#: Memory suffixes: the binary ones Kubernetes prefers, and the decimal ones it allows.
BYTE_UNITS = {
    "": 1.0, "k": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15,
    "Ki": 1024.0, "Mi": 1024.0**2, "Gi": 1024.0**3, "Ti": 1024.0**4, "Pi": 1024.0**5,
}
#: A pod that ended on purpose is not a pod in trouble.
FINISHED = ("Succeeded",)
NODES_SECONDS = 30
PODS_SECONDS = 30
VERSION_SECONDS = 3600


def cpu_cores(written: Any) -> float | None:
    """``250m``, ``1``, ``123456789n`` as a number of cores."""
    text = str(written or "").strip()
    if not text:
        return None
    suffix = "".join(character for character in text if character.isalpha())
    number = text[: len(text) - len(suffix)] if suffix else text
    try:
        return float(number) * CPU_UNITS.get(suffix, 1.0)
    except ValueError:
        return None


def bytes_of(written: Any) -> float | None:
    """``2Gi``, ``1024Ki``, ``1000M`` as bytes."""
    text = str(written or "").strip()
    if not text:
        return None
    suffix = "".join(character for character in text if character.isalpha())
    number = text[: len(text) - len(suffix)] if suffix else text
    try:
        return float(number) * BYTE_UNITS.get(suffix, 1.0)
    except ValueError:
        return None


def share(used: float | None, whole: float | None) -> float | None:
    if used is None or not whole:
        return None
    return round(100.0 * used / whole, 1)


class KubernetesAdapter(Adapter):
    kind = "kubernetes"
    label = "Kubernetes"
    category = "hosts"
    description = "Nodes, pods and deployments of a cluster, with usage where metrics-server reports it."
    icon = "kubernetes"
    beta = True
    docs_url = "https://kubernetes.io/docs/reference/kubernetes-api/"
    fields = (
        Field("url", "URL", type="url", required=True, placeholder="https://10.0.0.10:6443",
              help="The API server. kubectl cluster-info names it."),
        Field("token", "Token", type="password", secret=True, required=True,
              help="A service account token. A ClusterRole of view is enough, and nothing here writes."),
        Field("insecure", "Ignore TLS errors", type="bool", default=False,
              help="The API server's certificate is signed by the cluster's own authority, which nothing outside the cluster trusts."),
    )
    widgets = (
        WidgetType(kind="cluster", label="Cluster", description="Nodes ready, pods running, what is degraded, and usage where it is measured.",
                   renderer="stats", default_size=(4, 2), refresh_seconds=60, metrics=("cpu", "memory")),
        WidgetType(kind="nodes", label="Nodes", description="Every node with its state, its usage and its kubelet, the unhealthy ones first.",
                   renderer="list", default_size=(3, 2), refresh_seconds=60, metrics=("ready",),
                   options=(Field("limit", "Entries", type="number", default=10),)),
        WidgetType(kind="pods", label="Pods", description="The pods that are not running, with their phase and their restarts.",
                   renderer="list", default_size=(3, 3), refresh_seconds=60, metrics=("unhealthy",),
                   options=(
                       Field("namespace", "Namespace", placeholder="default",
                             help="Empty means every namespace the token may see."),
                       Field("troubled_only", "Only what is not running", type="bool", default=True,
                             help="Off lists every pod, which on a real cluster is a long list."),
                       Field("limit", "Entries", type="number", default=12),
                   )),
        WidgetType(kind="workloads", label="Deployments", description="Every deployment with the replicas it wants and the replicas it has.",
                   renderer="list", default_size=(3, 2), refresh_seconds=120, metrics=("degraded",),
                   options=(
                       Field("namespace", "Namespace", placeholder="default"),
                       Field("limit", "Entries", type="number", default=12),
                   )),
    )

    # -- talking to the API server --------------------------------------------

    async def _get(self, config: dict[str, Any], ctx: Context, path: str, cache: float,
                   optional: bool = False) -> dict[str, Any] | None:
        response = await ctx.request(
            "GET", f"{base_url(config)}{path}",
            headers={"Authorization": f"Bearer {str(config.get('token') or '').strip()}", "Accept": "application/json"},
            verify=not config.get("insecure"), cache_seconds=cache, auth_errors=False,
        )
        if response.status_code == 401:
            raise AuthFailed("The cluster refused the token.")
        if response.status_code == 403:
            raise AuthFailed("This token may not read that. A ClusterRole of view covers every call this adapter makes.")
        if response.status_code == 404 and optional:
            # metrics-server is not installed. That is a choice, not a fault.
            return None
        if response.status_code >= 400:
            raise AdapterError(f"The API server answered with HTTP {response.status_code}.", code="http_error")
        try:
            answer = response.json()
        except ValueError as failure:
            raise AdapterError("The API server did not answer with JSON.", code="not_json",
                               hint="Is this the API server's port, 6443 by default, rather than an ingress?") from failure
        if not isinstance(answer, dict):
            raise AdapterError("That address answers, but not the way Kubernetes does.", code="not_kubernetes")
        return answer

    @staticmethod
    def _items(answer: dict[str, Any] | None) -> list[dict[str, Any]]:
        return [one for one in (answer or {}).get("items") or [] if isinstance(one, dict)]

    async def _nodes(self, config: dict[str, Any], ctx: Context) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        nodes = self._items(await self._get(config, ctx, "/api/v1/nodes", NODES_SECONDS))
        measured_by_name: dict[str, dict[str, Any]] = {}
        for one in self._items(await self._get(config, ctx, "/apis/metrics.k8s.io/v1beta1/nodes", NODES_SECONDS, optional=True)):
            name = str((one.get("metadata") or {}).get("name") or "")
            if name and isinstance(one.get("usage"), dict):
                measured_by_name[name] = one["usage"]
        return nodes, measured_by_name

    async def _pods(self, config: dict[str, Any], ctx: Context, namespace: str) -> list[dict[str, Any]]:
        path = f"/api/v1/namespaces/{namespace}/pods" if namespace else "/api/v1/pods"
        return self._items(await self._get(config, ctx, path, PODS_SECONDS))

    async def _deployments(self, config: dict[str, Any], ctx: Context, namespace: str) -> list[dict[str, Any]]:
        path = f"/apis/apps/v1/namespaces/{namespace}/deployments" if namespace else "/apis/apps/v1/deployments"
        return self._items(await self._get(config, ctx, path, PODS_SECONDS))

    async def test(self, config: dict[str, Any], ctx: Context) -> str:
        version = await self._get(config, ctx, "/version", 0) or {}
        if not version.get("gitVersion"):
            raise AdapterError("That address answers, but not the way Kubernetes does.", code="not_kubernetes")
        nodes, usage = await self._nodes(config, ctx)
        measuring = "with metrics" if usage else "without metrics-server"
        return f"Kubernetes {version['gitVersion']} answers with {len(nodes)} nodes, {measuring}."

    async def fetch(self, widget_kind: str, config: dict[str, Any], options: dict[str, Any], ctx: Context) -> WidgetData:
        namespace = str(options.get("namespace") or "").strip()
        if widget_kind == "pods":
            return self._pod_list(await self._pods(config, ctx, namespace), options)
        if widget_kind == "workloads":
            return self._workloads(await self._deployments(config, ctx, namespace), options)
        nodes, usage = await self._nodes(config, ctx)
        if widget_kind == "nodes":
            return self._node_list(nodes, usage, options)
        version = await self._get(config, ctx, "/version", VERSION_SECONDS) or {}
        return self._cluster(nodes, usage, await self._pods(config, ctx, ""), version)

    # -- reading one object ---------------------------------------------------

    @staticmethod
    def _node_state(node: dict[str, Any]) -> tuple[str, str]:
        """``(status, why)`` for one node, in the cluster's own words."""
        conditions = [one for one in (node.get("status") or {}).get("conditions") or [] if isinstance(one, dict)]
        ready = next((one for one in conditions if one.get("type") == "Ready"), {})
        # ⚠️ A cordoned node is Ready and takes no new pods. Calling it healthy
        # hides a node somebody is about to drain.
        if (node.get("spec") or {}).get("unschedulable"):
            return "warn", "Cordoned"
        if str(ready.get("status")) != "True":
            return "bad", str(ready.get("reason") or "NotReady")
        pressed = [str(one.get("type")) for one in conditions
                   if str(one.get("type", "")).endswith("Pressure") and str(one.get("status")) == "True"]
        if pressed:
            return "warn", ", ".join(pressed)
        return "ok", "Ready"

    @classmethod
    def _pod_state(cls, pod: dict[str, Any]) -> tuple[str, str, int]:
        """``(status, why, restarts)``, where a finished job is not a failure."""
        status = pod.get("status") or {}
        phase = str(status.get("phase") or "Unknown")
        containers = [one for one in status.get("containerStatuses") or [] if isinstance(one, dict)]
        restarts = sum(int(one.get("restartCount") or 0) for one in containers)
        waiting = next((str((one.get("state") or {}).get("waiting", {}).get("reason") or "")
                        for one in containers if (one.get("state") or {}).get("waiting")), "")
        if phase in FINISHED:
            return "ok", phase, restarts
        if phase == "Failed":
            return "bad", str(status.get("reason") or phase), restarts
        if waiting in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError"):
            return "bad", waiting, restarts
        if phase != "Running":
            return "warn", waiting or phase, restarts
        if containers and not all(one.get("ready") for one in containers):
            return "warn", "Not ready", restarts
        return "ok", "Running", restarts

    # -- the cards -----------------------------------------------------------

    @classmethod
    def _cluster(cls, nodes: list[dict[str, Any]], usage: dict[str, dict[str, Any]],
                 pods: list[dict[str, Any]], version: dict[str, Any]) -> WidgetData:
        states = [cls._node_state(one)[0] for one in nodes]
        ready = sum(1 for one in states if one == "ok")
        judged = [cls._pod_state(one) for one in pods]
        # ⚠️ A pod that has Succeeded is well and is not running, so it counts
        # towards neither. Counting it as running makes a cluster full of
        # finished jobs look busier than it is.
        running = sum(1 for state, why, _restarts in judged if state == "ok" and why == "Running")
        broken = sum(1 for state, _why, _restarts in judged if state == "bad")
        cpu_used = sum(cpu_cores(one.get("cpu")) or 0.0 for one in usage.values()) if usage else None
        memory_used = sum(bytes_of(one.get("memory")) or 0.0 for one in usage.values()) if usage else None
        cpu_whole = sum(cpu_cores(((one.get("status") or {}).get("allocatable") or {}).get("cpu")) or 0.0 for one in nodes)
        memory_whole = sum(bytes_of(((one.get("status") or {}).get("allocatable") or {}).get("memory")) or 0.0 for one in nodes)
        cpu_share, memory_share = share(cpu_used, cpu_whole), share(memory_used, memory_whole)
        secondary: list[dict[str, Any]] = [{"label": "Nodes", "value": f"{ready} / {len(nodes)}"}]
        if cpu_share is not None:
            secondary.append({"label": "CPU", "value": cpu_share, "unit": "%", "metric": "cpu",
                              "hint": f"{cpu_used:.1f} of {cpu_whole:.0f} cores"})
        if memory_share is not None:
            secondary.append({"label": "Memory", "value": memory_share, "unit": "%", "metric": "memory",
                              "hint": f"{human_bytes(memory_used)} of {human_bytes(memory_whole)}"})
        if broken:
            secondary.append({"label": "Failing", "value": broken})
        if version.get("gitVersion"):
            secondary.append({"label": "Version", "value": str(version["gitVersion"])})
        worst = max([value for value in (cpu_share, memory_share) if value is not None], default=None)
        return WidgetData(
            status="bad" if broken or ready < len(nodes) else status_from_percent(worst),
            primary={"label": "Pods", "value": f"{running} / {len(pods)}"},
            secondary=secondary,
            metrics=measured({"cpu": cpu_share, "memory": memory_share}),
            meta={"notice": "" if usage else "No usage is shown: this cluster has no metrics-server."},
        )

    @classmethod
    def _node_list(cls, nodes: list[dict[str, Any]], usage: dict[str, dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        order = {"bad": 0, "warn": 1, "ok": 2}
        rows = []
        for node in nodes:
            name = str((node.get("metadata") or {}).get("name") or "?")
            state, why = cls._node_state(node)
            allocatable = (node.get("status") or {}).get("allocatable") or {}
            measured_here = usage.get(name) or {}
            cpu_share = share(cpu_cores(measured_here.get("cpu")), cpu_cores(allocatable.get("cpu")))
            memory_share = share(bytes_of(measured_here.get("memory")), bytes_of(allocatable.get("memory")))
            parts = [why] if why != "Ready" else []
            if cpu_share is not None:
                parts.append(f"cpu {cpu_share:.0f}%")
            if memory_share is not None:
                parts.append(f"memory {memory_share:.0f}%")
            kubelet = str(((node.get("status") or {}).get("nodeInfo") or {}).get("kubeletVersion") or "")
            rows.append({"state": state, "row": {
                "title": name,
                "subtitle": " · ".join(parts),
                "value": kubelet,
                "status": state,
                "progress": memory_share if memory_share is not None else None,
            }})
        ranked = sorted(rows, key=lambda one: (order.get(one["state"], 3), one["row"]["title"]))
        ready = sum(1 for one in rows if one["state"] == "ok")
        return WidgetData(
            status="bad" if any(one["state"] == "bad" for one in rows) else "warn" if ready < len(rows) else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 10))],
            primary={"label": "Ready", "value": f"{ready} / {len(rows)}"},
            metrics=measured({"ready": float(ready)}),
            meta={"empty": "This cluster reports no nodes."},
        )

    @classmethod
    def _pod_list(cls, pods: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        order = {"bad": 0, "warn": 1, "ok": 2}
        rows = []
        for pod in pods:
            metadata = pod.get("metadata") or {}
            state, why, restarts = cls._pod_state(pod)
            if options.get("troubled_only", True) and state == "ok":
                continue
            rows.append({"state": state, "restarts": restarts, "row": {
                "title": str(metadata.get("name") or "?"),
                "subtitle": " · ".join(part for part in (str(metadata.get("namespace") or ""),
                                                         f"{restarts} restarts" if restarts else "") if part),
                "value": why,
                "status": state,
            }})
        ranked = sorted(rows, key=lambda one: (order.get(one["state"], 3), -one["restarts"], one["row"]["title"]))
        unhealthy = sum(1 for one in rows if one["state"] in ("bad", "warn"))
        return WidgetData(
            status="bad" if any(one["state"] == "bad" for one in rows) else "warn" if unhealthy else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 12))],
            primary={"label": "Not running", "value": unhealthy},
            metrics=measured({"unhealthy": float(unhealthy)}),
            meta={"empty": "Every pod is running."},
        )

    @classmethod
    def _workloads(cls, deployments: list[dict[str, Any]], options: dict[str, Any]) -> WidgetData:
        rows = []
        for deployment in deployments:
            metadata, spec, status = deployment.get("metadata") or {}, deployment.get("spec") or {}, deployment.get("status") or {}
            wanted = int(spec.get("replicas") or 0)
            have = int(status.get("readyReplicas") or 0)
            # ⚠️ Nought wanted is scaled to nothing on purpose, not degraded.
            state = "ok" if have >= wanted else "bad" if have == 0 and wanted else "warn"
            rows.append({"state": state, "row": {
                "title": str(metadata.get("name") or "?"),
                "subtitle": str(metadata.get("namespace") or ""),
                "value": f"{have} / {wanted}",
                "status": "unknown" if not wanted else state,
                "progress": round(100.0 * have / wanted, 1) if wanted else None,
            }})
        order = {"bad": 0, "warn": 1, "ok": 2}
        ranked = sorted(rows, key=lambda one: (order.get(one["state"], 3), one["row"]["title"]))
        degraded = sum(1 for one in rows if one["state"] in ("bad", "warn"))
        return WidgetData(
            status="bad" if any(one["state"] == "bad" for one in rows) else "warn" if degraded else "ok",
            items=[one["row"] for one in ranked][: max(1, int(options.get("limit") or 12))],
            primary={"label": "Degraded", "value": degraded},
            secondary=[{"label": "Deployments", "value": len(rows)}],
            metrics=measured({"degraded": float(degraded)}),
            meta={"empty": "This namespace has no deployments."},
        )

    def demo(self, widget_kind: str, options: dict[str, Any], tick: int) -> WidgetData:
        nodes = [
            {"metadata": {"name": "k3s-1"}, "spec": {}, "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "allocatable": {"cpu": "4", "memory": "8Gi"}, "nodeInfo": {"kubeletVersion": "v1.31.4+k3s1"}}},
            {"metadata": {"name": "k3s-2"}, "spec": {"unschedulable": True}, "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "allocatable": {"cpu": "4", "memory": "8Gi"}, "nodeInfo": {"kubeletVersion": "v1.31.4+k3s1"}}},
        ]
        usage = {
            "k3s-1": {"cpu": f"{int(fake.walk('k8s-cpu', tick, 400, 2600))}m", "memory": "3200Mi"},
            "k3s-2": {"cpu": "820m", "memory": "2100Mi"},
        }
        pods = [
            {"metadata": {"name": "immich-server-0", "namespace": "media"}, "status": {
                "phase": "Running", "containerStatuses": [{"ready": True, "restartCount": 0}]}},
            {"metadata": {"name": "paperless-7c9", "namespace": "docs"}, "status": {
                "phase": "Running", "containerStatuses": [{"ready": False, "restartCount": 3, "state": {}}]}},
            {"metadata": {"name": "backup-cron-28r", "namespace": "ops"}, "status": {
                "phase": "Succeeded", "containerStatuses": [{"ready": False, "restartCount": 0}]}},
            {"metadata": {"name": "grafana-5f4", "namespace": "monitoring"}, "status": {
                "phase": "Pending", "containerStatuses": [{"ready": False, "restartCount": 7,
                                                           "state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}},
        ]
        if widget_kind == "nodes":
            return self._node_list(nodes, usage, options)
        if widget_kind == "pods":
            return self._pod_list(pods, options)
        if widget_kind == "workloads":
            return self._workloads([
                {"metadata": {"name": "immich-server", "namespace": "media"}, "spec": {"replicas": 1}, "status": {"readyReplicas": 1}},
                {"metadata": {"name": "grafana", "namespace": "monitoring"}, "spec": {"replicas": 1}, "status": {}},
                {"metadata": {"name": "old-thing", "namespace": "ops"}, "spec": {"replicas": 0}, "status": {}},
            ], options)
        return self._cluster(nodes, usage, pods, {"gitVersion": "v1.31.4+k3s1"})


ADAPTER = KubernetesAdapter()
