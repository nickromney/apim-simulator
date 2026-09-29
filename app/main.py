from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import GatewayConfig, load_config, validate_policy_config
from app.gateway_errors import GatewayError, gateway_error_handler
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


def _build_oidc_verifiers(cfg: GatewayConfig) -> dict[str, OIDCVerifier]:
    """One verifier per configured provider, or a single default provider.

    `oidc_providers` and `oidc` are alternative spellings of the same thing;
    the plural form wins when both are present.
    """
    if cfg.oidc_providers:
        return {
            provider_id: OIDCVerifier(
                provider.issuer,
                provider.audience,
                jwks_uri=provider.jwks_uri,
                jwks=provider.jwks,
            )
            for provider_id, provider in cfg.oidc_providers.items()
        }
    if cfg.oidc is not None:
        return {
            "default": OIDCVerifier(
                cfg.oidc.issuer,
                cfg.oidc.audience,
                jwks_uri=cfg.oidc.jwks_uri,
                jwks=cfg.oidc.jwks,
            )
        }
    return {}


@dataclass(frozen=True)
class _ConfigFingerprint:
    """What the watcher compares between polls.

    A Kubernetes ConfigMap is mounted as a symlink that is swapped on update, so
    the mtime of the path can stay put while the content changes. Both halves
    have to be watched, and neither on its own is enough.
    """

    mtime: float = 0.0
    target: str = ""

    @classmethod
    def read(cls, path: Path) -> _ConfigFingerprint | None:
        """The current fingerprint, or None if the path cannot be read."""
        try:
            if not path.exists():
                return None
            return cls(
                mtime=path.stat().st_mtime,
                target=str(path.resolve()) if path.is_symlink() else "",
            )
        except OSError:
            return None

    def succeeds(self, previous: _ConfigFingerprint) -> bool:
        """True when this reading means the file changed since `previous`.

        An empty target is "not a symlink", not "the symlink went away", so it
        never counts as a change on its own.
        """
        if self.mtime != previous.mtime:
            return True
        return bool(self.target) and self.target != previous.target


async def _watch_config(
    config_path: str,
    *,
    resolve_manager: Callable[[], ManagementService | None],
    interval: float = 5.0,
) -> None:
    """Reload the tenant document whenever its file changes on disk."""
    path = Path(config_path)
    seen = _ConfigFingerprint.read(path) or _ConfigFingerprint()
    logger.info("config watcher started | path=%s | interval=%.1fs", config_path, interval)

    while True:
        await asyncio.sleep(interval)
        try:
            current = _ConfigFingerprint.read(path)
            if current is None or not current.succeeds(seen):
                continue
            seen = current
            logger.info("config file changed, reloading...")
            manager = resolve_manager()
            if manager is None:
                logger.warning("config watcher skipped reload because management service was unavailable")
                continue
            manager.reload_config()
        except Exception as exc:  # noqa: BLE001 - a watcher must outlive one bad reload
            logger.warning("config watcher error: %s", exc)


@dataclass(frozen=True)
class _WatchSettings:
    """How the environment asks for config watching."""

    path: str
    enabled: bool
    interval: float

    @classmethod
    def from_env(cls) -> _WatchSettings:
        return cls(
            path=os.getenv("APIM_CONFIG_PATH", "").strip(),
            enabled=os.getenv("APIM_CONFIG_WATCH", "false").lower() == "true",
            interval=float(os.getenv("APIM_CONFIG_WATCH_INTERVAL", "5")),
        )

    @property
    def active(self) -> bool:
        return bool(self.path) and self.enabled


def _install_request_state(request: Request, correlation_id: str) -> None:
    """Seed the per-request fields the pipeline and the access log both read."""
    request.state.correlation_id = correlation_id
    request.state.apim_cache_result = "none"
    request.state.apim_backend_id = "none"
    request.state.apim_upstream_attempts = 0
    request.state.apim_trace_requested = False
    request.state.apim_result_reason = None


def _reset_runtime_stores(app: FastAPI) -> None:
    """Give the app the empty per-process stores the pipeline expects."""
    app.state.cache = {}
    app.state.policy_cache = {}
    app.state.policy_response_cache = {}
    app.state.policy_value_cache = {}
    app.state.rate_limit_store = {}
    app.state.quota_store = {}
    app.state.trace_store = {}
    app.state.backend_health = {}


