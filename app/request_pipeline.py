"""APIM request pipeline.

Product grant, policy stacking, backend pick, cache, retry, and trace attach
live here. ``create_app`` stays the composer; the HTTP catch-all is an adapter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
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
from app.gateway_errors import subscription_key_error
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


def _roles_in(value: Any) -> set[str]:
    """Roles from one claim value, which may be a single role or a list of them."""
    if isinstance(value, str) and value:
        return {value}
    if isinstance(value, list):
        return {str(role) for role in value if role}
    return set()


def _role_claim_sources(claims: dict) -> Iterator[Any]:
    """Every place an identity provider might have put roles.

    Three shapes in the wild: a top-level `roles`, Keycloak's realm-wide
    `realm_access.roles`, and its per-client `resource_access.<client>.roles`.
    """
    yield claims.get("roles")

    realm_access = claims.get("realm_access")
    if isinstance(realm_access, dict):
        yield realm_access.get("roles")

    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict):
        for entry in resource_access.values():
            if isinstance(entry, dict):
                yield entry.get("roles")


def extract_roles(claims: dict) -> set[str]:
    """The union of every role claim, whichever shape the issuer used."""
    return set().union(*(_roles_in(source) for source in _role_claim_sources(claims)))


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
    request: Request | None = None,
) -> str:
    allowed_products = allowed_products_for_route(route)
    if not allowed_products:
        return ""

    published_products = [p for p in allowed_products if product_is_published(cfg, p)]
    if not published_products:
        # The simulator keeps this product-state gate as an explicit adaptation;
        # APIM says unpublishing hides a product from the portal without
        # invalidating existing keys or product-context access:
        # https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
        raise HTTPException(status_code=403, detail="Product is not published")

    require_sub = any(
        (cfg.products.get(p).require_subscription if cfg.products.get(p) else True) for p in published_products
    )
    if require_sub and subscription_is_bypassed:
        require_sub = False
    if require_sub:
        if auth.subscription is None:
            raise subscription_key_error(request, cfg, route, missing=True)
        granted = set(auth.subscription_products)
        if not set(published_products).intersection(granted):
            if set(allowed_products).intersection(granted):
                raise HTTPException(status_code=403, detail="Product is not published")
            raise subscription_key_error(request, cfg, route, missing=False)

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
    _add_simulator_response_headers(out_headers, cfg, correlation_id)
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


def _add_simulator_response_headers(headers: dict[str, str], cfg: GatewayConfig, correlation_id: str | None) -> None:
    """Add opt-in headers used by simulator demos, not by APIM itself."""
    if not cfg.emit_simulator_response_headers:
        return
    headers.setdefault("x-apim-simulator", "apim-simulator")
    if correlation_id is not None:
        headers.setdefault("x-correlation-id", correlation_id)


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
    _add_simulator_response_headers(headers, cfg, correlation_id)
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


@dataclass
class _UpstreamPayload:
    """The upstream response, normalised and buffered if anything needs the body."""

    status_code: int
    headers: dict[str, str]
    media_type: str | None
    content: bytes
    buffered: bool


async def _read_upstream_response(
    *,
    upstream_response: httpx.Response,
    correlation_id: str | None,
    pool: _PoolState,
    cfg: GatewayConfig,
    cache_key: str | None,
    policy_req: PolicyRequest,
    policy_response_cache_active: bool,
) -> _UpstreamPayload:
    """Normalise the upstream response, buffering the body only when needed.

    Streaming is the point of the proxy, so the body is read into memory only
    when something downstream must see all of it: the cache, a policy that
    asked to buffer, or a non-streaming configuration.
    """
    headers = filter_response_headers(dict(upstream_response.headers))
    _add_simulator_response_headers(headers, cfg, correlation_id)
    if pool.pool_backend is not None:
        headers["x-apim-backend-pool"] = pool.pool_backend_id
        headers["x-apim-backend-id"] = pool.backend_id

    status_code = int(upstream_response.status_code)
    if not (100 <= status_code <= 599):
        raise HTTPException(status_code=502, detail="Backend API returned invalid status code")

    requires_buffering = (
        cache_key is not None
        or policy_response_cache_active
        or bool(policy_req.variables.get("_policy_response_buffering_required"))
        or bool(policy_req.variables.get("_forward_request_buffer_response"))
        or bool(policy_req.variables.get("_forward_request_fail_on_error_status_code"))
        or not cfg.proxy_streaming
    )
    content = b""
    if requires_buffering:
        content = await upstream_response.aread()
        await upstream_response.aclose()

    return _UpstreamPayload(
        status_code=status_code,
        headers=headers,
        media_type=upstream_response.headers.get("content-type"),
        content=content,
        buffered=requires_buffering,
    )


async def _handle_backend_error_status(
    *,
    request: Request,
    cfg: GatewayConfig,
    policy_docs: list[Any],
    policy_req: PolicyRequest,
    policy_runtime: Any,
    upstream_response: httpx.Response,
    upstream: _UpstreamPayload,
    attempts_used: int,
    elapsed_seconds: float,
    trace: _TraceContext,
    trace_store: dict[str, Any],
    trace_base: dict[str, Any],
    trace_id: str | None,
    trace_collector: Any,
    correlation_id: str | None,
) -> Response | None:
    """Apply on-error for a backend status when forward-request asks for it."""
    status_code = upstream.status_code
    if not (400 <= status_code <= 599) or not policy_req.variables.get("_forward_request_fail_on_error_status_code"):
        return None

    failure_req = PolicyRequest(
        method=request.method,
        path=policy_req.path,
        query=dict(policy_req.query),
        headers=dict(policy_req.headers),
        variables={
            **policy_req.variables,
            "error": "backend_response_failure",
            "_last_error": {
                "Source": "forward-request",
                "Reason": "BackendResponseFailure",
                "Message": f"Backend returned HTTP {status_code}",
                "Scope": "",
                "Section": "backend",
                "Path": "",
                "PolicyId": "",
            },
        },
        body=policy_req.body,
        response_status_code=status_code,
        response_headers=dict(upstream.headers),
        response_body=upstream.content,
        response_media_type=upstream.media_type,
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
            policy_req=failure_req,
            policy_runtime=policy_runtime,
        )

    request.state.apim_result_reason = "backend_response_failure"
    return _uncached_response(
        request=request,
        cfg=cfg,
        upstream_response=upstream_response,
        streaming=False,
        status_code=status_code,
        response_headers=upstream.headers,
        media_type=upstream.media_type,
        content=upstream.content,
        attempts_used=attempts_used,
        elapsed_seconds=elapsed_seconds,
        trace=trace,
        trace_store=trace_store,
        trace_base=trace_base,
    )


async def _apply_outbound_policies(
    *,
    policy_docs: list[Any],
    policy_runtime: Any,
    request: Request,
    policy_req: PolicyRequest,
    response_headers: dict[str, str],
    content: bytes,
    media_type: str | None,
    upstream_status_code: int,
) -> tuple[dict[str, str], bytes, str | None]:
    """Run the outbound stage and return what it made of the response."""
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
    return outbound_req.headers, outbound_req.response_body, outbound_req.response_media_type or media_type


def _enforce_authz_with_policy_claims(
    *, request: Request, route: Any, auth: AuthContext, policy_req: PolicyRequest, cfg: GatewayConfig
) -> None:
    """Apply route authorization against the claims policy actually validated.

    A validate-jwt policy can produce a richer claim set than the gateway's own
    authentication did. When it has, those claims are what authorization must
    read, and they are copied onto the upstream headers too.
    """
    effective_claims = auth.claims
    jwt_claims = policy_req.variables.get("_last_jwt_claims")
    if isinstance(jwt_claims, dict):
        effective_claims = jwt_claims
        if cfg.inject_simulator_identity_headers:
            apply_claim_headers(policy_req.headers, effective_claims)

    try:
        enforce_route_authz(route, effective_claims)
    except HTTPException as exc:
        request.state.apim_result_reason = _route_authz_reason(exc)
        raise


def _serve_from_cache(
    *,
    cache_key: str,
    request: Request,
    route: Any,
    policy_req: PolicyRequest,
    policy_runtime: Any,
    trace_base: dict[str, Any],
    trace_collector: Any,
    cfg: GatewayConfig,
    gateway_metrics: Any,
    correlation_id: str | None,
    trace_id: str | None,
) -> Response | None:
    """The cached response for this key, or None to go upstream.

    An entry the cache holds but cannot serve (expired, or vary-mismatched) is
    evicted rather than left to be re-checked on every later request.
    """
    cached = request.app.state.cache.get(cache_key)
    if cached is None:
        return None
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
    if cached_response is None:
        request.app.state.cache.pop(cache_key, None)
    return cached_response


@dataclass(frozen=True)
class _AdmittedRequest:
    """A request that passed the gate: it has a route, an identity and a product."""

    resolved: Any
    route: Any
    auth: AuthContext
    effective_product_id: str


def _admit_request(request: Request, cfg: GatewayConfig) -> _AdmittedRequest:
    """Decide whether this call may proceed, and against which product.

    Refusals are annotated with the reason the access log reports before being
    re-raised, so an operator can tell a missing subscription from an
    unpublished product without reading the policy.
    """
    from app.telemetry import set_current_span_attributes

    validate_client_certificate(request, cfg)

    resolved = resolve_route(cfg, request)
    if resolved is None:
        request.state.apim_result_reason = "no_route"
        raise HTTPException(status_code=404, detail="Resource not found")
    route = resolved.route
    request.state.apim_route_name = route.name

    auth = authenticate_request(request, cfg, request.app.state.oidc_verifiers, route)
    allowed_products = allowed_products_for_route(route)
    try:
        effective_product_id = enforce_product_grant(
            cfg,
            route,
            auth,
            subscription_is_bypassed=subscription_bypassed(request, cfg),
            request=request,
        )
    except HTTPException as exc:
        request.state.apim_result_reason = _product_grant_reason(exc)
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
    return _AdmittedRequest(resolved=resolved, route=route, auth=auth, effective_product_id=effective_product_id)


def _policy_document_stack(
    cfg: GatewayConfig, route: Any, effective_product_id: str, policy_cache: dict[Any, Any]
) -> list[Any]:
    """Parse the global -> product -> API -> operation policy stack, once each.

    Parsing is memoised on the XML plus the fragment table, because the same
    documents are re-parsed on every single request otherwise.
    """

    def _doc_for(xml: str) -> Any:
        cache_key = (xml, tuple(sorted(cfg.policy_fragments.items())))
        cached = policy_cache.get(cache_key)
        if cached is not None:
            return cached
        doc = parse_policies_xml(xml, policy_fragments=cfg.policy_fragments)
        policy_cache[cache_key] = doc
        return doc

    effective_product = cfg.products.get(effective_product_id) if effective_product_id else None
    return [_doc_for(xml) for xml in stacked_policy_xml_documents(cfg, route, effective_product)]


async def _read_body_within_limit(request: Request, cfg: GatewayConfig) -> bytes:
    """The request body, or a 413 when it exceeds the configured ceiling."""
    body = await request.body()
    if len(body) > cfg.max_request_body_bytes:
        request.state.apim_result_reason = "request_body_too_large"
        # Learn documents validation size errors, but not this simulator limit's
        # public text; retain the local 413 contract inside the APIM envelope:
        # https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy
        raise HTTPException(status_code=413, detail="Request body too large")
    return body


def _upstream_last_error(last_exc: Exception | None) -> dict[str, str]:
    """Map transport failures to APIM's documented LastError vocabulary."""
    timed_out = isinstance(last_exc, httpx.TimeoutException)
    return {
        "Source": "forward-request" if timed_out else "multiple",
        "Reason": "Timeout" if timed_out else "BackendConnectionFailure",
        "Message": str(last_exc) or ("Backend timeout" if timed_out else "Backend connection failure"),
        "Scope": "",
        "Section": "backend",
        "Path": "",
        "PolicyId": "",
    }


