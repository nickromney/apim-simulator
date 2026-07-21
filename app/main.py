from __future__ import annotations

import asyncio
import base64
import hashlib
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
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.config import (
    BackendCircuitBreakerConfig,
    BackendConfig,
    GatewayConfig,
    ProductState,
    load_config,
)
from app.management_api import build_management_router
from app.management_service import ManagementService
from app.named_values import mask_secret_data
from app.policy import (
    PolicyRequest,
    PolicyRuntime,
    PolicyTraceCollector,
    apply_backend_async,
    apply_inbound_async,
    apply_on_error_async,
    apply_outbound_async,
    finalize_deferred_actions,
    parse_policies_xml,
)
from app.proxy import build_upstream_headers, build_user_payload, filter_response_headers, resolve_route
from app.request_auth import (
    _extract_roles,
    _extract_scopes,
    _find_subscription_by_id,
    _require_admin,
)
from app.security import (
    OIDCVerifier,
    authenticate_request,
    build_client_principal,
    subscription_bypassed,
    validate_client_certificate,
)
from app.telemetry import (
    ObservabilityRuntime,
    configure_observability,
    get_correlation_id,
    instrument_fastapi_app,
    instrument_httpx_client,
    reset_correlation_id,
    set_correlation_id,
    set_current_span_attributes,
)

logger = logging.getLogger("apim-simulator")

APIM_SERVICE_NAME = "apim-simulator"
APIM_SERVICE_VERSION = "0.4.0"
APIM_ROUTE_NAME_ATTR = "apim.route.name"
APIM_CACHE_RESULT_ATTR = "apim.cache.result"
APIM_BACKEND_ID_ATTR = "apim.backend.id"
APIM_TRACE_REQUESTED_ATTR = "apim.trace.requested"
APIM_RESULT_REASON_ATTR = "apim.result.reason"
APIM_UPSTREAM_ATTEMPTS_ATTR = "apim.upstream.attempts"
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


def _apply_claim_headers(headers: dict[str, str], claims: dict[str, Any]) -> None:
    headers["x-apim-user-object-id"] = str(claims.get("sub", ""))
    headers["x-apim-user-email"] = str(claims.get("email", ""))
    headers["x-apim-user-name"] = str(claims.get("name") or claims.get("preferred_username") or "")
    headers["x-apim-auth-method"] = "oidc"
    headers["x-ms-client-principal"] = build_client_principal(claims)
    headers["x-ms-client-principal-name"] = str(claims.get("preferred_username", ""))


def _trace_payload(
    *,
    trace_base: dict[str, Any],
    trace_collector: PolicyTraceCollector | None,
    cfg: GatewayConfig,
    extra: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        **trace_base,
        "policy_steps": trace_collector.steps if trace_collector else [],
        "policy_variable_writes": trace_collector.variable_writes if trace_collector else [],
        "jwt_validations": trace_collector.jwt_validations if trace_collector else [],
        "send_requests": trace_collector.send_requests if trace_collector else [],
        "selected_backend": trace_collector.selected_backend if trace_collector else None,
        **extra,
    }
    return mask_secret_data(payload, cfg)


