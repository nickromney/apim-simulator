"""Local metrics, activity/resource logs and alert-rule evaluation."""

from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.management_service import ManagementService


class AlertRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    metric: Literal["requests", "failed_requests"] = "failed_requests"
    threshold: int = Field(default=0, ge=0)
    window_seconds: int = Field(default=300, ge=1, le=86400)
    enabled: bool = True
    action_group: str = Field(default="local-operators", min_length=1, max_length=200)


class DiagnosticSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gateway_logs: bool = False
    sampling_percentage: float = Field(default=100, ge=0, le=100)


class MonitoringConfig(BaseModel):
    diagnostics: DiagnosticSettings = Field(default_factory=DiagnosticSettings)
    alert_rules: dict[str, AlertRule] = Field(default_factory=dict)


class MonitoringStore:
    """Bounded per-process observations, retained across configuration edits."""

    def __init__(self) -> None:
        self.requests: deque[dict[str, Any]] = deque(maxlen=5000)
        self.activity_logs: deque[dict[str, Any]] = deque(maxlen=1000)
        self.resource_logs: deque[dict[str, Any]] = deque(maxlen=1000)
        self.alerts: deque[dict[str, Any]] = deque(maxlen=1000)
        # Application Insights / Azure Monitor records shaped by APIM diagnostics.
        self.telemetry: deque[dict[str, Any]] = deque(maxlen=2000)
        self.fired_rules: set[str] = set()
        self.total_requests = 0
        self.status_counts: Counter[str] = Counter()
        self.total_duration_ms = 0.0
        # Detailed requests are capped separately from the alert window counts.
        # One-second buckets retain the longest supported rule window.
        self.request_buckets: dict[int, Counter[str]] = {}
        self.bucket_seconds: deque[int] = deque()

    def _prune_buckets(self, now: float) -> None:
        cutoff = int(now) - 86400
        while self.bucket_seconds and self.bucket_seconds[0] < cutoff:
            del self.request_buckets[self.bucket_seconds.popleft()]

    def _count_window(self, metric: str, *, now: float, window_seconds: int) -> int:
        cutoff = int(now) - window_seconds
        return sum(counts[metric] for second, counts in self.request_buckets.items() if second >= cutoff)

    def evaluate(self, rules: dict[str, AlertRule]) -> None:
        now = time.time()
        self._prune_buckets(now)
        self.fired_rules.intersection_update(rules)
        for rule_id, rule in rules.items():
            value = self._count_window(rule.metric, now=now, window_seconds=rule.window_seconds)
            firing = rule.enabled and value > rule.threshold
            was_firing = rule_id in self.fired_rules
            if firing != was_firing:
                self.alerts.append(
                    {
                        "rule_id": rule_id,
                        "name": rule.name,
                        "state": "Fired" if firing else "Resolved",
                        "value": value,
                        "action_group": rule.action_group,
                        "time": datetime.now(UTC).isoformat(),
                    }
                )
            if firing:
                self.fired_rules.add(rule_id)
            else:
                self.fired_rules.discard(rule_id)

    def record(
        self,
        request: Request,
        *,
        status_code: int,
        duration_seconds: float,
        response_headers: Any = None,
    ) -> None:
        path = request.url.path
        event = {
            "timestamp": time.time(),
            "time": datetime.now(UTC).isoformat(),
            "method": request.method,
            "path": path,
            "status_code": status_code,
            "duration_ms": round(duration_seconds * 1000, 3),
            "api_id": getattr(request.state, "apim_api_id", None),
            "correlation_id": getattr(request.state, "correlation_id", None),
        }
        if path.startswith("/apim/"):
            if path.startswith("/apim/management/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                self.activity_logs.append(event)
            return
        if path in {"/", "/docs", "/redoc", "/openapi.json"}:
            return
        self.requests.append(event)
        self.total_requests += 1
        self.status_counts[str(status_code)] += 1
        self.total_duration_ms += event["duration_ms"]
        second = int(event["timestamp"])
        if second not in self.request_buckets:
            self.request_buckets[second] = Counter()
            self.bucket_seconds.append(second)
        self.request_buckets[second]["requests"] += 1
        if status_code >= 400:
            self.request_buckets[second]["failed_requests"] += 1
        settings = request.app.state.gateway_config.monitoring
        diagnostic = settings.diagnostics
        # Stable local sampling; logging never reads or consumes request bodies.
        if diagnostic.gateway_logs and (self.total_requests * 37) % 100 < diagnostic.sampling_percentage:
            self.resource_logs.append(event)
        self._record_diagnostic_telemetry(request, event, response_headers)
        self.evaluate(settings.alert_rules)

    def _record_diagnostic_telemetry(self, request: Request, event: dict[str, Any], response_headers: Any) -> None:
        from app.diagnostic_logging import RequestOutcome, build_telemetry, capture_stage, diagnostics_active

        cfg = request.app.state.gateway_config
        if not diagnostics_active(cfg):
            return
        variables = getattr(request.state, "apim_policy_variables", None) or {}
        if response_headers is not None:
            capture_stage(
                request.state,
                cfg,
                "frontend_response",
                headers=response_headers,
                body=variables.get("_diagnostic_response_body"),
            )
        outcome = RequestOutcome(
            method=event["method"],
            url=str(request.url),
            status_code=event["status_code"],
            duration_ms=event["duration_ms"],
            api_id=event["api_id"],
            operation_id=getattr(request.state, "apim_operation_id", None),
            client_ip=getattr(request.state, "apim_client_ip", None),
            request_headers={key.lower(): value for key, value in request.headers.items()},
        )
        traces = variables.get("_diagnostic_traces")
        self.telemetry.extend(
            build_telemetry(
                request.state,
                cfg,
                outcome,
                counter=self.total_requests,
                traces=traces if isinstance(traces, list) else [],
            )
        )

    def metrics(self) -> dict[str, Any]:
        return {
            "requests": self.total_requests,
            "failed_requests": sum(count for status, count in self.status_counts.items() if int(status) >= 400),
            "status_counts": dict(self.status_counts),
            "average_duration_ms": self.total_duration_ms / self.total_requests if self.total_requests else 0,
        }


def build_monitoring_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:  # noqa: C901 - route registration
    from app.security import require_tenant_access

    router = APIRouter()

    @router.get("/apim/management/monitoring/metrics")
    async def metrics(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        return request.app.state.monitoring_store.metrics()

    @router.get("/apim/management/monitoring/activity-logs")
    async def activities(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        return {"items": list(request.app.state.monitoring_store.activity_logs)}

    @router.get("/apim/management/monitoring/resource-logs")
    async def resource_logs(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        return {"items": list(request.app.state.monitoring_store.resource_logs)}

    @router.get("/apim/management/monitoring/telemetry")
    async def telemetry(request: Request, api_id: str | None = None, destination: str | None = None) -> dict[str, Any]:
        require_tenant_access(request)
        items = [
            item
            for item in reversed(request.app.state.monitoring_store.telemetry)
            if (api_id is None or item.get("api_id") == api_id)
            and (destination is None or item.get("destination") == destination)
        ]
        return {"items": items}

    @router.delete("/apim/management/monitoring/telemetry")
    async def clear_telemetry(request: Request) -> dict[str, bool]:
        require_tenant_access(request)
        request.app.state.monitoring_store.telemetry.clear()
        return {"cleared": True}

    @router.put("/apim/management/monitoring/diagnostic-settings")
    async def diagnostics(body: DiagnosticSettings, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        cfg.monitoring.diagnostics = body
        updated = require_management_plane().persist_or_apply_config(cfg)
        return updated.monitoring.diagnostics.model_dump(mode="json")

    @router.get("/apim/management/monitoring/diagnostic-settings")
    async def get_diagnostics(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        return request.app.state.gateway_config.monitoring.diagnostics.model_dump(mode="json")

    @router.put("/apim/management/monitoring/alert-rules/{rule_id}")
    async def alert_rule(rule_id: str, body: AlertRule, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        cfg.monitoring.alert_rules[rule_id] = body
        updated = require_management_plane().persist_or_apply_config(cfg)
        request.app.state.monitoring_store.evaluate(updated.monitoring.alert_rules)
        return {"id": rule_id, **body.model_dump(mode="json")}

    @router.get("/apim/management/monitoring/alerts")
    async def alerts(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        store = request.app.state.monitoring_store
        store.evaluate(request.app.state.gateway_config.monitoring.alert_rules)
        return {"items": list(store.alerts), "firing_rules": sorted(store.fired_rules)}

    @router.delete("/apim/management/monitoring/alert-rules/{rule_id}")
    async def delete_rule(rule_id: str, request: Request) -> dict[str, bool]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        cfg.monitoring.alert_rules.pop(rule_id, None)
        updated = require_management_plane().persist_or_apply_config(cfg)
        request.app.state.monitoring_store.evaluate(updated.monitoring.alert_rules)
        return {"deleted": True}

    return router