async def _fail_upstream_unavailable(
    *,
    request: Request,
    cfg: GatewayConfig,
    policy_docs: list[Any],
    policy_req: PolicyRequest,
    policy_runtime: Any,
    attempts_used: int,
    elapsed_seconds: float,
    last_exc: Exception | None,
    correlation_id: str | None,
    trace_id: str | None,
    trace_store: dict[str, Any],
    trace_base: dict[str, Any],
    trace_collector: Any,
) -> Response:
    """Every retry failed. Give on-error policy the last word, else return APIM's 500."""
    from app.telemetry import set_current_span_attributes

    request.state.apim_result_reason = "upstream_unavailable"
    request.state.apim_upstream_duration_seconds = elapsed_seconds
    set_current_span_attributes(
        **{
            APIM_RESULT_REASON_ATTR: "upstream_unavailable",
            APIM_UPSTREAM_ATTEMPTS_ATTR: attempts_used,
        }
    )

    override = None
    if policy_docs:
        last_error = _upstream_last_error(last_exc)
        failure_req = PolicyRequest(
            method=request.method,
            path=policy_req.path,
            query=dict(policy_req.query),
            headers=dict(policy_req.headers),
            variables={
                **policy_req.variables,
                "error": "upstream_unavailable",
                "_last_error": last_error,
            },
            response_status_code=500,
        )
        override = await apply_on_error_async(policy_docs, failure_req, policy_runtime)

    if override is None:
        logging.getLogger("apim-simulator").exception("Unable to reach upstream", exc_info=last_exc)
        # The Learn error-handling page documents HTTP 500 and the
        # BackendConnectionFailure reason, but not the default JSON body. This
        # shape is the observed APIM gateway response documented in the linked
        # Microsoft Q&A answer.
        body = json.dumps(
            {
                "statusCode": 500,
                "message": "Internal server error",
                "activityId": str(uuid.uuid4()),
            }
        ).encode()
        return _policy_response(
            body=body,
            status_code=500,
            headers={"content-type": "application/json"},
            media_type="application/json",
            correlation_id=correlation_id,
            trace_id=trace_id,
            trace_store=trace_store,
            trace_base=trace_base,
            trace_collector=trace_collector,
            cfg=cfg,
            extra={
                "attempts": attempts_used,
                "status": 500,
                "elapsed_ms": int(elapsed_seconds * 1000),
                "cache": None,
                "reason": "backend_connection_failure",
            },
            policy_req=policy_req,
            policy_runtime=policy_runtime,
        )

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