def _build_gateway_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:  # noqa: C901 - one branch per route registered, not per decision; see docs/complexity.md
    """The gateway's own endpoints: health, probes, reload, traces, identity."""
    router = APIRouter()

    @router.get("/")
    async def root_hint(request: Request) -> dict[str, Any]:
        cfg: GatewayConfig = request.app.state.gateway_config
        route_prefixes = sorted({route.path_prefix or "/" for route in cfg.routes})
        operator_console_url = os.getenv("OPERATOR_CONSOLE_URL", "http://localhost:3007")
        return {
            "service": cfg.service.display_name,
            "message": (
                "This is an API gateway. Try /apim/health, /apim/startup, or one of the configured route prefixes."
            ),
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

    @router.get("/apim/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @router.get("/apim/startup")
    async def startup(request: Request) -> dict[str, str]:
        """Startup probe: 200 only once the app is ready to serve traffic."""
        if not getattr(request.app.state, "startup_complete", False):
            raise HTTPException(status_code=503, detail="Starting up")
        return {"status": "started"}

    @router.post("/apim/reload")
    async def reload_config(request: Request) -> dict[str, Any]:
        """Reload configuration from file. Requires the admin token when one is set."""
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

    @router.get("/apim/trace/{trace_id}")
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

    @router.get("/apim/user")
    async def current_user(request: Request) -> dict:
        cfg: GatewayConfig = request.app.state.gateway_config
        verifiers: dict[str, OIDCVerifier] = request.app.state.oidc_verifiers
        auth = authenticate_request(request, cfg, verifiers)
        return build_user_payload(auth, None, None)

    @router.post("/apim/admin/subscriptions/{subscription_id}/rotate")
    async def rotate_subscription_key(subscription_id: str, request: Request, key: str = "secondary") -> dict:
        require_admin(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        updated, new_key = require_management_plane().rotate_subscription_key(cfg, subscription_id, key)
        sub = updated.subscription.find_by_id(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"subscription_id": sub.id, "subscription_name": sub.name, "rotated": key, "new_key": new_key}

    return router


def _build_catch_all_router() -> APIRouter:  # noqa: C901 - one branch per route registered, not per decision; see docs/complexity.md
    """The gateway proxy itself.

    Registered last on purpose: it matches every path, so anything included
    after it is unreachable. tests/test_app_composition.py asserts the order.
    """
    router = APIRouter()

    @router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def gateway_proxy(full_path: str, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204)
        try:
            return await execute_gateway_request(request)
        except GatewayError:
            raise
        except HTTPException as exc:
            raise GatewayError.from_http_exception(exc) from exc

    return router


def _add_observability_middleware(app: FastAPI, telemetry: ObservabilityRuntime) -> None:
    """Record a metric and one access log line for every request, success or not."""

    @app.middleware("http")
    async def observe_requests(request: Request, call_next):
        correlation_id = request.headers.get("x-correlation-id") or f"corr-{uuid.uuid4()}"
        _install_request_state(request, correlation_id)
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
            if request.app.state.gateway_config.emit_simulator_response_headers:
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


def _build_lifespan(
    *,
    gateway_config: GatewayConfig,
    telemetry: ObservabilityRuntime,
    http_client: httpx.AsyncClient | None,
    require_management_plane: Callable[[], ManagementService],
    resolve_manager: Callable[[], ManagementService | None],
):
    """Startup and shutdown: the HTTP client, the runtime stores, the watcher."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owns_client = http_client is None
        app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0)) if owns_client else http_client
        instrument_httpx_client(app.state.http_client, telemetry)

        manager = require_management_plane()
        manager.apply_runtime_config(gateway_config)
        _reset_runtime_stores(app)
        app.state.config_reload_fn = manager.reload_config
        app.state.startup_complete = True

        watch = _WatchSettings.from_env()
        watcher_task: asyncio.Task | None = None
        if watch.active:
            watcher_task = asyncio.create_task(
                _watch_config(watch.path, resolve_manager=resolve_manager, interval=watch.interval)
            )

        logger.info(
            "apim-sim ready | routes=%d | origins=%s | anonymous=%s | watch=%s",
            len(gateway_config.routes),
            gateway_config.allowed_origins,
            gateway_config.allow_anonymous,
            watch.enabled,
        )
        yield

        if watcher_task is not None:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        if owns_client:
            await app.state.http_client.aclose()

    return lifespan


def _add_cors_middleware(app: FastAPI, gateway_config: GatewayConfig) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=gateway_config.allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-apim-simulator", "x-apim-trace-id", "x-correlation-id", "x-todo-demo-policy"],
    )


def _require_manager(manager: ManagementService | None) -> ManagementService:
    """The management plane, or a 500 saying it never came up.

    create_app builds the FastAPI app before the ManagementService, because the
    service needs the app. Every route that reaches for the service therefore
    goes through here rather than assuming it exists.
    """
    if manager is None:
        raise HTTPException(status_code=500, detail="Management service not initialized")
    return manager


def create_app(*, config: GatewayConfig | None = None, http_client: httpx.AsyncClient | None = None) -> FastAPI:
    """Compose the gateway: config, telemetry, middleware, then routers in order.

    Router order is load-bearing. The catch-all proxy matches every path, so it
    is included last and everything else has to be included before it.
    """
    telemetry = configure_observability(service_name=APIM_SERVICE_NAME, service_version=APIM_SERVICE_VERSION)
    gateway_config = validate_policy_config(config or load_config())
    gateway_config.routes = gateway_config.materialize_routes()

    management_plane: ManagementService | None = None

    def _resolve_manager() -> ManagementService | None:
        return management_plane

    def _require_management_plane() -> ManagementService:
        return _require_manager(management_plane)

    app = FastAPI(
        title="Local APIM Simulator",
        version=APIM_SERVICE_VERSION,
        lifespan=_build_lifespan(
            gateway_config=gateway_config,
            telemetry=telemetry,
            http_client=http_client,
            require_management_plane=_require_management_plane,
            resolve_manager=_resolve_manager,
        ),
    )
    app.add_exception_handler(GatewayError, gateway_error_handler)
    management_plane = ManagementService(
        app=app,
        serialize_gateway_config=_serialize_gateway_config,
        build_oidc_verifiers=_build_oidc_verifiers,
    )
    app.state.telemetry = telemetry
    app.state.gateway_metrics = _get_gateway_metrics(telemetry)

    _add_cors_middleware(app, gateway_config)
    _add_observability_middleware(app, telemetry)

    app.include_router(_build_gateway_router(require_management_plane=_require_management_plane))
    app.include_router(build_management_router(require_management_plane=_require_management_plane))
    app.include_router(_build_catch_all_router())

    instrument_fastapi_app(app, telemetry)
    return app


app = create_app()
