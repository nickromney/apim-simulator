from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import GatewayConfig, load_config
from app.management_api import build_management_router
from app.management_service import ManagementService
from app.proxy import build_user_payload
from app.request_pipeline import (
    APIM_BACKEND_ID_ATTR,
    APIM_CACHE_RESULT_ATTR,
    APIM_RESULT_REASON_ATTR,
    APIM_ROUTE_NAME_ATTR,
    APIM_TRACE_REQUESTED_ATTR,
    APIM_UPSTREAM_ATTEMPTS_ATTR,
    cached_gateway_response,
    execute_gateway_request,
)
from app.security import OIDCVerifier, authenticate_request, require_admin
from app.telemetry import (
    ObservabilityRuntime,
    configure_observability,
    get_correlation_id,
    instrument_fastapi_app,
    instrument_httpx_client,
    reset_correlation_id,
    set_correlation_id,
)

logger = logging.getLogger("apim-simulator")

APIM_SERVICE_NAME = "apim-simulator"
APIM_SERVICE_VERSION = "0.4.0"
_GATEWAY_METRICS: GatewayMetrics | None = None


@dataclass(frozen=True)
class GatewayMetrics:
    requests: Any
    request_duration: Any
    upstream_duration: Any
    cache_events: Any
    policy_short_circuits: Any
    config_reloads: Any
    llm_tokens: Any
    custom_metrics: Any


def _serialize_gateway_config(cfg: GatewayConfig) -> str:
    payload = cfg.model_dump(mode="json")
    if payload.get("apis"):
        payload["routes"] = []
    return json.dumps(payload, indent=2) + "\n"


_cached_gateway_response = cached_gateway_response


def _get_gateway_metrics(telemetry: ObservabilityRuntime) -> GatewayMetrics:
    global _GATEWAY_METRICS
    if _GATEWAY_METRICS is not None:
        return _GATEWAY_METRICS

    meter = telemetry.meter
    _GATEWAY_METRICS = GatewayMetrics(
        requests=meter.create_counter(
            "apim.gateway.requests",
            description="Count of requests handled by the APIM simulator gateway",
        ),
        request_duration=meter.create_histogram(
            "apim.gateway.request.duration",
            unit="s",
            description="End-to-end gateway request duration",
        ),
        upstream_duration=meter.create_histogram(
            "apim.gateway.upstream.duration",
            unit="s",
            description="Duration spent waiting on upstream backends",
        ),
        cache_events=meter.create_counter(
            "apim.gateway.cache.events",
            description="Gateway response cache outcomes",
        ),
        policy_short_circuits=meter.create_counter(
            "apim.gateway.policy.short_circuits",
            description="Requests terminated by inbound or backend APIM policy stages",
        ),
        config_reloads=meter.create_counter(
            "apim.gateway.config.reloads",
            description="Gateway config reload attempts",
        ),
        llm_tokens=meter.create_counter(
            "apim.llm.tokens",
            unit="{token}",
            description="LLM tokens observed by llm-emit-token-metric policies",
        ),
        custom_metrics=meter.create_counter(
            "apim.policy.metric",
            description="Custom metrics emitted by emit-metric policies",
        ),
    )
    return _GATEWAY_METRICS


def _request_route_label(request: Request) -> str:
    apim_route_name = getattr(request.state, "apim_route_name", None)
    if apim_route_name:
        return apim_route_name

    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if route_path:
        return str(route_path)
    return request.url.path


def _request_client_ip(request: Request) -> str:
    state_value = getattr(request.state, "apim_client_ip", None)
    if state_value:
        return state_value
    if request.client is not None:
        return request.client.host
    return ""


def _request_observation_attrs(request: Request, status_code: int) -> dict[str, str | int | bool]:
    return {
        "http.request.method": request.method,
        "http.response.status_code": status_code,
        "http.route": _request_route_label(request),
        APIM_ROUTE_NAME_ATTR: getattr(request.state, "apim_route_name", "none"),
        APIM_CACHE_RESULT_ATTR: getattr(request.state, "apim_cache_result", "none"),
        APIM_BACKEND_ID_ATTR: getattr(request.state, "apim_backend_id", "none"),
        APIM_TRACE_REQUESTED_ATTR: bool(getattr(request.state, "apim_trace_requested", False)),
    }


def _record_request_observation(request: Request, *, status_code: int, duration_seconds: float) -> None:
    metrics: GatewayMetrics = request.app.state.gateway_metrics
    attrs = _request_observation_attrs(request, status_code)
    metrics.requests.add(1, attrs)
    metrics.request_duration.record(duration_seconds, attrs)

    upstream_duration = getattr(request.state, "apim_upstream_duration_seconds", None)
    if upstream_duration is not None:
        metrics.upstream_duration.record(upstream_duration, attrs)