def _client_ip(request: Request, forwarded_for: str) -> str:
    """The caller's address: the first X-Forwarded-For hop, else the socket peer."""
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def _subscription_context(cfg: GatewayConfig, auth: AuthContext) -> tuple[str | None, list[str]]:
    """Who owns the calling subscription, and which groups that owner is in.

    Policies address groups by id, so an owner with no groups and no owner at
    all both resolve to an empty list rather than to None.
    """
    if auth.subscription is None:
        return None, []
    record = cfg.subscription.find_by_id(auth.subscription.id)
    owner = record.created_by if record is not None else None
    if not owner:
        return owner, []
    return owner, sorted(group.id for group in cfg.groups.values() if owner in group.users)


def _gateway_cache_key(
    *,
    cfg: GatewayConfig,
    request: Request,
    upstream_url: str,
    policy_req: PolicyRequest,
    policy_response_cache_active: bool,
) -> str | None:
    """The gateway cache key for this exchange, or None when it is not cacheable.

    Only GETs are cached, never a streaming proxy, and never when a policy has
    already taken responsibility for caching the response itself.
    """
    if not cfg.cache_enabled or request.method != "GET" or cfg.proxy_streaming or policy_response_cache_active:
        return None
    return request_cache_key(
        method=request.method,
        upstream_url=upstream_url,
        query=policy_req.query,
        authorization=request.headers.get("authorization", ""),
        subscription_key=request.headers.get("ocp-apim-subscription-key", ""),
    )


