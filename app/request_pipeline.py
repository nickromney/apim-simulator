"""APIM request pipeline.

Product grant, policy stacking, backend pick, cache, retry, and trace attach
live here. ``create_app`` stays the composer; the HTTP catch-all is an adapter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

import httpx
from fastapi import HTTPException, Request, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.backend_pool import (
    apply_backend_credentials,
    pool_member_breaker,
    record_backend_result,
    render_backend_value,
    select_pool_member,
)
from app.config import GatewayConfig, ProductState, RouteConfig
from app.effective_policy import stacked_policy_xml_documents
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
from app.proxy import apply_claim_headers, build_upstream_headers, filter_response_headers, resolve_route
from app.security import AuthContext, authenticate_request, subscription_bypassed, validate_client_certificate

APIM_ROUTE_NAME_ATTR = "apim.route.name"
APIM_CACHE_RESULT_ATTR = "apim.cache.result"
APIM_BACKEND_ID_ATTR = "apim.backend.id"
APIM_TRACE_REQUESTED_ATTR = "apim.trace.requested"
APIM_RESULT_REASON_ATTR = "apim.result.reason"
APIM_UPSTREAM_ATTEMPTS_ATTR = "apim.upstream.attempts"


def extract_scopes(claims: dict) -> set[str]:
    scopes: set[str] = set()
    raw = claims.get("scope") or claims.get("scp")
    if isinstance(raw, str):
        scopes.update(s for s in raw.split() if s)
    if isinstance(raw, list):
        scopes.update(str(s) for s in raw if s)
    return scopes


def extract_roles(claims: dict) -> set[str]:
    roles: set[str] = set()
    raw = claims.get("roles")
    if isinstance(raw, str) and raw:
        roles.add(raw)
    if isinstance(raw, list):
        roles.update(str(r) for r in raw if r)

    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        rr = realm_access.get("roles")
        if isinstance(rr, list):
            roles.update(str(r) for r in rr if r)

    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict):
        for entry in resource_access.values():
            if not isinstance(entry, dict):
                continue
            cr = entry.get("roles")
            if isinstance(cr, list):
                roles.update(str(r) for r in cr if r)
    return roles


def product_is_published(cfg: GatewayConfig, product_id: str) -> bool:
    product = cfg.products.get(product_id)
    if product is None:
        return True
    return product.state == ProductState.Published


def allowed_products_for_route(route: RouteConfig) -> list[str]:
    if route.products:
        return list(route.products)
    if route.product:
        return [route.product]
    return []


def effective_product_id_for_call(
    cfg: GatewayConfig,
    allowed_products: list[str],
    auth: AuthContext,
) -> str:
    if not allowed_products:
        return ""
    published = [p for p in allowed_products if product_is_published(cfg, p)]
    if auth.subscription is not None:
        granted = set(auth.subscription_products)
        matched = next((p for p in published if p in granted), "")
        if matched:
            return matched
    if published:
        return published[0]
    return ""


def enforce_product_grant(
    cfg: GatewayConfig,
    route: RouteConfig,
    auth: AuthContext,
    *,
    subscription_is_bypassed: bool,
) -> str:
    allowed_products = allowed_products_for_route(route)
    if not allowed_products:
        return ""

    published_products = [p for p in allowed_products if product_is_published(cfg, p)]
    if not published_products:
        raise HTTPException(status_code=403, detail="Product is not published")

    require_sub = any(
        (cfg.products.get(p).require_subscription if cfg.products.get(p) else True) for p in published_products
    )
    if require_sub and subscription_is_bypassed:
        require_sub = False
    if require_sub:
        if auth.subscription is None:
            raise HTTPException(status_code=401, detail="Missing subscription key")
        granted = set(auth.subscription_products)
        if not set(published_products).intersection(granted):
            if set(allowed_products).intersection(granted):
                raise HTTPException(status_code=403, detail="Product is not published")
            raise HTTPException(status_code=403, detail="Subscription not authorized for product")

    return effective_product_id_for_call(cfg, allowed_products, auth)


def enforce_route_authz(route: RouteConfig, claims: dict[str, Any]) -> None:
    if route.authz is None:
        return
    scopes = extract_scopes(claims)
    roles = extract_roles(claims)
    if route.authz.required_scopes and not set(route.authz.required_scopes).issubset(scopes):
        raise HTTPException(status_code=403, detail="Missing required scope")
    if route.authz.required_roles and not set(route.authz.required_roles).issubset(roles):
        raise HTTPException(status_code=403, detail="Missing required role")
    for key, expected in route.authz.required_claims.items():
        actual = claims.get(key)
        if actual is None or str(actual) != expected:
            raise HTTPException(status_code=403, detail="Missing required claim")


def trace_payload(
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


def request_cache_key(
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


def _store_trace(trace_store: dict[str, Any], trace_id: str | None, payload: dict[str, Any]) -> None:
    if not trace_id:
        return
    trace_store[trace_id] = {
        "trace_id": trace_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **payload,
    }


def attach_trace(
    headers: dict[str, str],
    *,
    trace_id: str | None,
    trace_store: dict[str, Any],
    trace_base: dict[str, Any],
    trace_collector: PolicyTraceCollector | None,
    cfg: GatewayConfig,
    extra: dict[str, Any],
) -> None:
    if not trace_id:
        return
    trace = trace_payload(
        trace_base=trace_base,
        trace_collector=trace_collector,
        cfg=cfg,
        extra=extra,
    )
    headers["x-apim-trace-id"] = trace_id
    headers["x-apim-trace"] = base64.b64encode(json.dumps(trace).encode("utf-8")).decode("utf-8")
    _store_trace(trace_store, trace_id, trace)


def cached_gateway_response(
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
    from app.telemetry import set_current_span_attributes

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
    attach_trace(
        out_headers,
        trace_id=trace_id,
        trace_store=request.app.state.trace_store,
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
    return Response(
        content=body_bytes,
        status_code=cached_status,
        headers=out_headers,
        media_type=media_type,
    )


def _policy_response(
    *,
    body: bytes,
    status_code: int,
    headers: dict[str, str],
    media_type: str | None,
    correlation_id: str,
    trace_id: str | None,
    trace_store: dict[str, Any],
    trace_base: dict[str, Any],
    trace_collector: PolicyTraceCollector | None,
    cfg: GatewayConfig,
    extra: dict[str, Any],
    policy_req: PolicyRequest,
    policy_runtime: PolicyRuntime,
) -> Response:
    finalize_deferred_actions(
        PolicyRequest(
            method=policy_req.method,
            path=policy_req.path,
            query=dict(policy_req.query),
            headers=dict(policy_req.headers),
            variables=policy_req.variables,
            body=policy_req.body,
            response_status_code=status_code,
            response_headers=headers,
            response_body=body,
            response_media_type=media_type,
        ),
        policy_runtime,
    )
    headers["x-apim-simulator"] = "apim-sim-full"
    headers["x-correlation-id"] = correlation_id
    attach_trace(
        headers,
        trace_id=trace_id,
        trace_store=trace_store,
        trace_base=trace_base,
        trace_collector=trace_collector,
        cfg=cfg,
        extra=extra,
    )
    return Response(content=body, status_code=status_code, headers=headers, media_type=media_type)


async def execute_gateway_request(request: Request) -> Response:
    from app.telemetry import set_current_span_attributes

    cfg: GatewayConfig = request.app.state.gateway_config
    gateway_metrics = request.app.state.gateway_metrics

    validate_client_certificate(request, cfg)

    resolved = resolve_route(cfg, request)
    if resolved is None:
        request.state.apim_result_reason = "no_route"
        raise HTTPException(status_code=404, detail="No route")
    route = resolved.route
    request.state.apim_route_name = route.name

    verifiers = request.app.state.oidc_verifiers
    auth = authenticate_request(request, cfg, verifiers, route)

    allowed_products = allowed_products_for_route(route)
    try:
        effective_product_id = enforce_product_grant(
            cfg,
            route,
            auth,
            subscription_is_bypassed=subscription_bypassed(request, cfg),
        )
    except HTTPException as exc:
        if exc.status_code == 401:
            request.state.apim_result_reason = "missing_subscription"
        elif exc.detail == "Product is not published":
            request.state.apim_result_reason = "product_not_published"
        else:
            request.state.apim_result_reason = "subscription_not_authorized"
        raise

    set_current_span_attributes(
        **{
            APIM_ROUTE_NAME_ATTR: route.name,
            "apim.route.path_prefix": route.path_prefix,
            "apim.subscription.present": auth.subscription is not None,
            "apim.allowed_products.count": len(allowed_products),
            "apim.product.effective": effective_product_id,
        }
    )

    policy_cache: dict[str, Any] = request.app.state.policy_cache

    def _doc_for(xml: str) -> Any:
        cache_key = (xml, tuple(sorted(cfg.policy_fragments.items())))
        cached = policy_cache.get(cache_key)
        if cached is not None:
            return cached
        doc = parse_policies_xml(xml, policy_fragments=cfg.policy_fragments)
        policy_cache[cache_key] = doc
        return doc

    effective_product = cfg.products.get(effective_product_id) if effective_product_id else None
    policy_docs = [_doc_for(xml) for xml in stacked_policy_xml_documents(cfg, route, effective_product)]

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
    subscription_record = cfg.subscription.find_by_id(auth.subscription.id) if auth.subscription else None
    subscription_owner = subscription_record.created_by if subscription_record is not None else None
    subscription_groups = (
        sorted(group.id for group in cfg.groups.values() if subscription_owner and subscription_owner in group.users)
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

    trace_store: dict[str, Any] = request.app.state.trace_store
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
            return _policy_response(
                body=early.body,
                status_code=early.status_code,
                headers=dict(early.headers),
                media_type=early.media_type,
                correlation_id=correlation_id,
                trace_id=trace_id,
                trace_store=trace_store,
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
                policy_req=policy_req,
                policy_runtime=policy_runtime,
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
            return _policy_response(
                body=backend_early.body,
                status_code=backend_early.status_code,
                headers=dict(backend_early.headers),
                media_type=backend_early.media_type,
                correlation_id=correlation_id,
                trace_id=trace_id,
                trace_store=trace_store,
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
                policy_req=policy_req,
                policy_runtime=policy_runtime,
            )

    effective_claims = auth.claims
    jwt_claims = policy_req.variables.get("_last_jwt_claims")
    if isinstance(jwt_claims, dict):
        effective_claims = jwt_claims
        apply_claim_headers(policy_req.headers, effective_claims)

    try:
        enforce_route_authz(route, effective_claims)
    except HTTPException as exc:
        if exc.detail == "Missing required scope":
            request.state.apim_result_reason = "missing_required_scope"
        elif exc.detail == "Missing required role":
            request.state.apim_result_reason = "missing_required_role"
        else:
            request.state.apim_result_reason = "missing_required_claim"
        raise

    upstream_base_url = route.upstream_base_url
    upstream_auth: tuple[str, str] | None = None
    selected_backend_url = str(policy_req.variables.get("selected_backend_url") or "")
    selected_backend_id = str(policy_req.variables.get("selected_backend_id") or "")
    backend_id = selected_backend_id or (route.backend or "" if not selected_backend_url else "")
    if selected_backend_url:
        upstream_base_url = selected_backend_url
    pool_backend = None
    pool_backend_id = ""
    backend = None
    backend_health: dict[str, Any] = request.app.state.backend_health
    if backend_id:
        backend = cfg.backends.get(backend_id)
        if backend is not None and (backend.type or "single").lower() == "pool":
            pool_backend = backend
            pool_backend_id = backend_id
            selection = select_pool_member(cfg, backend_health, pool_backend_id, pool_backend, now=time.time())
            if selection is None:
                request.state.apim_result_reason = "backend_pool_exhausted"
                raise HTTPException(status_code=503, detail="All backend pool members are unavailable")
            backend_id, backend = selection
            policy_req.headers["x-apim-backend-pool"] = pool_backend_id
        if backend is not None:
            upstream_base_url = selected_backend_url or (
                render_backend_value(backend.url, policy_req, cfg) or backend.url
            )
            policy_req.headers.setdefault("x-apim-backend-id", backend_id)
            upstream_auth = apply_backend_credentials(backend, policy_req, cfg)

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
        cache_key = request_cache_key(
            method=request.method,
            upstream_url=upstream_url,
            query=policy_req.query,
            authorization=authz,
            subscription_key=sub_key,
        )
        cached = request.app.state.cache.get(cache_key)
        if cached is not None:
            cached_response = cached_gateway_response(
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
        nonlocal backend_id, backend, upstream_base_url, upstream_url
        if pool_backend is None or backend is None:
            return
        now = time.time()
        breaker = pool_member_breaker(pool_backend, backend)
        record_backend_result(backend_health, breaker, backend_id, now=now, failed=True)
        reselected = select_pool_member(cfg, backend_health, pool_backend_id, pool_backend, now=now)
        if reselected is None:
            return
        backend_id, backend = reselected
        upstream_base_url = render_backend_value(backend.url, policy_req, cfg) or backend.url
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
        breaker = pool_member_breaker(pool_backend, backend)
        record_backend_result(
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
                return _policy_response(
                    body=override.body,
                    status_code=override.status_code,
                    headers=dict(override.headers),
                    media_type=override.media_type,
                    correlation_id=correlation_id,
                    trace_id=trace_id,
                    trace_store=trace_store,
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
                    policy_req=policy_req,
                    policy_runtime=policy_runtime,
                )
        import logging

        logging.getLogger("apim-simulator").exception("Unable to reach upstream", exc_info=last_exc)
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
        cache_key is not None or policy_response_cache_active or policy_buffering_required or not cfg.proxy_streaming
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

    finalize_deferred_actions(
        PolicyRequest(
            method=policy_req.method,
            path=policy_req.path,
            query=dict(policy_req.query),
            headers=dict(policy_req.headers),
            variables=policy_req.variables,
            body=policy_req.body,
            response_status_code=upstream_status_code,
            response_headers=response_headers,
            response_body=content,
            response_media_type=media_type,
        ),
        policy_runtime,
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
        attach_trace(
            response_headers,
            trace_id=trace_id if trace_requested else None,
            trace_store=trace_store,
            trace_base=trace_base,
            trace_collector=trace_collector,
            cfg=cfg,
            extra={
                "attempts": attempts_used,
                "status": upstream_status_code,
                "elapsed_ms": int(elapsed_seconds * 1000),
                "cache": "miss",
            },
        )
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
            attach_trace(
                response_headers,
                trace_id=trace_id,
                trace_store=trace_store,
                trace_base=trace_base,
                trace_collector=trace_collector,
                cfg=cfg,
                extra={
                    "attempts": attempts_used,
                    "status": upstream_status_code,
                    "elapsed_ms": int(elapsed_seconds * 1000),
                    "cache": None,
                },
            )
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
        attach_trace(
            response_headers,
            trace_id=trace_id,
            trace_store=trace_store,
            trace_base=trace_base,
            trace_collector=trace_collector,
            cfg=cfg,
            extra={
                "attempts": attempts_used,
                "status": upstream_status_code,
                "elapsed_ms": int(elapsed_seconds * 1000),
                "cache": None,
            },
        )
    return Response(
        content=content,
        status_code=upstream_status_code,
        headers=response_headers,
        media_type=media_type,
    )