def _request_cache_key(
    *,
    method: str,
    upstream_url: str,
    query: dict[str, str],
    authorization: str,
    subscription_key: str,
) -> str:
    payload = json.dumps(
        {
            "method": method,
            "upstream_url": upstream_url,
            "query": query,
            "authorization": authorization,
            "subscription_key": subscription_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cached_gateway_response(
    *,
    cached: tuple[float, int, dict[str, str], str | None, bytes] | None,
    request: Request,
    route_name: str,
    policy_req: PolicyRequest,
    policy_runtime: PolicyRuntime,
    trace_base: dict[str, Any],
    trace_collector: PolicyTraceCollector | None,
    cfg: GatewayConfig,
    gateway_metrics: Any,
    correlation_id: str,
    trace_id: str | None,
) -> Response | None:
    if cached is None:
        return None

    expires_at, cached_status, cached_headers, cached_media_type, cached_body = cached
    if time.time() >= expires_at:
        return None

    if not isinstance(cached_status, int) or not (100 <= cached_status <= 599):
        return None

    body_bytes = bytes(cached_body)
    out_headers = dict(cached_headers)
    media_type = (
        cached_media_type if cached_media_type is None or isinstance(cached_media_type, str) else str(cached_media_type)
    )

    request.state.apim_cache_result = "hit"
    request.state.apim_result_reason = "cache_hit"
    request.state.apim_upstream_attempts = 0
    gateway_metrics.cache_events.add(
        1,
        {
            APIM_ROUTE_NAME_ATTR: route_name,
            APIM_CACHE_RESULT_ATTR: "hit",
            "http.request.method": request.method,
        },
    )
    set_current_span_attributes(
        **{
            APIM_CACHE_RESULT_ATTR: "hit",
            APIM_RESULT_REASON_ATTR: "cache_hit",
            APIM_UPSTREAM_ATTEMPTS_ATTR: 0,
        }
    )
    final_req = PolicyRequest(
        method=policy_req.method,
        path=policy_req.path,
        query=dict(policy_req.query),
        headers=dict(policy_req.headers),
        variables=policy_req.variables,
        body=policy_req.body,
        response_status_code=cached_status,
        response_headers=out_headers,
        response_body=body_bytes,
        response_media_type=media_type,
    )
    finalize_deferred_actions(final_req, policy_runtime)
    out_headers["x-apim-cache"] = "hit"
    out_headers["x-correlation-id"] = correlation_id
    if trace_id:
        out_headers["x-apim-trace-id"] = trace_id
        trace = _trace_payload(
            trace_base=trace_base,
            trace_collector=trace_collector,
            cfg=cfg,
            extra={
                "attempts": 0,
                "status": cached_status,
                "elapsed_ms": 0,
                "cache": "hit",
            },
        )
        out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
        trace_store: dict[str, Any] = request.app.state.trace_store
        trace_store[trace_id] = {
            "trace_id": trace_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **trace,
        }
    return Response(
        content=body_bytes,
        status_code=cached_status,
        headers=out_headers,
        media_type=media_type,
    )


def _render_backend_value(value: str | None, policy_req: PolicyRequest, cfg: GatewayConfig) -> str | None:
    if value is None:
        return None
    runtime = PolicyRuntime(gateway_config=cfg)
    from app.policy import render_policy_value

    return render_policy_value(value, policy_req, runtime)


_DEFAULT_POOL_CIRCUIT_BREAKER = BackendCircuitBreakerConfig()


def _pool_member_breaker(pool_backend: BackendConfig, member_backend: BackendConfig) -> BackendCircuitBreakerConfig:
    return member_backend.circuit_breaker or pool_backend.circuit_breaker or _DEFAULT_POOL_CIRCUIT_BREAKER


def _backend_health_entry(health: dict[str, Any], backend_id: str) -> dict[str, Any]:
    entry = health.setdefault(backend_id, {"failures": [], "open_until": 0.0})
    if not isinstance(entry, dict):
        entry = {"failures": [], "open_until": 0.0}
        health[backend_id] = entry
    return entry


def _record_backend_result(
    health: dict[str, Any],
    breaker: BackendCircuitBreakerConfig,
    backend_id: str,
    *,
    now: float,
    failed: bool,
) -> None:
    entry = _backend_health_entry(health, backend_id)
    if not failed:
        entry["failures"] = []
        return
    failures = [t for t in entry.get("failures", []) if t > now - breaker.interval_seconds]
    failures.append(now)
    if len(failures) >= max(1, breaker.failure_count):
        entry["open_until"] = now + breaker.trip_duration_seconds
        entry["failures"] = []
    else:
        entry["failures"] = failures


def _select_pool_member(
    cfg: GatewayConfig,
    health: dict[str, Any],
    pool_id: str,
    pool_backend: BackendConfig,
    *,
    now: float,
) -> tuple[str, BackendConfig] | None:
    members = [m for m in pool_backend.pool if cfg.backends.get(m.backend_id) is not None]
    if not members:
        return None
    rotation = _backend_health_entry(health, f"pool:{pool_id}")
    for priority in sorted({m.priority for m in members}):
        group = [m for m in members if m.priority == priority]
        # Deterministic weighted round-robin: expand by weight, then walk the
        # schedule from the pool's rotation cursor skipping open circuits.
        schedule = [m for m in group for _ in range(max(1, m.weight))]
        start = int(rotation.get(f"rr:{priority}", 0))
        for offset in range(len(schedule)):
            member = schedule[(start + offset) % len(schedule)]
            member_entry = _backend_health_entry(health, member.backend_id)
            if float(member_entry.get("open_until", 0.0)) <= now:
                rotation[f"rr:{priority}"] = (start + offset + 1) % len(schedule)
                return member.backend_id, cfg.backends[member.backend_id]
    return None


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


def _product_is_published(cfg: GatewayConfig, product_id: str) -> bool:
    product = cfg.products.get(product_id)
    if product is None:
        return True
    return product.state == ProductState.Published


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
            _require_admin(request)
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
            _require_admin(request)

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
        _require_admin(request)
        cfg: GatewayConfig = request.app.state.gateway_config
        sub = _find_subscription_by_id(cfg, subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if key not in {"primary", "secondary"}:
            raise HTTPException(status_code=400, detail="Invalid key")

        # Keep this deterministic (non-secret) so we don't accidentally commit real keys.
        new_key = f"rotated-{sub.id}-{key}"
        if key == "primary":
            sub.keys.primary = new_key
        else:
            sub.keys.secondary = new_key
        return {"subscription_id": sub.id, "subscription_name": sub.name, "rotated": key, "new_key": new_key}

    app.include_router(build_management_router(require_management_plane=_require_management_plane))

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def gateway_proxy(full_path: str, request: Request) -> Response:
        if request.method == "OPTIONS":
            return Response(status_code=204)

        cfg: GatewayConfig = request.app.state.gateway_config
        gateway_metrics: GatewayMetrics = request.app.state.gateway_metrics

        # mTLS validation (before route resolution)
        validate_client_certificate(request, cfg)

        resolved = resolve_route(cfg, request)
        if resolved is None:
            request.state.apim_result_reason = "no_route"
            raise HTTPException(status_code=404, detail="No route")
        route = resolved.route
        request.state.apim_route_name = route.name

        verifiers: dict[str, OIDCVerifier] = request.app.state.oidc_verifiers
        auth = authenticate_request(request, cfg, verifiers, route)

        if route.product:
            allowed_products = [route.product]
        else:
            allowed_products = []

        if route.products:
            allowed_products = list(route.products)

        if allowed_products:
            # Only published products participate in authorization. Products
            # missing from config are treated as published for back-compat.
            published_products = [p for p in allowed_products if _product_is_published(cfg, p)]
            if not published_products:
                request.state.apim_result_reason = "product_not_published"
                raise HTTPException(status_code=403, detail="Product is not published")
            require_sub = any(
                (cfg.products.get(p).require_subscription if cfg.products.get(p) else True) for p in published_products
            )
            if require_sub and subscription_bypassed(request, cfg):
                require_sub = False
            if require_sub:
                if auth.subscription is None:
                    request.state.apim_result_reason = "missing_subscription"
                    raise HTTPException(status_code=401, detail="Missing subscription key")
                granted = set(auth.subscription_products)
                if not set(published_products).intersection(granted):
                    if set(allowed_products).intersection(granted):
                        request.state.apim_result_reason = "product_not_published"
                        raise HTTPException(status_code=403, detail="Product is not published")
                    request.state.apim_result_reason = "subscription_not_authorized"
                    raise HTTPException(status_code=403, detail="Subscription not authorized for product")

        # Azure applies policies at product scope for the product the call is
        # authorized under. Adapted rule: prefer the first published product
        # granted by the subscription, else the first published product on the
        # route (open products and subscription bypass).
        effective_product_id = ""
        if allowed_products:
            published = [p for p in allowed_products if _product_is_published(cfg, p)]
            if auth.subscription is not None:
                granted = set(auth.subscription_products)
                effective_product_id = next((p for p in published if p in granted), "")
            if not effective_product_id and published:
                effective_product_id = published[0]

        set_current_span_attributes(
            **{
                APIM_ROUTE_NAME_ATTR: route.name,
                "apim.route.path_prefix": route.path_prefix,
                "apim.subscription.present": auth.subscription is not None,
                "apim.allowed_products.count": len(allowed_products),
                "apim.product.effective": effective_product_id,
            }
        )

        policy_docs: list[Any] = []
        policy_cache: dict[str, Any] = request.app.state.policy_cache

        def _doc_for(xml: str) -> Any:
            cache_key = (xml, tuple(sorted(cfg.policy_fragments.items())))
            cached = policy_cache.get(cache_key)
            if cached is not None:
                return cached
            doc = parse_policies_xml(xml, policy_fragments=cfg.policy_fragments)
            policy_cache[cache_key] = doc
            return doc

        for xml in cfg.policies_xml_documents:
            policy_docs.append(_doc_for(xml))
        if cfg.policies_xml:
            policy_docs.append(_doc_for(cfg.policies_xml))
        effective_product = cfg.products.get(effective_product_id) if effective_product_id else None
        if effective_product is not None and effective_product.policies_xml:
            policy_docs.append(_doc_for(effective_product.policies_xml))
        for xml in route.policies_xml_documents:
            policy_docs.append(_doc_for(xml))
        if route.policies_xml:
            policy_docs.append(_doc_for(route.policies_xml))

        body = await request.body()
        if len(body) > cfg.max_request_body_bytes:
            request.state.apim_result_reason = "request_body_too_large"
            raise HTTPException(status_code=413, detail="Request body too large")
        headers = {k.lower(): v for k, v in build_upstream_headers(request, auth).items()}

        correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get("x-correlation-id")
        headers.setdefault("x-correlation-id", correlation_id)

        incoming_host = request.headers.get("host", "")
        forwarded_host = request.headers.get("x-forwarded-host", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        forwarded_for = request.headers.get("x-forwarded-for", "")
        client_ip = (
            forwarded_for.split(",", 1)[0].strip() if forwarded_for else (request.client.host if request.client else "")
        )
        request.state.apim_client_ip = client_ip
        subscription_record = _find_subscription_by_id(cfg, auth.subscription.id) if auth.subscription else None
        subscription_owner = subscription_record.created_by if subscription_record is not None else None
        subscription_groups = (
            sorted(
                group.id for group in cfg.groups.values() if subscription_owner and subscription_owner in group.users
            )
            if subscription_owner
            else []
        )

        upstream_path = resolved.upstream_path
        upstream_query = dict(request.query_params)
        policy_req = PolicyRequest(
            method=request.method,
            path=upstream_path,
            query=upstream_query,
            headers=headers,
            variables={
                "route": route.name,
                "api_id": route.api_id or "",
                "operation_id": route.operation_id or "",
                "subscription_id": auth.subscription.id if auth.subscription else "",
                "products": auth.subscription_products,
                "product_id": effective_product_id,
                "client_ip": client_ip,
                "correlation_id": correlation_id,
                "incoming_host": incoming_host,
                "forwarded_host": forwarded_host,
                "forwarded_proto": forwarded_proto,
                "forwarded_for": forwarded_for,
                "subscription_owner": subscription_owner or "",
                "subscription_groups": subscription_groups,
                "rate_limit_store": request.app.state.rate_limit_store,
                "quota_store": request.app.state.quota_store,
                "original_request_url": str(request.url),
                "_request_headers": dict(headers),
                "_request_query": dict(upstream_query),
            },
            body=body,
        )

        trace_requested = cfg.trace_enabled and request.headers.get("x-apim-trace", "").lower() == "true"
        request.state.apim_trace_requested = trace_requested
        trace_id = f"trace-{int(time.time() * 1000)}" if trace_requested else None
        trace_collector = PolicyTraceCollector() if trace_requested else None
        client: httpx.AsyncClient = request.app.state.http_client
        policy_runtime = PolicyRuntime(
            gateway_config=cfg,
            http_client=client,
            timeout_seconds=cfg.proxy_timeout_seconds,
            trace=trace_collector,
            response_cache=request.app.state.policy_response_cache,
            value_cache=request.app.state.policy_value_cache,
            llm_metric_emitter=lambda amount, attributes: gateway_metrics.llm_tokens.add(amount, attributes),
            custom_metric_emitter=lambda amount, attributes: gateway_metrics.custom_metrics.add(amount, attributes),
        )

        set_current_span_attributes(
            **{
                "apim.trace.requested": trace_requested,
                "apim.subscription.authorized": auth.subscription is not None,
            }
        )

        def _store_trace(payload: dict[str, Any]) -> None:
            if not trace_id:
                return
            trace_store: dict[str, Any] = request.app.state.trace_store
            trace_store[trace_id] = {
                "trace_id": trace_id,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **payload,
            }

        def _finalize_policy_response(
            *,
            status_code: int,
            headers: dict[str, str],
            body_bytes: bytes = b"",
            media_type: str | None = None,
        ) -> None:
            final_req = PolicyRequest(
                method=policy_req.method,
                path=policy_req.path,
                query=dict(policy_req.query),
                headers=dict(policy_req.headers),
                variables=policy_req.variables,
                body=policy_req.body,
                response_status_code=status_code,
                response_headers=headers,
                response_body=body_bytes,
                response_media_type=media_type,
            )
            finalize_deferred_actions(final_req, policy_runtime)

        trace_base = {
            "route": route.name,
            "correlation_id": correlation_id,
            "incoming_host": incoming_host,
            "forwarded_host": forwarded_host,
            "forwarded_proto": forwarded_proto,
            "forwarded_for": forwarded_for,
            "client_ip": client_ip,
            "upstream_url": None,
        }

        if policy_docs:
            early = await apply_inbound_async(policy_docs, policy_req, policy_runtime)
            if early is not None:
                request.state.apim_result_reason = "policy_inbound_short_circuit"
                request.state.apim_upstream_attempts = 0
                gateway_metrics.policy_short_circuits.add(
                    1,
                    {
                        APIM_ROUTE_NAME_ATTR: route.name,
                        "apim.policy.stage": "inbound",
                        "http.request.method": request.method,
                    },
                )
                set_current_span_attributes(
                    **{
                        APIM_RESULT_REASON_ATTR: "policy_inbound_short_circuit",
                        APIM_UPSTREAM_ATTEMPTS_ATTR: 0,
                    }
                )
                out_headers = dict(early.headers)
                _finalize_policy_response(
                    status_code=early.status_code,
                    headers=out_headers,
                    body_bytes=early.body,
                    media_type=early.media_type,
                )
                out_headers["x-apim-simulator"] = "apim-sim-full"
                out_headers["x-correlation-id"] = correlation_id
                if trace_id:
                    out_headers["x-apim-trace-id"] = trace_id
                    trace = _trace_payload(
                        trace_base=trace_base,
                        trace_collector=trace_collector,
                        cfg=cfg,
                        extra={
                            "upstream_url": None,
                            "attempts": 0,
                            "status": early.status_code,
                            "elapsed_ms": 0,
                            "cache": None,
                            "reason": "policy_inbound_short_circuit",
                        },
                    )
                    out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                    _store_trace(trace)
                return Response(
                    content=early.body,
                    status_code=early.status_code,
                    headers=out_headers,
                    media_type=early.media_type,
                )

            backend_early = await apply_backend_async(policy_docs, policy_req, policy_runtime)
            if backend_early is not None:
                request.state.apim_result_reason = "policy_backend_short_circuit"
                request.state.apim_upstream_attempts = 0
                gateway_metrics.policy_short_circuits.add(
                    1,
                    {
                        APIM_ROUTE_NAME_ATTR: route.name,
                        "apim.policy.stage": "backend",
                        "http.request.method": request.method,
                    },
                )
                set_current_span_attributes(
                    **{
                        APIM_RESULT_REASON_ATTR: "policy_backend_short_circuit",
                        APIM_UPSTREAM_ATTEMPTS_ATTR: 0,
                    }
                )
                out_headers = dict(backend_early.headers)
                _finalize_policy_response(
                    status_code=backend_early.status_code,
                    headers=out_headers,
                    body_bytes=backend_early.body,
                    media_type=backend_early.media_type,
                )
                out_headers["x-apim-simulator"] = "apim-sim-full"
                out_headers["x-correlation-id"] = correlation_id
                if trace_id:
                    out_headers["x-apim-trace-id"] = trace_id
                    trace = _trace_payload(
                        trace_base=trace_base,
                        trace_collector=trace_collector,
                        cfg=cfg,
                        extra={
                            "upstream_url": None,
                            "attempts": 0,
                            "status": backend_early.status_code,
                            "elapsed_ms": 0,
                            "cache": None,
                            "reason": "policy_backend_short_circuit",
                        },
                    )
                    out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                    _store_trace(trace)
                return Response(
                    content=backend_early.body,
                    status_code=backend_early.status_code,
                    headers=out_headers,
                    media_type=backend_early.media_type,
                )

        effective_claims = auth.claims
        jwt_claims = policy_req.variables.get("_last_jwt_claims")
        if isinstance(jwt_claims, dict):
            effective_claims = jwt_claims
            _apply_claim_headers(policy_req.headers, effective_claims)

        if route.authz is not None:
            scopes = _extract_scopes(effective_claims)
            roles = _extract_roles(effective_claims)
            if route.authz.required_scopes and not set(route.authz.required_scopes).issubset(scopes):
                request.state.apim_result_reason = "missing_required_scope"
                raise HTTPException(status_code=403, detail="Missing required scope")
            if route.authz.required_roles and not set(route.authz.required_roles).issubset(roles):
                request.state.apim_result_reason = "missing_required_role"
                raise HTTPException(status_code=403, detail="Missing required role")
            for key, expected in route.authz.required_claims.items():
                actual = effective_claims.get(key)
                if actual is None or str(actual) != expected:
                    request.state.apim_result_reason = "missing_required_claim"
                    raise HTTPException(status_code=403, detail="Missing required claim")

        upstream_base_url = route.upstream_base_url
        upstream_auth: tuple[str, str] | None = None
        selected_backend_url = str(policy_req.variables.get("selected_backend_url") or "")
        selected_backend_id = str(policy_req.variables.get("selected_backend_id") or "")
        backend_id = selected_backend_id or (route.backend or "" if not selected_backend_url else "")
        if selected_backend_url:
            upstream_base_url = selected_backend_url
        pool_backend: BackendConfig | None = None
        pool_backend_id = ""
        backend_health: dict[str, Any] = request.app.state.backend_health
        if backend_id:
            backend = cfg.backends.get(backend_id)
            if backend is not None and (backend.type or "single").lower() == "pool":
                pool_backend = backend
                pool_backend_id = backend_id
                selection = _select_pool_member(cfg, backend_health, pool_backend_id, pool_backend, now=time.time())
                if selection is None:
                    request.state.apim_result_reason = "backend_pool_exhausted"
                    raise HTTPException(status_code=503, detail="All backend pool members are unavailable")
                backend_id, backend = selection
                policy_req.headers["x-apim-backend-pool"] = pool_backend_id
            if backend is not None:
                upstream_base_url = selected_backend_url or (
                    _render_backend_value(backend.url, policy_req, cfg) or backend.url
                )
                policy_req.headers.setdefault("x-apim-backend-id", backend_id)

                auth_type = (backend.auth_type or "none").lower()
                if auth_type == "basic":
                    username = _render_backend_value(backend.basic_username, policy_req, cfg)
                    password = _render_backend_value(backend.basic_password, policy_req, cfg)
                    if "authorization" not in policy_req.headers and username and password:
                        upstream_auth = (username, password)
                elif auth_type == "managed_identity":
                    policy_req.headers.setdefault("x-apim-managed-identity", "true")
                    if backend.managed_identity_resource:
                        policy_req.headers.setdefault(
                            "x-apim-managed-identity-resource",
                            _render_backend_value(backend.managed_identity_resource, policy_req, cfg),
                        )
                elif auth_type == "client_certificate":
                    policy_req.headers.setdefault("x-apim-client-certificate", "present")

                if (
                    backend.authorization_scheme
                    and backend.authorization_parameter
                    and "authorization" not in policy_req.headers
                ):
                    scheme = _render_backend_value(backend.authorization_scheme, policy_req, cfg) or ""
                    parameter = _render_backend_value(backend.authorization_parameter, policy_req, cfg) or ""
                    policy_req.headers["authorization"] = f"{scheme} {parameter}".strip()

                for header_name, header_value in backend.header_credentials.items():
                    rendered = _render_backend_value(header_value, policy_req, cfg)
                    if rendered is not None:
                        policy_req.headers[header_name.lower()] = rendered

                for query_name, query_value in backend.query_credentials.items():
                    rendered = _render_backend_value(query_value, policy_req, cfg)
                    if rendered is not None:
                        policy_req.query[query_name] = rendered

                if backend.client_certificate_thumbprints:
                    policy_req.headers.setdefault(
                        "x-apim-client-certificate-thumbprints",
                        ",".join(backend.client_certificate_thumbprints),
                    )

        request.state.apim_backend_id = backend_id or "direct"
        set_current_span_attributes(
            **{
                APIM_BACKEND_ID_ATTR: request.state.apim_backend_id,
                "apim.policy.documents": len(policy_docs),
            }
        )

        if trace_collector is not None and trace_collector.selected_backend is None:
            trace_collector.selected_backend = {
                "backend_id": backend_id or None,
                "base_url": upstream_base_url,
            }

        upstream_url = route.build_upstream_url(policy_req.path, upstream_base_url=upstream_base_url)
        policy_req.variables["upstream_url"] = upstream_url

        trace_base["upstream_url"] = upstream_url

        policy_response_cache_active = bool(policy_req.variables.get("_policy_response_cache_active"))
        cache_key = None
        if (
            cfg.cache_enabled
            and (request.method == "GET")
            and (not cfg.proxy_streaming)
            and not policy_response_cache_active
        ):
            authz = request.headers.get("authorization", "")
            sub_key = request.headers.get("ocp-apim-subscription-key", "")
            cache_key = _request_cache_key(
                method=request.method,
                upstream_url=upstream_url,
                query=policy_req.query,
                authorization=authz,
                subscription_key=sub_key,
            )
            cached = request.app.state.cache.get(cache_key)
            if cached is not None:
                cached_response = _cached_gateway_response(
                    cached=cached,
                    request=request,
                    route_name=route.name,
                    policy_req=policy_req,
                    policy_runtime=policy_runtime,
                    trace_base=trace_base,
                    trace_collector=trace_collector,
                    cfg=cfg,
                    gateway_metrics=gateway_metrics,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                )
                if cached_response is not None:
                    return cached_response
                request.app.state.cache.pop(cache_key, None)

        timeout = httpx.Timeout(cfg.proxy_timeout_seconds)
        max_attempts = max(1, cfg.proxy_max_attempts)
        last_exc: Exception | None = None
        upstream_response: httpx.Response | None = None
        start = time.perf_counter()
        attempts_used = 0

        def _pool_failover() -> None:
            # Record the failure for the current member and rotate to another
            # healthy member for the next attempt. Pool members are assumed to
            # share auth configuration; only the base URL is recomputed.
            nonlocal backend_id, backend, upstream_base_url, upstream_url
            if pool_backend is None or backend is None:
                return
            now = time.time()
            breaker = _pool_member_breaker(pool_backend, backend)
            _record_backend_result(backend_health, breaker, backend_id, now=now, failed=True)
            reselected = _select_pool_member(cfg, backend_health, pool_backend_id, pool_backend, now=now)
            if reselected is None:
                return
            backend_id, backend = reselected
            upstream_base_url = _render_backend_value(backend.url, policy_req, cfg) or backend.url
            upstream_url = route.build_upstream_url(policy_req.path, upstream_base_url=upstream_base_url)
            policy_req.headers["x-apim-backend-id"] = backend_id

        for attempt in range(1, max_attempts + 1):
            attempts_used = attempt
            req = client.build_request(
                request.method,
                upstream_url,
                content=policy_req.body,
                headers=policy_req.headers,
                params=policy_req.query,
                timeout=timeout,
            )
            try:
                upstream_response = await client.send(req, stream=cfg.proxy_streaming, auth=upstream_auth)
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    _pool_failover()
                    break
                _pool_failover()
                continue

            if upstream_response.status_code in cfg.proxy_retry_statuses and attempt < max_attempts:
                await upstream_response.aclose()
                upstream_response = None
                _pool_failover()
                continue
            break

        if pool_backend is not None and backend is not None and upstream_response is not None:
            breaker = _pool_member_breaker(pool_backend, backend)
            _record_backend_result(
                backend_health,
                breaker,
                backend_id,
                now=time.time(),
                failed=upstream_response.status_code in breaker.error_statuses,
            )

        elapsed_seconds = time.perf_counter() - start
        request.state.apim_upstream_attempts = attempts_used

        if upstream_response is None:
            request.state.apim_result_reason = "upstream_unavailable"
            request.state.apim_upstream_duration_seconds = elapsed_seconds
            set_current_span_attributes(
                **{
                    APIM_RESULT_REASON_ATTR: "upstream_unavailable",
                    APIM_UPSTREAM_ATTEMPTS_ATTR: attempts_used,
                }
            )
            if policy_docs:
                failure_req = PolicyRequest(
                    method=request.method,
                    path=policy_req.path,
                    query=dict(policy_req.query),
                    headers=dict(policy_req.headers),
                    variables={**policy_req.variables, "error": "upstream_unavailable"},
                )
                override = await apply_on_error_async(policy_docs, failure_req, policy_runtime)
                if override is not None:
                    request.state.apim_result_reason = "policy_on_error_override"
                    out_headers = dict(override.headers)
                    _finalize_policy_response(
                        status_code=override.status_code,
                        headers=out_headers,
                        body_bytes=override.body,
                        media_type=override.media_type,
                    )
                    out_headers["x-apim-simulator"] = "apim-sim-full"
                    out_headers["x-correlation-id"] = correlation_id
                    if trace_id:
                        out_headers["x-apim-trace-id"] = trace_id
                        trace = _trace_payload(
                            trace_base=trace_base,
                            trace_collector=trace_collector,
                            cfg=cfg,
                            extra={
                                "attempts": attempts_used,
                                "status": override.status_code,
                                "elapsed_ms": int(elapsed_seconds * 1000),
                                "cache": None,
                                "reason": "policy_on_error_override",
                            },
                        )
                        out_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode(
                            "utf-8"
                        )
                        _store_trace(trace)
                    return Response(
                        content=override.body,
                        status_code=override.status_code,
                        headers=out_headers,
                        media_type=override.media_type,
                    )
            logger.exception("Unable to reach upstream", exc_info=last_exc)
            raise HTTPException(status_code=502, detail="Backend API unavailable")

        response_headers = filter_response_headers(dict(upstream_response.headers))
        media_type = upstream_response.headers.get("content-type")
        response_headers["x-correlation-id"] = correlation_id
        if pool_backend is not None:
            response_headers["x-apim-backend-pool"] = pool_backend_id
            response_headers["x-apim-backend-id"] = backend_id
        request.state.apim_upstream_duration_seconds = elapsed_seconds
        upstream_status_code = int(upstream_response.status_code)
        if not (100 <= upstream_status_code <= 599):
            raise HTTPException(status_code=502, detail="Backend API returned invalid status code")
        policy_buffering_required = bool(policy_req.variables.get("_policy_response_buffering_required"))
        requires_buffering = (
            cache_key is not None
            or policy_response_cache_active
            or policy_buffering_required
            or not cfg.proxy_streaming
        )
        content = b""
        if requires_buffering:
            content = await upstream_response.aread()
            await upstream_response.aclose()

        if policy_docs:
            outbound_req = PolicyRequest(
                method=request.method,
                path=policy_req.path,
                query=dict(policy_req.query),
                headers=response_headers,
                variables=policy_req.variables,
                body=policy_req.body,
                response_status_code=upstream_status_code,
                response_headers=response_headers,
                response_body=content,
                response_media_type=media_type,
            )
            await apply_outbound_async(policy_docs, outbound_req, policy_runtime)
            response_headers = outbound_req.headers
            content = outbound_req.response_body
            media_type = outbound_req.response_media_type or media_type

        _finalize_policy_response(
            status_code=upstream_status_code,
            headers=response_headers,
            body_bytes=content,
            media_type=media_type,
        )

        if cache_key is not None:
            request.state.apim_cache_result = "miss"
            request.state.apim_result_reason = "upstream_response"
            gateway_metrics.cache_events.add(
                1,
                {
                    APIM_ROUTE_NAME_ATTR: route.name,
                    APIM_CACHE_RESULT_ATTR: "miss",
                    "http.request.method": request.method,
                },
            )
            set_current_span_attributes(
                **{
                    APIM_CACHE_RESULT_ATTR: "miss",
                    APIM_RESULT_REASON_ATTR: "upstream_response",
                    APIM_UPSTREAM_ATTEMPTS_ATTR: attempts_used,
                }
            )
            response_headers["x-apim-cache"] = "miss"
            if len(request.app.state.cache) >= cfg.cache_max_entries:
                request.app.state.cache.clear()
            request.app.state.cache[cache_key] = (
                time.time() + cfg.cache_ttl_seconds,
                upstream_status_code,
                dict(response_headers),
                media_type,
                content,
            )
            if trace_requested:
                elapsed_ms = int(elapsed_seconds * 1000)
                trace = _trace_payload(
                    trace_base=trace_base,
                    trace_collector=trace_collector,
                    cfg=cfg,
                    extra={
                        "attempts": attempts_used,
                        "status": upstream_status_code,
                        "elapsed_ms": elapsed_ms,
                        "cache": "miss",
                    },
                )
                response_headers["x-apim-trace-id"] = trace_id
                response_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                _store_trace(trace)
            return Response(
                content=content,
                status_code=upstream_status_code,
                headers=response_headers,
                media_type=media_type,
            )

        if cfg.proxy_streaming and not requires_buffering:
            request.state.apim_result_reason = "upstream_stream"
            set_current_span_attributes(
                **{
                    APIM_RESULT_REASON_ATTR: "upstream_stream",
                    APIM_UPSTREAM_ATTEMPTS_ATTR: attempts_used,
                }
            )
            if trace_requested:
                elapsed_ms = int(elapsed_seconds * 1000)
                trace = _trace_payload(
                    trace_base=trace_base,
                    trace_collector=trace_collector,
                    cfg=cfg,
                    extra={
                        "attempts": attempts_used,
                        "status": upstream_status_code,
                        "elapsed_ms": elapsed_ms,
                        "cache": None,
                    },
                )
                response_headers["x-apim-trace-id"] = trace_id
                response_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
                _store_trace(trace)
            return StreamingResponse(
                upstream_response.aiter_bytes(),
                status_code=upstream_status_code,
                headers=response_headers,
                media_type=media_type,
                background=BackgroundTask(upstream_response.aclose),
            )

        request.state.apim_result_reason = "upstream_response"
        set_current_span_attributes(
            **{
                APIM_RESULT_REASON_ATTR: "upstream_response",
                APIM_UPSTREAM_ATTEMPTS_ATTR: attempts_used,
            }
        )
        if trace_requested:
            elapsed_ms = int(elapsed_seconds * 1000)
            trace = _trace_payload(
                trace_base=trace_base,
                trace_collector=trace_collector,
                cfg=cfg,
                extra={
                    "attempts": attempts_used,
                    "status": upstream_status_code,
                    "elapsed_ms": elapsed_ms,
                    "cache": None,
                },
            )
            response_headers["x-apim-trace-id"] = trace_id
            response_headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
            _store_trace(trace)
        return Response(
            content=content,
            status_code=upstream_status_code,
            headers=response_headers,
            media_type=media_type,
        )

    instrument_fastapi_app(app, telemetry)
    return app


app = create_app()