@dataclass
class _BackendChoice:
    """Where this exchange is going upstream, and how it authenticates there."""

    upstream_base_url: str
    upstream_auth: tuple[str, str] | None
    pool: _PoolState


def _choose_backend(
    *,
    cfg: GatewayConfig,
    route: Any,
    policy_req: PolicyRequest,
    backend_health: dict[str, Any],
    request: Request,
) -> _BackendChoice:
    """Resolve the upstream for this exchange.

    Precedence: a backend a policy selected outright, then a named backend on
    the route. A named backend of type `pool` picks one healthy member, and an
    exhausted pool is a 503 rather than a fall-through to the route default.
    """
    upstream_base_url = route.upstream_base_url
    upstream_auth: tuple[str, str] | None = None
    selected_backend_url = str(policy_req.variables.get("selected_backend_url") or "")
    selected_backend_id = str(policy_req.variables.get("selected_backend_id") or "")
    backend_id = selected_backend_id or (route.backend or "" if not selected_backend_url else "")
    if selected_backend_url:
        upstream_base_url = selected_backend_url

    pool = _PoolState(
        pool_backend=None, pool_backend_id="", backend=None, backend_id=backend_id, backend_health=backend_health
    )
    if not backend_id:
        return _BackendChoice(upstream_base_url, upstream_auth, pool)

    backend = cfg.backends.get(backend_id)
    if backend is not None and (backend.type or "single").lower() == "pool":
        pool.pool_backend = backend
        pool.pool_backend_id = backend_id
        selection = select_pool_member(cfg, backend_health, backend_id, backend, now=time.time())
        if selection is None:
            request.state.apim_result_reason = "backend_pool_exhausted"
            # APIM's circuit-breaker documentation defines 503 availability
            # behavior, but not this simulator pool's public message:
            # https://learn.microsoft.com/en-us/azure/api-management/backends
            raise HTTPException(status_code=503, detail="All backend pool members are unavailable")
        backend_id, backend = selection
        policy_req.headers["x-apim-backend-pool"] = pool.pool_backend_id

    pool.backend_id = backend_id
    pool.backend = backend
    if backend is not None:
        upstream_base_url = selected_backend_url or (render_backend_value(backend.url, policy_req, cfg) or backend.url)
        policy_req.headers.setdefault("x-apim-backend-id", backend_id)
        upstream_auth = apply_backend_credentials(backend, policy_req, cfg)

    return _BackendChoice(upstream_base_url, upstream_auth, pool)


@dataclass
class _PoolState:
    """The backend pool member currently in play, and its health bookkeeping.

    A pool call can change its mind mid-exchange: when a member fails, the
    breaker is tripped and another member is selected. Both the caller and the
    retry loop need to see that change, so it lives in one mutable object
    rather than in six `nonlocal` names.
    """

    pool_backend: Any
    pool_backend_id: str
    backend: Any
    backend_id: str
    backend_health: dict[str, Any]

    @property
    def is_pool(self) -> bool:
        return self.pool_backend is not None and self.backend is not None

    def trip_and_reselect(self, cfg: GatewayConfig, policy_req: PolicyRequest) -> str | None:
        """Record this member as failed and pick another, if the pool has one.

        Returns the new member's base URL, or None when this is not a pool or
        the pool has nothing left to offer.
        """
        if not self.is_pool:
            return None
        now = time.time()
        breaker = pool_member_breaker(self.pool_backend, self.backend)
        record_backend_result(self.backend_health, breaker, self.backend_id, now=now, failed=True)
        reselected = select_pool_member(cfg, self.backend_health, self.pool_backend_id, self.pool_backend, now=now)
        if reselected is None:
            return None
        self.backend_id, self.backend = reselected
        policy_req.headers["x-apim-backend-id"] = self.backend_id
        return render_backend_value(self.backend.url, policy_req, cfg) or self.backend.url


@dataclass
class _UpstreamAttempt:
    """What the retry loop finished with."""

    response: httpx.Response | None
    attempts: int
    elapsed_seconds: float
    error: Exception | None
    upstream_url: str
    pool: _PoolState