def _access_log_fields(request: Request, *, status_code: int, duration_seconds: float) -> dict[str, Any]:
    return {
        "event.name": "http.request.completed",
        "http.request.method": request.method,
        "url.path": request.url.path,
        "http.route": _request_route_label(request),
        "http.response.status_code": status_code,
        "duration_ms": round(duration_seconds * 1000, 3),
        "network.client.ip": _request_client_ip(request),
        "correlation_id": get_correlation_id() or getattr(request.state, "correlation_id", None),
        APIM_ROUTE_NAME_ATTR: getattr(request.state, "apim_route_name", None),
        APIM_BACKEND_ID_ATTR: getattr(request.state, "apim_backend_id", None),
        APIM_CACHE_RESULT_ATTR: getattr(request.state, "apim_cache_result", None),
        APIM_TRACE_REQUESTED_ATTR: getattr(request.state, "apim_trace_requested", False),
        APIM_UPSTREAM_ATTEMPTS_ATTR: getattr(request.state, "apim_upstream_attempts", None),
        APIM_RESULT_REASON_ATTR: getattr(request.state, "apim_result_reason", None),
    }


def create_app(*, config: GatewayConfig | None = None, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    telemetry = configure_observability(service_name=APIM_SERVICE_NAME, service_version=APIM_SERVICE_VERSION)
    gateway_config = config or load_config()
    gateway_config.routes = gateway_config.materialize_routes()

    def _build_oidc_verifiers(cfg: GatewayConfig) -> dict[str, OIDCVerifier]:
        verifiers: dict[str, OIDCVerifier] = {}
        if cfg.oidc_providers:
            for provider_id, provider in cfg.oidc_providers.items():
                verifiers[provider_id] = OIDCVerifier(
                    provider.issuer,
                    provider.audience,
                    jwks_uri=provider.jwks_uri,
                    jwks=provider.jwks,
                )
        elif cfg.oidc is not None:
            verifiers["default"] = OIDCVerifier(
                cfg.oidc.issuer,
                cfg.oidc.audience,
                jwks_uri=cfg.oidc.jwks_uri,
                jwks=cfg.oidc.jwks,
            )
        return verifiers

    management_plane: ManagementService | None = None

    def _require_management_plane() -> ManagementService:
        if management_plane is None:
            raise HTTPException(status_code=500, detail="Management service not initialized")
        return management_plane

    async def _config_watcher(app: FastAPI, config_path: str, interval: float = 5.0) -> None:
        """Watch config file for changes and reload when modified.

        Kubernetes ConfigMaps are mounted as symlinks that change on update.
        We track both mtime and resolved symlink target to detect changes.
        """
        path = Path(config_path)
        last_mtime: float = 0
        last_target: str = ""

        try:
            if path.exists():
                last_mtime = path.stat().st_mtime
                last_target = str(path.resolve()) if path.is_symlink() else ""
        except OSError:
            pass

        logger.info("config watcher started | path=%s | interval=%.1fs", config_path, interval)

        while True:
            await asyncio.sleep(interval)
            try:
                if not path.exists():
                    continue

                current_mtime = path.stat().st_mtime
                current_target = str(path.resolve()) if path.is_symlink() else ""

                changed = False
                if current_mtime != last_mtime:
                    changed = True
                    last_mtime = current_mtime
                if current_target and current_target != last_target:
                    changed = True
                    last_target = current_target

                if changed:
                    logger.info("config file changed, reloading...")
                    manager = management_plane
                    if manager is None:
                        logger.warning("config watcher skipped reload because management service was unavailable")
                        continue
                    manager.reload_config()
            except Exception as exc:
                logger.warning("config watcher error: %s", exc)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        created = False
        if http_client is None:
            app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
            created = True
        else:
            app.state.http_client = http_client
        instrument_httpx_client(app.state.http_client, telemetry)
        manager = _require_management_plane()
        manager.apply_runtime_config(gateway_config)
        app.state.cache = {}
        app.state.policy_cache = {}
        app.state.policy_response_cache = {}
        app.state.policy_value_cache = {}
        app.state.rate_limit_store = {}
        app.state.quota_store = {}
        app.state.trace_store = {}
        app.state.backend_health = {}
        app.state.config_reload_fn = manager.reload_config
        app.state.startup_complete = True

        watcher_task: asyncio.Task | None = None
        config_path = os.getenv("APIM_CONFIG_PATH", "").strip()
        watch_enabled = os.getenv("APIM_CONFIG_WATCH", "false").lower() == "true"
        watch_interval = float(os.getenv("APIM_CONFIG_WATCH_INTERVAL", "5"))

        if config_path and watch_enabled:
            watcher_task = asyncio.create_task(_config_watcher(app, config_path, watch_interval))

        logger.info(
            "apim-sim ready | routes=%d | origins=%s | anonymous=%s | watch=%s",
            len(gateway_config.routes),
            gateway_config.allowed_origins,
            gateway_config.allow_anonymous,
            watch_enabled,
        )
        yield
        if watcher_task:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        if created:
            await app.state.http_client.aclose()

    app = FastAPI(title="Local APIM Simulator", version=APIM_SERVICE_VERSION, lifespan=lifespan)
    management_plane = ManagementService(
        app=app,
        serialize_gateway_config=_serialize_gateway_config,
        build_oidc_verifiers=_build_oidc_verifiers,
    )
    app.state.telemetry = telemetry
    app.state.gateway_metrics = _get_gateway_metrics(telemetry)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=gateway_config.allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-apim-simulator", "x-apim-trace-id", "x-correlation-id", "x-todo-demo-policy"],
    )

    @app.middleware("http")
    async def observe_requests(request: Request, call_next):
        correlation_id = request.headers.get("x-correlation-id") or f"corr-{uuid.uuid4()}"
        request.state.correlation_id = correlation_id
        request.state.apim_cache_result = "none"
        request.state.apim_backend_id = "none"
        request.state.apim_upstream_attempts = 0
        request.state.apim_trace_requested = False
        request.state.apim_result_reason = None
        token = set_correlation_id(correlation_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_seconds = time.perf_counter() - start
            _record_request_observation(request, status_code=500, duration_seconds=duration_seconds)
            telemetry.logger.exception(
                "request failed",
                extra=_access_log_fields(request, status_code=500, duration_seconds=duration_seconds),
            )
            raise
        else:
            response.headers.setdefault("x-correlation-id", correlation_id)
            duration_seconds = time.perf_counter() - start
            _record_request_observation(request, status_code=response.status_code, duration_seconds=duration_seconds)
            telemetry.logger.info(
                "request completed",
                extra=_access_log_fields(request, status_code=response.status_code, duration_seconds=duration_seconds),
            )
            return response
        finally:
            reset_correlation_id(token)

    @app.get("/")
    async def root_hint(request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        route_prefixes = sorted({route.path_prefix or "/" for route in cfg.routes})
        operator_console_url = os.getenv("OPERATOR_CONSOLE_URL", "http://localhost:3007")
        return {
            "service": cfg.service.display_name,
            "message": "This is an API gateway. Try /apim/health, /apim/startup, or one of the configured route prefixes.",
            "gateway_endpoints": ["/apim/health", "/apim/startup"],
            "route_prefixes": route_prefixes,
            "management": {
                "enabled": cfg.tenant_access.enabled,
                "status_path": "/apim/management/status" if cfg.tenant_access.enabled else None,
                "required_header": "X-Apim-Tenant-Key" if cfg.tenant_access.enabled else None,
            },
            "operator_console": {
                "url": operator_console_url,
                "note": "Run make up-ui to start the operator console.",
            },
        }

    @app.get("/apim/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/apim/startup")
    async def startup(request: Request) -> dict[str, str]:
        """Startup probe endpoint - returns 200 once app is ready to serve traffic."""
        if not getattr(request.app.state, "startup_complete", False):
            raise HTTPException(status_code=503, detail="Starting up")
        return {"status": "started"}

    @app.post("/apim/reload")
    async def reload_config(request: Request) -> dict[str, Any]:
        """Reload configuration from file. Requires admin token if configured."""
        cfg: GatewayConfig = request.app.state.gateway_config
        if cfg.admin_token:
            require_admin(request)
        reload_fn = getattr(request.app.state, "config_reload_fn", None)
        if reload_fn is None:
            raise HTTPException(status_code=500, detail="Reload not available")
        new_cfg = reload_fn()
        return {
            "status": "reloaded",
            "routes": len(new_cfg.routes),
            "products": len(new_cfg.products),
            "subscriptions": len(new_cfg.subscription.subscriptions),
        }

    @app.get("/apim/trace/{trace_id}")
    async def get_trace(trace_id: str, request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        if not cfg.trace_enabled:
            raise HTTPException(status_code=404, detail="Not found")
        if cfg.admin_token:
            require_admin(request)

        trace_store: dict[str, Any] = request.app.state.trace_store
        entry = trace_store.get(trace_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Not found")
        return entry

    @app.get("/apim/user")
    async def current_user(request: Request) -> dict:
        cfg: GatewayConfig = request.app.state.gateway_config
        verifiers: dict[str, OIDCVerifier] = request.app.state.oidc_verifiers
        auth = authenticate_request(request, cfg, verifiers)
        return build_user_payload(auth, None, None)

    @app.post("/apim/admin/subscriptions/{subscription_id}/rotate")
    async def rotate_subscription_key(subscription_id: str, request: Request, key: str = "secondary") -> dict:
        require_admin(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated, new_key = _require_management_plane().rotate_subscription_key(cfg, subscription_id, key)
        sub = updated.subscription.find_by_id(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"subscription_id": sub.id, "subscription_name": sub.name, "rotated": key, "new_key": new_key}

    app.include_router(build_management_router(require_management_plane=_require_management_plane))

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def gateway_proxy(full_path: str, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204)
        return await execute_gateway_request(request)

    instrument_fastapi_app(app, telemetry)
    return app


app = create_app()