async def _send_upstream_with_retries(
    *,
    client: httpx.AsyncClient,
    cfg: GatewayConfig,
    method: str,
    upstream_url: str,
    policy_req: PolicyRequest,
    upstream_auth: tuple[str, str] | None,
    route: Any,
    pool: _PoolState,
) -> _UpstreamAttempt:
    """Call the upstream, retrying on transport errors and retryable statuses.

    Each failed attempt against a pool trips that member's breaker and reselects,
    so a retry can land on a different backend than the one that just failed.
    """
    timeout_seconds = float(policy_req.variables.get("_forward_request_timeout_seconds", cfg.proxy_timeout_seconds))
    timeout = httpx.Timeout(timeout_seconds)
    follow_redirects = bool(policy_req.variables.get("_forward_request_follow_redirects", False))
    buffer_request_body = bool(policy_req.variables.get("_forward_request_buffer_request_body", True))
    max_attempts = max(1, cfg.proxy_max_attempts)
    last_exc: Exception | None = None
    upstream_response: httpx.Response | None = None
    attempts_used = 0
    start = time.perf_counter()

    def _failover() -> None:
        nonlocal upstream_url
        base_url = pool.trip_and_reselect(cfg, policy_req)
        if base_url is not None:
            upstream_url = route.build_upstream_url(policy_req.path, upstream_base_url=base_url)

    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        req = client.build_request(
            method,
            upstream_url,
            content=policy_req.body if attempt == 1 or buffer_request_body else b"",
            headers=policy_req.headers,
            params=policy_req.query,
            timeout=timeout,
        )
        try:
            upstream_response = await client.send(
                req,
                stream=cfg.proxy_streaming,
                auth=upstream_auth,
                follow_redirects=follow_redirects,
            )
        except httpx.RequestError as exc:
            last_exc = exc
            _failover()
            if attempt >= max_attempts:
                break
            continue

        if upstream_response.status_code in cfg.proxy_retry_statuses and attempt < max_attempts:
            await upstream_response.aclose()
            upstream_response = None
            _failover()
            continue
        break

    if pool.is_pool and upstream_response is not None:
        breaker = pool_member_breaker(pool.pool_backend, pool.backend)
        record_backend_result(
            pool.backend_health,
            breaker,
            pool.backend_id,
            now=time.time(),
            failed=upstream_response.status_code in breaker.error_statuses,
        )

    return _UpstreamAttempt(
        response=upstream_response,
        attempts=attempts_used,
        elapsed_seconds=time.perf_counter() - start,
        error=last_exc,
        upstream_url=upstream_url,
        pool=pool,
    )


_ROUTE_AUTHZ_REASONS = {
    "Missing required scope": "missing_required_scope",
    "Missing required role": "missing_required_role",
}


def _product_grant_reason(exc: HTTPException) -> str:
    """Why a product grant was refused, in the vocabulary the access log uses."""
    if exc.status_code == 401:
        return "missing_subscription"
    if exc.detail == "Product is not published":
        return "product_not_published"
    return "subscription_not_authorized"


def _route_authz_reason(exc: HTTPException) -> str:
    """Why route authorization was refused. Anything unnamed is a claim failure."""
    return _ROUTE_AUTHZ_REASONS.get(exc.detail, "missing_required_claim")


@dataclass(frozen=True)
class _TraceContext:
    """Whether this exchange is being traced, and what to collect into.

    Tracing is opt-in per request and only where the tenant enables it, so all
    three fields move together: an id and a collector exist exactly when the
    trace was requested.
    """

    requested: bool
    trace_id: str | None
    collector: PolicyTraceCollector | None

    @classmethod
    def read(cls, request: Request, cfg: GatewayConfig) -> _TraceContext:
        requested = cfg.trace_enabled and request.headers.get("x-apim-trace", "").lower() == "true"
        request.state.apim_trace_requested = requested
        return cls(
            requested=requested,
            trace_id=f"trace-{int(time.time() * 1000)}" if requested else None,
            collector=PolicyTraceCollector() if requested else None,
        )


def _uncached_response(
    *,
    request: Request,
    cfg: GatewayConfig,
    upstream_response: httpx.Response,
    streaming: bool,
    status_code: int,
    response_headers: dict[str, str],
    media_type: str | None,
    content: bytes,
    attempts_used: int,
    elapsed_seconds: float,
    trace: _TraceContext,
    trace_store: dict[str, Any],
    trace_base: dict[str, Any],
) -> Response:
    """Return the upstream response, streamed or buffered.

    Streaming hands the open upstream body straight to the client and closes it
    as a background task, so nothing here may read `content` in that case.
    """
    _record_final_reason(
        request,
        reason="upstream_stream" if streaming else "upstream_response",
        attempts_used=attempts_used,
    )
    if trace.requested:
        attach_trace(
            response_headers,
            trace_id=trace.trace_id,
            trace_store=trace_store,
            trace_base=trace_base,
            trace_collector=trace.collector,
            cfg=cfg,
            extra={
                "attempts": attempts_used,
                "status": status_code,
                "elapsed_ms": int(elapsed_seconds * 1000),
                "cache": None,
            },
        )

    if streaming:
        return StreamingResponse(
            upstream_response.aiter_bytes(),
            status_code=status_code,
            headers=response_headers,
            media_type=media_type,
            background=BackgroundTask(upstream_response.aclose),
        )
    return Response(content=content, status_code=status_code, headers=response_headers, media_type=media_type)


@dataclass(frozen=True)
class _ForwardingContext:
    """What the proxy chain in front of the gateway said about this call."""

    incoming_host: str
    forwarded_host: str
    forwarded_proto: str
    forwarded_for: str
    client_ip: str

    @classmethod
    def read(cls, request: Request) -> _ForwardingContext:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        return cls(
            incoming_host=request.headers.get("host", ""),
            forwarded_host=request.headers.get("x-forwarded-host", ""),
            forwarded_proto=request.headers.get("x-forwarded-proto", ""),
            forwarded_for=forwarded_for,
            client_ip=_client_ip(request, forwarded_for),
        )

    def as_trace_fields(self) -> dict[str, str]:
        return {
            "incoming_host": self.incoming_host,
            "forwarded_host": self.forwarded_host,
            "forwarded_proto": self.forwarded_proto,
            "forwarded_for": self.forwarded_for,
            "client_ip": self.client_ip,
        }


def _build_policy_request(
    *,
    cfg: GatewayConfig,
    request: Request,
    route: Any,
    auth: AuthContext,
    resolved: Any,
    headers: dict[str, str],
    body: bytes,
    effective_product_id: str,
    correlation_id: str | None,
    forwarding: _ForwardingContext,
    subscription_owner: str | None,
    subscription_groups: list[str],
) -> PolicyRequest:
    """The request object policy expressions read and write.

    Its `variables` are the policy-visible surface: everything a policy can
    address by name, plus the underscore-prefixed entries the pipeline uses to
    pass state between its own stages.
    """
    upstream_query = dict(request.query_params)
    api = cfg.apis.get(route.api_id or "")
    operation = api.operations.get(route.operation_id or "") if api is not None else None
    return PolicyRequest(
        method=request.method,
        path=resolved.upstream_path,
        query=upstream_query,
        headers=headers,
        variables={
            "route": route.name,
            "api_id": route.api_id or "",
            "api_name": api.name if api is not None else "",
            "operation_id": route.operation_id or "",
            "operation_name": operation.name if operation is not None else "",
            "subscription_id": auth.subscription.id if auth.subscription else "",
            "products": auth.subscription_products,
            "product_id": effective_product_id,
            "client_ip": forwarding.client_ip,
            "correlation_id": correlation_id,
            "incoming_host": forwarding.incoming_host,
            "forwarded_host": forwarding.forwarded_host,
            "forwarded_proto": forwarding.forwarded_proto,
            "forwarded_for": forwarding.forwarded_for,
            "subscription_owner": subscription_owner or "",
            "subscription_groups": subscription_groups,
            "rate_limit_store": request.app.state.rate_limit_store,
            "quota_store": request.app.state.quota_store,
            "original_request_url": str(request.url),
            "_request_headers": dict(headers),
            "_request_query": dict(upstream_query),
            "_matched_parameters": dict(resolved.matched_parameters),
        },
        body=body,
    )


def _record_final_reason(request: Request, *, reason: str, attempts_used: int) -> None:
    """Stamp how the exchange ended onto the request state and the active span."""
    from app.telemetry import set_current_span_attributes

    request.state.apim_result_reason = reason
    set_current_span_attributes(**{APIM_RESULT_REASON_ATTR: reason, APIM_UPSTREAM_ATTEMPTS_ATTR: attempts_used})


def _store_and_respond(
    *,
    request: Request,
    cfg: GatewayConfig,
    route: Any,
    gateway_metrics: Any,
    cache_key: str,
    status_code: int,
    response_headers: dict[str, str],
    media_type: str | None,
    content: bytes,
    attempts_used: int,
    elapsed_seconds: float,
    trace_id: str | None,
    trace_store: dict[str, Any],
    trace_base: dict[str, Any],
    trace_collector: Any,
) -> Response:
    """Record a cache miss, store the response, and return it.

    The cache is a plain dict with no eviction policy, so reaching the entry
    ceiling clears it outright rather than evicting by age.
    """
    from app.telemetry import set_current_span_attributes

    request.state.apim_cache_result = "miss"
    _record_final_reason(request, reason="upstream_response", attempts_used=attempts_used)
    gateway_metrics.cache_events.add(
        1,
        {
            APIM_ROUTE_NAME_ATTR: route.name,
            APIM_CACHE_RESULT_ATTR: "miss",
            "http.request.method": request.method,
        },
    )
    set_current_span_attributes(**{APIM_CACHE_RESULT_ATTR: "miss"})
    response_headers["x-apim-cache"] = "miss"

    if len(request.app.state.cache) >= cfg.cache_max_entries:
        request.app.state.cache.clear()
    request.app.state.cache[cache_key] = (
        time.time() + cfg.cache_ttl_seconds,
        status_code,
        dict(response_headers),
        media_type,
        content,
    )

    attach_trace(
        response_headers,
        trace_id=trace_id,
        trace_store=trace_store,
        trace_base=trace_base,
        trace_collector=trace_collector,
        cfg=cfg,
        extra={
            "attempts": attempts_used,
            "status": status_code,
            "elapsed_ms": int(elapsed_seconds * 1000),
            "cache": "miss",
        },
    )
    return Response(content=content, status_code=status_code, headers=response_headers, media_type=media_type)


async def _short_circuit_policy_stages(
    *,
    policy_docs: list[Any],
    policy_req: PolicyRequest,
    policy_runtime: Any,
    request: Request,
    route: Any,
    cfg: GatewayConfig,
    gateway_metrics: Any,
    correlation_id: str | None,
    trace_id: str | None,
    trace_store: dict[str, Any],
    trace_base: dict[str, Any],
    trace_collector: Any,
) -> Response | None:
    """Run inbound then backend policy, returning a response if either ends the call.

    The two stages differ only in which one they name, so they share a body
    rather than being written out twice.
    """
    from app.telemetry import set_current_span_attributes

    if not policy_docs:
        return None

    for stage, apply_stage in (("inbound", apply_inbound_async), ("backend", apply_backend_async)):
        early = await apply_stage(policy_docs, policy_req, policy_runtime)
        if early is None:
            continue
        reason = f"policy_{stage}_short_circuit"
        request.state.apim_result_reason = reason
        request.state.apim_upstream_attempts = 0
        gateway_metrics.policy_short_circuits.add(
            1,
            {
                APIM_ROUTE_NAME_ATTR: route.name,
                "apim.policy.stage": stage,
                "http.request.method": request.method,
            },
        )
        set_current_span_attributes(**{APIM_RESULT_REASON_ATTR: reason, APIM_UPSTREAM_ATTEMPTS_ATTR: 0})
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
                "reason": reason,
            },
            policy_req=policy_req,
            policy_runtime=policy_runtime,
        )
    return None


def _record_selected_backend(trace_collector: Any, backend_id: str | None, upstream_base_url: str) -> None:
    """Note the backend in the trace, unless a policy already recorded its own choice."""
    if trace_collector is not None and trace_collector.selected_backend is None:
        trace_collector.selected_backend = {
            "backend_id": backend_id or None,
            "base_url": upstream_base_url,
        }


def _initial_upstream_headers(
    request: Request, auth: AuthContext, cfg: GatewayConfig, correlation_id: str | None
) -> dict[str, str]:
    """The backend request headers before any policy runs, keyed in lower case.

    Simulator identity and correlation headers are added only when the config
    opts in; APIM itself adds neither.
    """
    headers = {
        key.lower(): value
        for key, value in build_upstream_headers(
            request,
            auth,
            inject_simulator_identity_headers=cfg.inject_simulator_identity_headers,
        ).items()
    }
    if cfg.propagate_simulator_correlation_id and correlation_id:
        headers.setdefault("x-correlation-id", correlation_id)
    return headers


async def execute_gateway_request(request: Request) -> Response:
    from app.telemetry import set_current_span_attributes

    cfg: GatewayConfig = request.app.state.gateway_config
    gateway_metrics = request.app.state.gateway_metrics

    admitted = _admit_request(request, cfg)
    resolved, route, auth, effective_product_id = (
        admitted.resolved,
        admitted.route,
        admitted.auth,
        admitted.effective_product_id,
    )

    policy_docs = _policy_document_stack(cfg, route, effective_product_id, request.app.state.policy_cache)

    body = await _read_body_within_limit(request, cfg)
    correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get("x-correlation-id")
    headers = _initial_upstream_headers(request, auth, cfg, correlation_id)

    forwarding = _ForwardingContext.read(request)
    request.state.apim_client_ip = forwarding.client_ip
    subscription_owner, subscription_groups = _subscription_context(cfg, auth)

    policy_req = _build_policy_request(
        cfg=cfg,
        request=request,
        route=route,
        auth=auth,
        resolved=resolved,
        headers=headers,
        body=body,
        effective_product_id=effective_product_id,
        correlation_id=correlation_id,
        forwarding=forwarding,
        subscription_owner=subscription_owner,
        subscription_groups=subscription_groups,
    )

    trace = _TraceContext.read(request, cfg)
    trace_requested, trace_id, trace_collector = trace.requested, trace.trace_id, trace.collector
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
        **forwarding.as_trace_fields(),
        "upstream_url": None,
    }

    short_circuit = await _short_circuit_policy_stages(
        policy_docs=policy_docs,
        policy_req=policy_req,
        policy_runtime=policy_runtime,
        request=request,
        route=route,
        cfg=cfg,
        gateway_metrics=gateway_metrics,
        correlation_id=correlation_id,
        trace_id=trace_id,
        trace_store=trace_store,
        trace_base=trace_base,
        trace_collector=trace_collector,
    )
    if short_circuit is not None:
        return short_circuit

    _enforce_authz_with_policy_claims(request=request, route=route, auth=auth, policy_req=policy_req, cfg=cfg)

    choice = _choose_backend(
        cfg=cfg,
        route=route,
        policy_req=policy_req,
        backend_health=request.app.state.backend_health,
        request=request,
    )
    upstream_base_url = choice.upstream_base_url
    upstream_auth = choice.upstream_auth
    backend_id = choice.pool.backend_id
    backend = choice.pool.backend
    pool_backend = choice.pool.pool_backend
    pool_backend_id = choice.pool.pool_backend_id
    backend_health = choice.pool.backend_health

    request.state.apim_backend_id = backend_id or "direct"
    set_current_span_attributes(
        **{
            APIM_BACKEND_ID_ATTR: request.state.apim_backend_id,
            "apim.policy.documents": len(policy_docs),
        }
    )

    _record_selected_backend(trace_collector, backend_id, upstream_base_url)

    upstream_url = route.build_upstream_url(policy_req.path, upstream_base_url=upstream_base_url)
    policy_req.variables["upstream_url"] = upstream_url
    trace_base["upstream_url"] = upstream_url

    policy_response_cache_active = bool(policy_req.variables.get("_policy_response_cache_active"))
    cache_key = _gateway_cache_key(
        cfg=cfg,
        request=request,
        upstream_url=upstream_url,
        policy_req=policy_req,
        policy_response_cache_active=policy_response_cache_active,
    )
    if cache_key is not None:
        hit = _serve_from_cache(
            cache_key=cache_key,
            request=request,
            route=route,
            policy_req=policy_req,
            policy_runtime=policy_runtime,
            trace_base=trace_base,
            trace_collector=trace_collector,
            cfg=cfg,
            gateway_metrics=gateway_metrics,
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        if hit is not None:
            return hit

    attempt_result = await _send_upstream_with_retries(
        client=client,
        cfg=cfg,
        method=request.method,
        upstream_url=upstream_url,
        policy_req=policy_req,
        upstream_auth=upstream_auth,
        route=route,
        pool=_PoolState(
            pool_backend=pool_backend,
            pool_backend_id=pool_backend_id,
            backend=backend,
            backend_id=backend_id,
            backend_health=backend_health,
        ),
    )
    upstream_response = attempt_result.response
    attempts_used = attempt_result.attempts
    last_exc = attempt_result.error
    backend_id = attempt_result.pool.backend_id
    backend = attempt_result.pool.backend
    upstream_url = attempt_result.upstream_url
    elapsed_seconds = attempt_result.elapsed_seconds

    request.state.apim_upstream_attempts = attempts_used

    if upstream_response is None:
        return await _fail_upstream_unavailable(
            request=request,
            cfg=cfg,
            policy_docs=policy_docs,
            policy_req=policy_req,
            policy_runtime=policy_runtime,
            attempts_used=attempts_used,
            elapsed_seconds=elapsed_seconds,
            last_exc=last_exc,
            correlation_id=correlation_id,
            trace_id=trace_id,
            trace_store=trace_store,
            trace_base=trace_base,
            trace_collector=trace_collector,
        )

    request.state.apim_upstream_duration_seconds = elapsed_seconds
    upstream = await _read_upstream_response(
        upstream_response=upstream_response,
        correlation_id=correlation_id,
        pool=attempt_result.pool,
        cfg=cfg,
        cache_key=cache_key,
        policy_req=policy_req,
        policy_response_cache_active=policy_response_cache_active,
    )
    response_headers, media_type, content = upstream.headers, upstream.media_type, upstream.content
    upstream_status_code = upstream.status_code
    requires_buffering = upstream.buffered

    backend_error_response = await _handle_backend_error_status(
        request=request,
        cfg=cfg,
        policy_docs=policy_docs,
        policy_req=policy_req,
        policy_runtime=policy_runtime,
        upstream_response=upstream_response,
        upstream=upstream,
        attempts_used=attempts_used,
        elapsed_seconds=elapsed_seconds,
        trace=trace,
        trace_store=trace_store,
        trace_base=trace_base,
        trace_id=trace_id if trace_requested else None,
        trace_collector=trace_collector,
        correlation_id=correlation_id,
    )
    if backend_error_response is not None:
        return backend_error_response

    if policy_docs:
        response_headers, content, media_type = await _apply_outbound_policies(
            policy_docs=policy_docs,
            policy_runtime=policy_runtime,
            request=request,
            policy_req=policy_req,
            response_headers=response_headers,
            content=content,
            media_type=media_type,
            upstream_status_code=upstream_status_code,
        )

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
        return _store_and_respond(
            request=request,
            cfg=cfg,
            route=route,
            gateway_metrics=gateway_metrics,
            cache_key=cache_key,
            status_code=upstream_status_code,
            response_headers=response_headers,
            media_type=media_type,
            content=content,
            attempts_used=attempts_used,
            elapsed_seconds=elapsed_seconds,
            trace_id=trace_id if trace_requested else None,
            trace_store=trace_store,
            trace_base=trace_base,
            trace_collector=trace_collector,
        )

    return _uncached_response(
        request=request,
        cfg=cfg,
        upstream_response=upstream_response,
        streaming=cfg.proxy_streaming and not requires_buffering,
        status_code=upstream_status_code,
        response_headers=response_headers,
        media_type=media_type,
        content=content,
        attempts_used=attempts_used,
        elapsed_seconds=elapsed_seconds,
        trace=trace,
        trace_store=trace_store,
        trace_base=trace_base,
    )
