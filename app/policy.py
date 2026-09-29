from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt
from defusedxml import ElementTree
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm

from app.apim_expr import (
    CalloutResponse,
    JwtValue,
    build_expression_context,
    evaluate_apim_expression,
    is_apim_expression,
)
from app.config import GatewayConfig
from app.named_values import mask_secret_data, resolve_named_values_in_text
from app.policy_errors import element_name


@dataclass(frozen=True)
class ResponseSpec:
    status_code: int
    headers: dict[str, str]
    body: bytes = b""
    media_type: str | None = None


@dataclass
class PolicyRequest:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    variables: dict[str, Any]
    body: bytes = b""
    response_status_code: int | None = None
    response_headers: dict[str, str] | None = None
    response_body: bytes = b""
    response_media_type: str | None = None
    # Which policy section is executing. Policies that act on "the message"
    # (set-body, validate-content) act on the response in outbound.
    section: str = "inbound"

    @property
    def in_outbound(self) -> bool:
        return self.section == "outbound"


@dataclass
class PolicyTraceCollector:
    steps: list[dict[str, Any]] = field(default_factory=list)
    variable_writes: list[dict[str, Any]] = field(default_factory=list)
    jwt_validations: list[dict[str, Any]] = field(default_factory=list)
    send_requests: list[dict[str, Any]] = field(default_factory=list)
    selected_backend: dict[str, Any] | None = None


@dataclass
class PolicyRuntime:
    gateway_config: GatewayConfig | None = None
    http_client: httpx.AsyncClient | None = None
    timeout_seconds: float = 30.0
    trace: PolicyTraceCollector | None = None
    openid_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=dict)
    response_cache: dict[str, Any] = field(default_factory=dict)
    value_cache: dict[str, Any] = field(default_factory=dict)
    deferred_actions: list[Any] = field(default_factory=list)
    llm_metric_emitter: Any = None
    custom_metric_emitter: Any = None
    clock: Callable[[], float] | None = None


@dataclass(frozen=True)
class ResponseCacheEntry:
    expires_at: float
    status_code: int
    headers: dict[str, str]
    body: bytes
    media_type: str | None = None


@dataclass(frozen=True)
class ValueCacheEntry:
    expires_at: float
    value: Any


@dataclass(frozen=True)
class ResponseCachePolicyContext:
    cache_key: str
    downstream_caching_type: str
    must_revalidate: bool
    allow_private_response_caching: bool


class DeferredPolicyAction:
    def finalize(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:  # pragma: no cover
        raise NotImplementedError


class Condition:
    def __call__(self, req: PolicyRequest) -> bool:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True)
class Always(Condition):
    def __call__(self, req: PolicyRequest) -> bool:
        return True


@dataclass(frozen=True)
class HeaderEquals(Condition):
    name: str
    value: str

    def __call__(self, req: PolicyRequest) -> bool:
        return req.headers.get(self.name.lower(), "") == self.value


@dataclass(frozen=True)
class HeaderStartsWith(Condition):
    name: str
    prefix: str

    def __call__(self, req: PolicyRequest) -> bool:
        return req.headers.get(self.name.lower(), "").startswith(self.prefix)


@dataclass(frozen=True)
class QueryEquals(Condition):
    name: str
    value: str

    def __call__(self, req: PolicyRequest) -> bool:
        return req.query.get(self.name, "") == self.value


@dataclass(frozen=True)
class MethodIs(Condition):
    method: str

    def __call__(self, req: PolicyRequest) -> bool:
        return req.method.upper() == self.method.upper()


@dataclass(frozen=True)
class PathStartsWith(Condition):
    prefix: str

    def __call__(self, req: PolicyRequest) -> bool:
        return req.path.startswith(self.prefix)


@dataclass(frozen=True)
class ExpressionCondition(Condition):
    expression: str

    def __call__(self, req: PolicyRequest) -> bool:
        return bool(evaluate_apim_expression(self.expression, build_expression_context(req)))


def _strip_condition_quotes(value: str) -> str:
    """Drop one matched pair of surrounding quotes, single or double."""
    value = value.strip()
    if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
        return value[1:-1]
    return value


def _condition_call_argument(expr: str, opener: str) -> str:
    """The argument of a `name(...)` call at the head of a condition expression."""
    return _strip_condition_quotes(expr.split(opener, 1)[1].split(")", 1)[0])


def _parse_header_starts_with(expr: str) -> Condition:
    prefix = _strip_condition_quotes(expr.split(".startswith(", 1)[1].rsplit(")", 1)[0])
    return HeaderStartsWith(name=_condition_call_argument(expr, "header(").lower(), prefix=prefix)


def _parse_header_equals(expr: str) -> Condition:
    left, right = expr.split("==", 1)
    return HeaderEquals(name=_condition_call_argument(left, "header(").lower(), value=_strip_condition_quotes(right))


def _parse_query_equals(expr: str) -> Condition:
    left, right = expr.split("==", 1)
    return QueryEquals(name=_condition_call_argument(left, "query("), value=_strip_condition_quotes(right))


def _parse_method_is(expr: str) -> Condition:
    return MethodIs(method=_strip_condition_quotes(expr.split("==", 1)[1]))


def _parse_path_starts_with(expr: str) -> Condition:
    return PathStartsWith(prefix=_strip_condition_quotes(expr.split("path.startswith(", 1)[1].rsplit(")", 1)[0]))


# Recognisers for the condition mini-language, in precedence order. The
# startswith form must be tried before the equality form, because a
# `header(x).startswith(y)` expression can also contain "==" inside its prefix.
_CONDITION_FORMS: tuple[tuple[Callable[[str], bool], Callable[[str], Condition]], ...] = (
    (lambda e: e.startswith("header(") and ").startswith(" in e, _parse_header_starts_with),
    (lambda e: e.startswith("header(") and "==" in e, _parse_header_equals),
    (lambda e: e.startswith("query(") and "==" in e, _parse_query_equals),
    (lambda e: e.startswith("method") and "==" in e, _parse_method_is),
    (lambda e: e.startswith("path.startswith("), _parse_path_starts_with),
)


def parse_condition(expr: str | None) -> Condition:
    """Parse a `<when condition="...">` expression.

    An empty condition always fires. An `@`-prefixed one is a full policy
    expression evaluated at request time; everything else is the small
    comparison language recognised by _CONDITION_FORMS.
    """
    if not expr:
        return Always()

    expr = expr.strip()
    if expr.startswith("@"):
        return ExpressionCondition(expression=expr)

    for matches, build in _CONDITION_FORMS:
        if matches(expr):
            return build(expr)

    raise HTTPException(status_code=500, detail=f"Unsupported policy condition: {expr}")


class PolicyNode:
    def apply(
        self, req: PolicyRequest, runtime: PolicyRuntime | None = None
    ) -> ResponseSpec | None:  # pragma: no cover
        raise NotImplementedError

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        return self.apply(req, runtime)


@dataclass(frozen=True)
class NoOp(PolicyNode):
    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        return None


@dataclass(frozen=True)
class SetHeader(PolicyNode):
    name: str
    value: str
    exists_action: str = "override"

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        key = self.name
        action = (self.exists_action or "override").lower()
        rendered = render_policy_value(self.value, req, runtime)
        if action == "delete":
            req.headers.pop(key, None)
            _record_step(runtime, "set-header", {"name": key, "action": "delete"})
            return None

        if action == "skip" and key in req.headers:
            _record_step(runtime, "set-header", {"name": key, "action": "skip"})
            return None

        if action == "append" and key in req.headers:
            req.headers[key] = f"{req.headers[key]},{rendered}"
        else:
            req.headers[key] = rendered

        _record_step(runtime, "set-header", {"name": key, "action": action, "value": rendered})
        return None


@dataclass(frozen=True)
class RewriteUri(PolicyNode):
    template: str

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        req.path = render_policy_value(self.template, req, runtime)
        _record_step(runtime, "rewrite-uri", {"path": req.path})
        return None


@dataclass(frozen=True)
class SetVariable(PolicyNode):
    name: str
    value: str

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        rendered = evaluate_policy_value(self.value, req, runtime)
        req.variables[self.name] = rendered
        _record_variable_write(runtime, self.name, rendered, "set-variable")
        return None


@dataclass(frozen=True)
class SetQueryParameter(PolicyNode):
    name: str
    value: str
    exists_action: str = "override"

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        key = self.name
        action = (self.exists_action or "override").lower()
        rendered = render_policy_value(self.value, req, runtime)
        if action == "delete":
            req.query.pop(key, None)
            _record_step(runtime, "set-query-parameter", {"name": key, "action": "delete"})
            return None

        if action == "skip" and key in req.query:
            _record_step(runtime, "set-query-parameter", {"name": key, "action": "skip"})
            return None

        if action == "append" and key in req.query:
            req.query[key] = f"{req.query[key]},{rendered}"
        else:
            req.query[key] = rendered

        _record_step(runtime, "set-query-parameter", {"name": key, "action": action, "value": rendered})
        return None


@dataclass(frozen=True)
class SetBody(PolicyNode):
    value: str

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        # https://learn.microsoft.com/en-us/azure/api-management/set-body-policy
        # In the outbound section set-body sets the response body.
        body = render_policy_value(self.value, req, runtime).encode("utf-8")
        if req.in_outbound:
            req.response_body = body
        else:
            req.body = body
        _record_step(runtime, "set-body", {"length": len(body)})
        return None


@dataclass(frozen=True)
class ReturnResponse(PolicyNode):
    status_code: int
    reason: str | None = None
    headers: list[SetHeader] = field(default_factory=list)
    body: str | None = None
    media_type: str | None = None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        out_headers: dict[str, str] = {}
        temp_req = PolicyRequest(
            method=req.method,
            path=req.path,
            query=dict(req.query),
            headers=out_headers,
            variables=req.variables,
            body=req.body,
            response_status_code=req.response_status_code,
        )
        for header in self.headers:
            header.apply(temp_req, runtime)

        body = render_policy_value(self.body or "", req, runtime)
        _record_step(runtime, "return-response", {"status_code": self.status_code})
        return ResponseSpec(
            status_code=self.status_code,
            headers=out_headers,
            body=body.encode("utf-8"),
            media_type=self.media_type or out_headers.get("content-type"),
        )


def _encode_mock_response_example(value: Any, *, content_type: str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        if content_type and "json" in content_type.lower():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return value.encode("utf-8")
            return json.dumps(parsed).encode("utf-8")
        return value.encode("utf-8")
    if content_type and "json" in content_type.lower():
        return json.dumps(value).encode("utf-8")
    return str(value).encode("utf-8")


def _mock_operation(req: PolicyRequest, runtime: PolicyRuntime | None) -> Any | None:
    """The catalogue operation this request maps to, if it can be resolved.

    Mocking is driven by the API catalogue, so a route with no api/operation
    pair, or one naming something absent, has no sample to return.
    """
    if runtime is None or runtime.gateway_config is None:
        return None
    api_id = str(req.variables.get("api_id") or "")
    operation_id = str(req.variables.get("operation_id") or "")
    if not api_id or not operation_id:
        return None
    api = runtime.gateway_config.apis.get(api_id)
    if api is None:
        return None
    return api.operations.get(operation_id)


def _mock_representation(operation: Any, *, status_code: int, content_type: str | None) -> Any | None:
    """The response representation to mock.

    Prefers the response declared for this status code, falling back to the
    first declared response: a mock-response naming a status the operation does
    not document is still better served by an example than by an empty body.
    """
    candidates = [item for item in operation.responses if item.status_code == status_code]
    if not candidates and operation.responses:
        candidates = [operation.responses[0]]
    if not candidates:
        return None

    representations = list(candidates[0].representations)
    if not representations:
        return None
    if content_type:
        matched = next(
            (r for r in representations if r.content_type.lower() == content_type.lower()),
            None,
        )
        if matched is not None:
            return matched
    return representations[0]


def _mock_response_sample(
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    *,
    status_code: int,
    content_type: str | None,
) -> tuple[bytes, str | None]:
    """The body a `mock-response` should return, from the API catalogue."""
    operation = _mock_operation(req, runtime)
    if operation is None:
        return b"", content_type

    representation = _mock_representation(operation, status_code=status_code, content_type=content_type)
    if representation is None:
        return b"", content_type

    resolved_content_type = content_type or representation.content_type
    for example in representation.examples:
        if example.value is not None:
            return (
                _encode_mock_response_example(example.value, content_type=resolved_content_type),
                resolved_content_type,
            )
    return b"", resolved_content_type


@dataclass(frozen=True)
class MockResponse(PolicyNode):
    status_code: int = 200
    content_type: str | None = None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        body, media_type = _mock_response_sample(
            req,
            runtime,
            status_code=self.status_code,
            content_type=self.content_type,
        )
        headers: dict[str, str] = {}
        if media_type:
            headers["content-type"] = media_type
        _record_step(
            runtime,
            "mock-response",
            {
                "status_code": self.status_code,
                "content_type": media_type,
                "has_body": bool(body),
            },
        )
        return ResponseSpec(
            status_code=self.status_code,
            headers=headers,
            body=body,
            media_type=media_type,
        )


@dataclass(frozen=True)
class Choose(PolicyNode):
    branches: list[tuple[Condition, list[PolicyNode]]]
    otherwise: list[PolicyNode]

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        raise RuntimeError("Choose must be executed through apply_async")

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        for cond, steps in self.branches:
            if cond(req):
                _record_step(runtime, "choose", {"branch": "when"})
                return await _apply_steps_async(steps, req, runtime)
        _record_step(runtime, "choose", {"branch": "otherwise"})
        return await _apply_steps_async(self.otherwise, req, runtime)


def _json_error_response(status_code: int, message: str) -> ResponseSpec:
    """APIM's JSON error envelope, as the other policies in this module return."""
    return ResponseSpec(
        status_code=status_code,
        headers={"content-type": "application/json"},
        body=_json_throttle_body(status_code, message),
    )


@dataclass(frozen=True)
class CheckHeader(PolicyNode):
    """Require a request header, optionally with one of a set of values.

    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy
    name, failed-check-httpcode, failed-check-error-message and ignore-case are
    all required and may be policy expressions. The docs do not say how a header
    sent several times is compared; the value is compared as the gateway
    received it (repeated headers already joined by the HTTP layer).
    The docs also do not show the error body; the caller gets the configured
    message in the JSON envelope the other policies use.
    """

    name: str
    status_code: str
    message: str
    ignore_case: str
    values: tuple[str, ...]

    def _resolve_status(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> int:
        raw = str(render_policy_value(self.status_code, req, runtime)).strip()
        try:
            return int(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=500, detail="check-header failed-check-httpcode must be an integer"
            ) from exc

    def _resolve_ignore_case(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> bool:
        raw = str(render_policy_value(self.ignore_case, req, runtime)).strip().lower()
        if raw not in {"true", "false"}:
            raise HTTPException(status_code=500, detail="check-header ignore-case must be true or false")
        return raw == "true"

    def _value_allowed(self, actual: str, ignore_case: bool) -> bool:
        if not self.values:
            return True
        if ignore_case:
            return actual.lower() in {value.lower() for value in self.values}
        return actual in self.values

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        name = render_policy_value(self.name, req, runtime).strip().lower()
        actual = req.headers.get(name)
        if actual is not None and self._value_allowed(actual, self._resolve_ignore_case(req, runtime)):
            return None
        req.variables["_policy_error_reason"] = "HeaderNotFound" if actual is None else "HeaderValueNotAllowed"
        return _json_error_response(self._resolve_status(req, runtime), render_policy_value(self.message, req, runtime))


IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True)
class IpFilter(PolicyNode):
    """Allow or forbid calls by caller address.

    https://learn.microsoft.com/en-us/azure/api-management/ip-filter-policy
    Error messages are the predefined ones in
    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    The docs do not state the status code or body the caller sees; 403 with the
    JSON error envelope is used, carrying the predefined message.
    """

    action: str
    ranges: tuple[tuple[IpAddress, IpAddress], ...]

    def _matches(self, ip: IpAddress) -> bool:
        return any(low.version == ip.version and low <= ip <= high for low, high in self.ranges)

    def _resolve_action(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> str:
        action = render_policy_value(self.action, req, runtime).strip().lower()
        if action not in {"allow", "forbid"}:
            raise HTTPException(status_code=500, detail="ip-filter action must be allow or forbid")
        return action

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        action = self._resolve_action(req, runtime)
        ip_raw = req.variables.get("client_ip")
        try:
            ip = ipaddress.ip_address(ip_raw.strip()) if isinstance(ip_raw, str) else None
        except ValueError:
            ip = None
        if ip is None:
            req.variables["_policy_error_reason"] = "FailedToParseCallerIP"
            return _json_error_response(403, "Failed to establish IP address for the caller. Access denied.")
        matched = self._matches(ip)
        if action == "allow" and not matched:
            req.variables["_policy_error_reason"] = "CallerIpNotAllowed"
            return _json_error_response(403, f"Caller IP address {ip} is not allowed. Access denied.")
        if action == "forbid" and matched:
            req.variables["_policy_error_reason"] = "CallerIpBlocked"
            return _json_error_response(403, "Caller IP address is blocked. Access denied.")
        return None


_CORS_DEFAULT_PORTS = {"http": 80, "https": 443}


def _cors_origin_key(value: str) -> str:
    """Comparable form of an origin: lower case, default port made explicit.

    The docs say an omitted port means 80 for HTTP and 443 for HTTPS, and their
    own example lists origins with a trailing slash, while browsers send none.
    """
    text = value.strip().rstrip("/").lower()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return text
    if not parts.scheme or not parts.hostname:
        return text
    return f"{parts.scheme}://{parts.hostname}:{port or _CORS_DEFAULT_PORTS.get(parts.scheme, 0)}"


def _header_ci(headers: dict[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


@dataclass(frozen=True)
class Cors(PolicyNode):
    """The `cors` policy.

    https://learn.microsoft.com/en-us/azure/api-management/cors-policy

    The gateway answers a preflight through `apply_preflight` before the normal
    pipeline runs; `apply` handles the actual (simple or approved) request.

    Documentation gaps, chosen deliberately:
    - `terminate-unmatched-request`: the attributes table says the default is
      `false`, while "Common configuration issues" says the default is `true`.
      We follow the latter, which describes the observed empty 200 OK.
    - The docs say `allow-credentials` shapes the preflight response only; we
      also send it on actual responses, since a browser needs it there.
    - With `*` origins and `allow-credentials="true"` the docs are silent. We
      echo the request origin (a literal `*` is rejected by browsers when
      credentials are on). `*` methods/headers echo the requested ones likewise.
    - Whether a preflight's requested method/headers are checked against the
      lists is undocumented; we advertise the lists and leave enforcement to
      the browser. The required `allowed-headers` element is not enforced.
    """

    origins: tuple[str, ...] = ()
    methods: tuple[str, ...] = ("GET", "POST")
    headers: tuple[str, ...] = ()
    expose_headers: tuple[str, ...] = ()
    allow_credentials: str | None = None
    terminate_unmatched_request: str | None = None
    preflight_max_age: str | None = None

    def _origin_matches(self, origin: str) -> bool:
        key = _cors_origin_key(origin)
        return any(item.strip() == "*" or _cors_origin_key(item) == key for item in self.origins)

    def _terminates_unmatched(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> bool:
        return _policy_bool(self.terminate_unmatched_request, req, runtime, default=True)

    def _allow_origin_value(self, origin: str, credentials: bool) -> str:
        wildcard = any(item.strip() == "*" for item in self.origins)
        return "*" if wildcard and not credentials else origin

    def _common_headers(self, origin: str, req: PolicyRequest, runtime: PolicyRuntime | None) -> dict[str, str]:
        credentials = _policy_bool(self.allow_credentials, req, runtime)
        out = {"access-control-allow-origin": self._allow_origin_value(origin, credentials)}
        if out["access-control-allow-origin"] != "*":
            out["vary"] = "Origin"
        if credentials:
            out["access-control-allow-credentials"] = "true"
        return out

    def preflight_headers(self, origin: str, req: PolicyRequest, runtime: PolicyRuntime | None) -> dict[str, str]:
        out = self._common_headers(origin, req, runtime)
        requested_method = _header_ci(req.headers, "access-control-request-method") or "*"
        requested_headers = _header_ci(req.headers, "access-control-request-headers") or "*"
        methods = [str(render_policy_value(m, req, runtime)).upper() for m in self.methods]
        out["access-control-allow-methods"] = requested_method if "*" in methods else ", ".join(methods)
        if self.headers:
            out["access-control-allow-headers"] = requested_headers if "*" in self.headers else ", ".join(self.headers)
        out["access-control-max-age"] = str(_policy_int(self.preflight_max_age, req, runtime, default=0))
        return out

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        origin = _header_ci(req.headers, "origin")
        if origin is None:
            return None
        if self._origin_matches(origin):
            for name, value in self._common_headers(origin, req, runtime).items():
                _queue_response_header(req, name, value)
            if self.expose_headers:
                _queue_response_header(req, "access-control-expose-headers", ", ".join(self.expose_headers))
            _record_step(runtime, "cors", {"origin": origin, "matched": True})
            return None
        _record_step(runtime, "cors", {"origin": origin, "matched": False})
        if req.method.upper() in {"GET", "HEAD"} and self._terminates_unmatched(req, runtime):
            return ResponseSpec(status_code=200, headers={})
        return None


async def apply_preflight(
    docs: list[PolicyDocument], req: PolicyRequest, runtime: PolicyRuntime | None = None
) -> ResponseSpec | None:
    """Answer a CORS preflight from the in-scope `cors` policies.

    Returns None when no `cors` policy is in scope (the caller then treats the
    request as an ordinary OPTIONS). Only cors is evaluated; the other policies
    run on the approved request.
    """
    origin = _header_ci(req.headers, "origin") or ""
    policies = [step for _, step in _effective_section_steps(docs, "inbound") if isinstance(step, Cors)]
    if not policies:
        return None
    for policy in policies:
        if policy._origin_matches(origin):
            return ResponseSpec(status_code=200, headers=policy.preflight_headers(origin, req, runtime))
        if policy._terminates_unmatched(req, runtime):
            break
    return ResponseSpec(status_code=200, headers={})


@dataclass(frozen=True)
class ThrottleRule:
    target_kind: str | None
    target_name: str | None
    target_id: str | None
    calls: int | None
    renewal_period: int
    bandwidth: int | None = None


@dataclass(frozen=True)
class RateLimit(PolicyNode):
    calls: int
    renewal_period: int
    retry_after_header_name: str | None = None
    retry_after_variable_name: str | None = None
    remaining_calls_header_name: str | None = None
    remaining_calls_variable_name: str | None = None
    total_calls_header_name: str | None = None
    rules: tuple[ThrottleRule, ...] = ()

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        store = req.variables.get("rate_limit_store")
        key = _subscription_throttle_key(req, "rate-limit")
        if not isinstance(store, dict) or key is None:
            return None

        now = _policy_now(runtime)
        rules = self._applicable_rules(req)
        buckets: list[tuple[ThrottleRule, list[float], str]] = []
        for rule in rules:
            rule_key = _throttle_rule_key(key, rule)
            bucket = _rate_limit_bucket(store, rule_key)
            _prune_rate_limit_bucket(bucket, now, rule.renewal_period)
            if len(bucket) >= rule.calls:
                remaining = max(0, rule.calls - len(bucket))
                return _rate_limit_response(
                    req,
                    runtime,
                    calls=rule.calls,
                    retry_after=_rate_limit_retry_after(bucket, now, rule.renewal_period),
                    remaining=remaining,
                    retry_after_header_name=self.retry_after_header_name,
                    retry_after_variable_name=self.retry_after_variable_name,
                    remaining_calls_header_name=self.remaining_calls_header_name,
                    remaining_calls_variable_name=self.remaining_calls_variable_name,
                    total_calls_header_name=self.total_calls_header_name,
                )
            buckets.append((rule, bucket, rule_key))

        for _, bucket, _ in buckets:
            bucket.append(now)
        root_bucket = buckets[0][1]
        remaining = max(0, self.calls - len(root_bucket))
        _publish_rate_counts(
            req,
            runtime,
            remaining=remaining,
            calls=self.calls,
            remaining_calls_header_name=self.remaining_calls_header_name,
            remaining_calls_variable_name=self.remaining_calls_variable_name,
            total_calls_header_name=self.total_calls_header_name,
        )
        _record_step(runtime, "rate-limit", {"count": len(root_bucket), "remaining": remaining})
        return None

    def _applicable_rules(self, req: PolicyRequest) -> tuple[ThrottleRule, ...]:
        root = ThrottleRule(None, None, None, self.calls, self.renewal_period)
        return (root, *(rule for rule in self.rules if _throttle_rule_matches(rule, req)))


@dataclass(frozen=True)
class Quota(PolicyNode):
    calls: int | None
    renewal_period: int
    bandwidth: int | None = None
    rules: tuple[ThrottleRule, ...] = ()

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        store = req.variables.get("quota_store")
        key = _subscription_throttle_key(req, "quota")
        if not isinstance(store, dict) or key is None:
            return None

        now = _policy_now(runtime)
        rules = self._applicable_rules(req)
        entries: list[tuple[ThrottleRule, dict[str, Any], int | None, str]] = []
        for rule in rules:
            rule_key = _throttle_rule_key(key, rule)
            entry, reset_at = _quota_window_state(
                store,
                rule_key,
                now=now,
                renewal_period=self.renewal_period if rule.target_kind is None else rule.renewal_period,
                first_period_start=None,
            )
            if _quota_limit_exceeded(entry, rule):
                return _quota_response(
                    now=now,
                    reset_at=reset_at,
                    bandwidth_exceeded=_quota_bandwidth_exceeded(entry, rule),
                )
            entries.append((rule, entry, reset_at, rule_key))

        for rule, entry, _, rule_key in entries:
            if rule.calls is not None:
                entry["count"] = int(entry.get("count") or 0) + 1
            if rule.bandwidth is not None:
                _queue_quota_finalization(req, runtime, rule, rule_key)
        _record_step(runtime, "quota", {"count": entries[0][1].get("count", 0)})
        return None

    def _applicable_rules(self, req: PolicyRequest) -> tuple[ThrottleRule, ...]:
        root = ThrottleRule(None, None, None, self.calls, self.renewal_period, self.bandwidth)
        return (root, *(rule for rule in self.rules if _throttle_rule_matches(rule, req)))


def _request_headers(req: PolicyRequest) -> dict[str, str]:
    return req.headers


def _request_query(req: PolicyRequest) -> dict[str, str]:
    return req.query


def _response_header_target(req: PolicyRequest) -> dict[str, str]:
    if req.response_headers is not None:
        return req.response_headers
    return req.headers


def _pending_response_headers(req: PolicyRequest) -> dict[str, str]:
    pending = req.variables.get("_pending_response_headers")
    if isinstance(pending, dict):
        return pending
    pending = {}
    req.variables["_pending_response_headers"] = pending
    return pending


def _queue_response_header(req: PolicyRequest, name: str, value: Any) -> None:
    _pending_response_headers(req)[name.lower()] = _stringify_policy_value(value)


def apply_pending_response_headers(req: PolicyRequest, headers: dict[str, str]) -> None:
    pending = req.variables.get("_pending_response_headers")
    if not isinstance(pending, dict):
        return
    for name, value in pending.items():
        headers[name.lower()] = str(value)


def _policy_bool(
    value: str | None,
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
    *,
    default: bool = False,
) -> bool:
    if value is None:
        return default
    resolved = evaluate_policy_value(value, req, runtime)
    if isinstance(resolved, bool):
        return resolved
    text = _stringify_policy_value(resolved).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    return default


def _policy_int(
    value: str | None,
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
    *,
    default: int = 0,
) -> int:
    if value is None:
        return default
    resolved = evaluate_policy_value(value, req, runtime)
    if isinstance(resolved, bool):
        return int(resolved)
    if isinstance(resolved, (int, float)):
        return int(resolved)
    text = _stringify_policy_value(resolved).strip()
    if not text:
        return default
    return int(float(text))


def _validated_policy_int(
    value: str | None,
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    *,
    name: str,
    minimum: int,
) -> int:
    try:
        number = _policy_int(value, req, runtime, default=0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"{name} must be an integer") from exc
    if number < minimum:
        raise HTTPException(status_code=500, detail=f"{name} must be >= {minimum}")
    return number


def _positive_policy_int(value: str | None, req: PolicyRequest, runtime: PolicyRuntime | None, *, name: str) -> int:
    return _validated_policy_int(value, req, runtime, name=name, minimum=1)


def _nonnegative_policy_int(
    value: str | None,
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    *,
    name: str,
    default: int = 1,
) -> int:
    return _validated_policy_int(value or str(default), req, runtime, name=name, minimum=0)


def _rate_period(value: str | None, req: PolicyRequest, runtime: PolicyRuntime | None) -> int:
    period = _validated_policy_int(value, req, runtime, name="rate-limit-by-key renewal-period", minimum=1)
    if period > 300:
        raise HTTPException(status_code=500, detail="rate-limit-by-key renewal-period must be <= 300")
    return period


def _policy_float(
    value: str | None,
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
    *,
    default: float = 0.0,
) -> float:
    if value is None:
        return default
    resolved = evaluate_policy_value(value, req, runtime)
    if isinstance(resolved, bool):
        return float(resolved)
    if isinstance(resolved, (int, float)):
        return float(resolved)
    text = _stringify_policy_value(resolved).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid numeric policy value: {text}") from exc


def _is_deferred_expression(value: str | None) -> bool:
    return value is not None and is_apim_expression(value)


def _policy_now(runtime: PolicyRuntime | None) -> float:
    """Read the runtime clock, falling back to wall time for normal requests."""
    return runtime.clock() if runtime is not None and runtime.clock is not None else time.time()


def _normalize_cache_caching_type(caching_type: str | None) -> tuple[str, bool]:
    normalized = (caching_type or "prefer-external").strip().lower() or "prefer-external"
    if normalized == "external":
        raise HTTPException(status_code=500, detail="Unsupported caching-type external")
    if normalized not in {"internal", "prefer-external"}:
        raise HTTPException(status_code=500, detail=f"Unsupported caching-type {normalized}")
    if normalized == "prefer-external":
        return "internal", True
    return "internal", False


def _normalize_downstream_caching_type(caching_type: str | None) -> str:
    mode = (caching_type or "none").strip().lower() or "none"
    if mode not in {"none", "private", "public"}:
        raise HTTPException(status_code=500, detail=f"Unsupported downstream-caching-type {mode}")
    return mode


def _validate_cache_enum(value: str | None, *, name: str, allowed: set[str], allow_expression: bool = False) -> None:
    # Learn documents the allowed values but not the gateway's validation
    # status/message; the simulator keeps its policy-configuration 500 shape.
    if value is None or not value.strip():
        return
    normalized = value.strip().lower()
    if allow_expression and is_apim_expression(value):
        return
    if normalized in allowed:
        return
    raise HTTPException(status_code=500, detail=f"Unsupported {name} {normalized}")


def _cleanup_value_cache(store: dict[str, Any], key: str, now: float) -> ValueCacheEntry | None:
    entry = store.get(key)
    if not isinstance(entry, ValueCacheEntry):
        return None
    if entry.expires_at <= now:
        store.pop(key, None)
        return None
    return entry


def _vary_values(values: list[str]) -> list[str]:
    out: list[str] = []
    for item in values:
        for part in item.split(";"):
            value = part.strip()
            if value:
                out.append(value)
    return out


def _build_response_cache_key(
    req: PolicyRequest,
    *,
    vary_by_headers: list[str],
    vary_by_query_parameters: list[str],
    vary_by_developer: bool,
    vary_by_developer_groups: bool,
) -> str:
    request_headers = _request_headers(req)
    request_query = _request_query(req)
    query_names = vary_by_query_parameters or sorted(request_query.keys())
    query_part = {name: request_query.get(name, "") for name in query_names}
    header_part = {name.lower(): request_headers.get(name.lower(), "") for name in vary_by_headers}
    developer = str(req.variables.get("subscription_owner") or "anonymous") if vary_by_developer else ""
    groups = req.variables.get("subscription_groups")
    group_part = sorted(str(item) for item in groups) if vary_by_developer_groups and isinstance(groups, list) else []
    return json.dumps(
        {
            "route": str(req.variables.get("route") or ""),
            "method": req.method.upper(),
            "path": req.path,
            "query": query_part,
            "headers": header_part,
            "developer": developer,
            "groups": group_part,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _apply_downstream_cache_headers(
    headers: dict[str, str],
    *,
    downstream_caching_type: str,
    must_revalidate: bool,
) -> None:
    # Learn defines the modes and must-revalidate directive, but not the
    # exact serialized Cache-Control value; retain the local header contract.
    mode = _normalize_downstream_caching_type(downstream_caching_type)
    if mode == "none":
        headers["cache-control"] = "no-store"
        return
    directives = [mode]
    if must_revalidate:
        directives.append("must-revalidate")
    headers["cache-control"] = ", ".join(directives)


def _rate_limit_bucket(store: dict[str, Any], key: str) -> list[float]:
    bucket = store.get(key)
    if not isinstance(bucket, list):
        bucket = []
        store[key] = bucket
    return bucket


def _prune_rate_limit_bucket(bucket: list[float], now: float, renewal_period: int) -> None:
    threshold = now - renewal_period
    while bucket and bucket[0] <= threshold:
        bucket.pop(0)


def _rate_limit_retry_after(bucket: list[float], now: float, renewal_period: int) -> int:
    if not bucket:
        return renewal_period
    earliest = bucket[0]
    return max(1, math.ceil((earliest + renewal_period) - now))


def _subscription_throttle_key(req: PolicyRequest, prefix: str) -> str | None:
    """The counter for a subscription-scoped throttle, or None without a subscription.

    Product, API and operation limits are applied independently
    (https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy),
    so the counter is keyed by the scope that authored the policy as well as by
    the subscription.
    """
    subscription_id = str(req.variables.get("subscription_id") or "")
    if not subscription_id:
        return None
    scope = str(req.variables.get("_policy_scope") or "")
    return f"{prefix}:{scope}:subscription:{subscription_id}"


def _throttle_rule_key(base_key: str, rule: ThrottleRule) -> str:
    if rule.target_kind is None:
        return base_key
    target = rule.target_id or rule.target_name or ""
    return f"{base_key}:{rule.target_kind}:{target}"


def _throttle_rule_matches(rule: ThrottleRule, req: PolicyRequest) -> bool:
    if rule.target_kind is None:
        return True
    if rule.target_id is not None:
        return str(req.variables.get(f"{rule.target_kind}_id") or "") == rule.target_id
    values = (
        req.variables.get(f"{rule.target_kind}_name"),
        req.variables.get(f"{rule.target_kind}_id"),
    )
    return rule.target_name in {str(value) for value in values if value is not None}


def _json_throttle_body(status_code: int, message: str) -> bytes:
    return json.dumps({"statusCode": status_code, "message": message}, separators=(",", ":")).encode("utf-8")


def _rate_limit_response(
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    *,
    calls: int,
    retry_after: int,
    remaining: int,
    retry_after_header_name: str | None,
    retry_after_variable_name: str | None,
    remaining_calls_header_name: str | None,
    remaining_calls_variable_name: str | None,
    total_calls_header_name: str | None,
) -> ResponseSpec:
    if retry_after_variable_name:
        req.variables[retry_after_variable_name] = retry_after
        _record_variable_write(runtime, retry_after_variable_name, retry_after, "rate-limit")
    if remaining_calls_variable_name:
        req.variables[remaining_calls_variable_name] = remaining
        _record_variable_write(runtime, remaining_calls_variable_name, remaining, "rate-limit")
    headers = {
        "content-type": "application/json",
        (retry_after_header_name or "Retry-After").lower(): str(retry_after),
    }
    if remaining_calls_header_name:
        headers[remaining_calls_header_name.lower()] = str(remaining)
    if total_calls_header_name:
        headers[total_calls_header_name.lower()] = str(calls)
    message = f"Rate limit is exceeded. Try again in {retry_after} seconds."
    return ResponseSpec(status_code=429, headers=headers, body=_json_throttle_body(429, message))


def _publish_rate_counts(
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    *,
    remaining: int,
    calls: int,
    remaining_calls_header_name: str | None,
    remaining_calls_variable_name: str | None,
    total_calls_header_name: str | None,
) -> None:
    if remaining_calls_variable_name:
        req.variables[remaining_calls_variable_name] = remaining
        _record_variable_write(runtime, remaining_calls_variable_name, remaining, "rate-limit")
    if remaining_calls_header_name:
        _queue_response_header(req, remaining_calls_header_name, remaining)
    if total_calls_header_name:
        _queue_response_header(req, total_calls_header_name, calls)


def _quota_limit_exceeded(entry: dict[str, Any], rule: ThrottleRule) -> bool:
    if rule.calls is not None and int(entry.get("count") or 0) >= rule.calls:
        return True
    return _quota_bandwidth_exceeded(entry, rule)


def _quota_bandwidth_exceeded(entry: dict[str, Any], rule: ThrottleRule) -> bool:
    return rule.bandwidth is not None and int(entry.get("bandwidth") or 0) >= rule.bandwidth


def _timespan(total_seconds: int) -> str:
    """Seconds in .NET TimeSpan's default form: hh:mm:ss, prefixed by d. past a day.

    The Learn error table shows the replenish time as xx:xx:xx; APIM renders a
    .NET TimeSpan, which adds a day component once the interval passes 24 hours.
    """
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    clock = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}.{clock}" if days else clock


def _quota_response(*, now: float, reset_at: int | None, bandwidth_exceeded: bool) -> ResponseSpec:
    retry_after = max(0, math.ceil(reset_at - now)) if reset_at is not None else 0
    kind = "bandwidth" if bandwidth_exceeded else "call volume"
    message = f"Out of {kind} quota. Quota will be replenished in {_timespan(retry_after)}."
    return ResponseSpec(
        status_code=403,
        headers={"content-type": "application/json", "retry-after": str(retry_after)},
        body=_json_throttle_body(403, message),
    )


def _quota_bandwidth_kilobytes(req: PolicyRequest) -> int:
    # The Learn policy references define the unit but not the rounding rule;
    # the simulator counts the request and response bodies in whole KB, rounding
    # up so a non-empty partial kilobyte is not silently free.
    total_bytes = len(req.body) + len(req.response_body)
    return math.ceil(total_bytes / 1024) if total_bytes else 0


def _queue_quota_finalization(
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    rule: ThrottleRule,
    key: str,
) -> None:
    if runtime is None:
        return
    runtime.deferred_actions.append(
        QuotaDeferred(
            key=key,
            renewal_period=rule.renewal_period,
            bandwidth=rule.bandwidth or 0,
        )
    )


def _quota_window_state(
    store: dict[str, Any],
    key: str,
    *,
    now: float,
    renewal_period: int,
    first_period_start: str | None,
) -> tuple[dict[str, Any], int | None]:
    if renewal_period == 0:
        entry = store.get(key)
        if not isinstance(entry, dict):
            entry = {"window_start": None, "count": 0, "bandwidth": 0}
            store[key] = entry
        return entry, None

    if renewal_period < 0:
        raise HTTPException(status_code=500, detail="quota renewal-period must be >= 0")

    previous = store.get(key)
    if first_period_start is None:
        # The local subscription model has no creation timestamp. APIM anchors
        # quota windows to that timestamp; first observation is the deterministic
        # fallback until the model can carry the real subscription start.
        anchor = float(previous.get("anchor", now)) if isinstance(previous, dict) else now
    elif first_period_start != "0001-01-01T00:00:00Z":
        anchor = datetime.strptime(first_period_start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    else:
        anchor = 0.0

    if now < anchor:
        window_start = anchor
    else:
        window_index = int((now - anchor) // renewal_period)
        window_start = anchor + (window_index * renewal_period)

    entry = store.get(key)
    if not isinstance(previous, dict) or previous.get("window_start") != window_start:
        entry = {"window_start": window_start, "anchor": anchor, "count": 0, "bandwidth": 0}
        store[key] = entry
    else:
        entry = previous

    reset_at = int(window_start + renewal_period)
    return entry, reset_at


@dataclass(frozen=True)
class QuotaDeferred(DeferredPolicyAction):
    key: str
    renewal_period: int
    bandwidth: int

    def finalize(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        store = req.variables.get("quota_store")
        if not isinstance(store, dict):
            return
        entry, _ = _quota_window_state(
            store,
            self.key,
            now=_policy_now(runtime),
            renewal_period=self.renewal_period,
            first_period_start=None,
        )
        entry["bandwidth"] = int(entry.get("bandwidth") or 0) + _quota_bandwidth_kilobytes(req)
        _record_step(
            runtime,
            "quota",
            {"deferred": True, "bandwidth": entry["bandwidth"], "key": self.key},
        )


@dataclass(frozen=True)
class RateLimitByKeyDeferred(DeferredPolicyAction):
    calls: str
    renewal_period: str
    counter_key: str
    increment_condition: str | None
    increment_count: str | None
    retry_after_header_name: str | None
    retry_after_variable_name: str | None
    remaining_calls_header_name: str | None
    remaining_calls_variable_name: str | None
    total_calls_header_name: str | None

    def finalize(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        store = req.variables.get("rate_limit_store")
        if not isinstance(store, dict):
            return
        calls = _positive_policy_int(self.calls, req, runtime, name="rate-limit-by-key calls")
        renewal_period = _rate_period(self.renewal_period, req, runtime)
        counter_key = render_policy_value(self.counter_key, req, runtime)
        now = _policy_now(runtime)
        bucket = _rate_limit_bucket(store, f"rate-limit-by-key:{counter_key}")
        _prune_rate_limit_bucket(bucket, now, renewal_period)
        should_increment = _policy_bool(self.increment_condition, req, runtime, default=True)
        increment = _nonnegative_policy_int(
            self.increment_count, req, runtime, name="rate-limit-by-key increment-count"
        )
        if should_increment and increment:
            bucket.extend([now] * increment)
        remaining = max(0, calls - len(bucket))
        if self.remaining_calls_variable_name:
            req.variables[self.remaining_calls_variable_name] = remaining
            _record_variable_write(runtime, self.remaining_calls_variable_name, remaining, "rate-limit-by-key")
        if self.remaining_calls_header_name:
            _queue_response_header(req, self.remaining_calls_header_name, remaining)
        if self.total_calls_header_name:
            _queue_response_header(req, self.total_calls_header_name, calls)
        _record_step(
            runtime,
            "rate-limit-by-key",
            {
                "counter_key": counter_key,
                "deferred": True,
                "count": len(bucket),
                "remaining": remaining,
            },
        )


def _claim_quota_key_increment(req: PolicyRequest, counter_key: str) -> bool:
    """True the first time this request increments a quota-by-key counter.

    https://learn.microsoft.com/en-us/azure/api-management/quota-by-key-policy
    says a key shared by several policies is incremented only once per request.
    """
    claimed = req.variables.setdefault("_quota_by_key_incremented", set())
    if counter_key in claimed:
        return False
    claimed.add(counter_key)
    return True


@dataclass(frozen=True)
class QuotaByKeyDeferred(DeferredPolicyAction):
    calls: int
    renewal_period: int
    counter_key: str
    increment_condition: str | None
    increment_count: str | None
    first_period_start: str | None

    def finalize(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        store = req.variables.get("quota_store")
        if not isinstance(store, dict):
            return
        renewal_period = self.renewal_period
        counter_key = render_policy_value(self.counter_key, req, runtime)
        now = _policy_now(runtime)
        entry, _ = _quota_window_state(
            store,
            f"quota-by-key:{counter_key}",
            now=now,
            renewal_period=renewal_period,
            first_period_start=self.first_period_start,
        )
        should_increment = _policy_bool(self.increment_condition, req, runtime, default=True)
        increment = _nonnegative_policy_int(self.increment_count, req, runtime, name="quota-by-key increment-count")
        if should_increment and increment and _claim_quota_key_increment(req, counter_key):
            entry["count"] = int(entry.get("count") or 0) + increment
        _record_step(
            runtime,
            "quota-by-key",
            {
                "counter_key": counter_key,
                "deferred": True,
                "count": int(entry.get("count") or 0),
            },
        )


@dataclass(frozen=True)
class RateLimitByKey(PolicyNode):
    calls: str
    renewal_period: str
    counter_key: str
    increment_condition: str | None = None
    increment_count: str | None = None
    retry_after_header_name: str | None = None
    retry_after_variable_name: str | None = None
    remaining_calls_header_name: str | None = None
    remaining_calls_variable_name: str | None = None
    total_calls_header_name: str | None = None

    def _defer(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> None:
        """Queue the increment for after the response is known."""
        if runtime is None:
            return
        runtime.deferred_actions.append(
            RateLimitByKeyDeferred(
                calls=self.calls,
                renewal_period=self.renewal_period,
                counter_key=self.counter_key,
                increment_condition=self.increment_condition,
                increment_count=self.increment_count,
                retry_after_header_name=self.retry_after_header_name,
                retry_after_variable_name=self.retry_after_variable_name,
                remaining_calls_header_name=self.remaining_calls_header_name,
                remaining_calls_variable_name=self.remaining_calls_variable_name,
                total_calls_header_name=self.total_calls_header_name,
            )
        )

    def _publish_counts(self, req: PolicyRequest, runtime: PolicyRuntime | None, *, remaining: int, calls: int) -> None:
        """Expose the remaining and total call counts where they were asked for."""
        if self.remaining_calls_variable_name:
            req.variables[self.remaining_calls_variable_name] = remaining
            _record_variable_write(runtime, self.remaining_calls_variable_name, remaining, "rate-limit-by-key")
        if self.remaining_calls_header_name:
            _queue_response_header(req, self.remaining_calls_header_name, remaining)
        if self.total_calls_header_name:
            _queue_response_header(req, self.total_calls_header_name, calls)

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        """Count this call against a keyed bucket, or refuse it.

        When the increment depends on the response, the counting is deferred and
        this stage only checks whether the bucket is already full.
        """
        store = req.variables.get("rate_limit_store")
        if not isinstance(store, dict):
            return None
        calls = _positive_policy_int(self.calls, req, runtime, name="rate-limit-by-key calls")
        renewal_period = _rate_period(self.renewal_period, req, runtime)
        counter_key = render_policy_value(self.counter_key, req, runtime)

        now = _policy_now(runtime)
        bucket = _rate_limit_bucket(store, f"rate-limit-by-key:{counter_key}")
        _prune_rate_limit_bucket(bucket, now, renewal_period)

        if _is_deferred_expression(self.increment_condition) or _is_deferred_expression(self.increment_count):
            if len(bucket) >= calls:
                return self._limit_response(
                    req,
                    runtime,
                    calls=calls,
                    retry_after=_rate_limit_retry_after(bucket, now, renewal_period),
                    remaining=0,
                )
            self._defer(req, runtime)
            _record_step(
                runtime,
                "rate-limit-by-key",
                {
                    "counter_key": counter_key,
                    "deferred": True,
                    "count": len(bucket),
                    "remaining": max(0, calls - len(bucket)),
                },
            )
            return None

        should_increment = _policy_bool(self.increment_condition, req, runtime, default=True)
        increment = _nonnegative_policy_int(
            self.increment_count, req, runtime, name="rate-limit-by-key increment-count"
        )
        would_exceed = should_increment and len(bucket) + increment > calls
        if should_increment and increment and not would_exceed:
            bucket.extend([now] * increment)

        remaining = max(0, calls - len(bucket))
        self._publish_counts(req, runtime, remaining=remaining, calls=calls)
        _record_step(
            runtime,
            "rate-limit-by-key",
            {"counter_key": counter_key, "count": len(bucket), "remaining": remaining},
        )
        if would_exceed:
            return self._limit_response(
                req,
                runtime,
                calls=calls,
                retry_after=_rate_limit_retry_after(bucket, now, renewal_period),
                remaining=remaining,
            )
        return None

    def _limit_response(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        calls: int,
        retry_after: int,
        remaining: int,
    ) -> ResponseSpec:
        header_name = self.retry_after_header_name or "Retry-After"
        if self.retry_after_variable_name:
            req.variables[self.retry_after_variable_name] = retry_after
            _record_variable_write(runtime, self.retry_after_variable_name, retry_after, "rate-limit-by-key")
        if self.remaining_calls_variable_name:
            req.variables[self.remaining_calls_variable_name] = remaining
            _record_variable_write(runtime, self.remaining_calls_variable_name, remaining, "rate-limit-by-key")
        headers = {"content-type": "application/json", header_name.lower(): str(retry_after)}
        if self.remaining_calls_header_name:
            headers[self.remaining_calls_header_name.lower()] = str(remaining)
        if self.total_calls_header_name:
            headers[self.total_calls_header_name.lower()] = str(calls)
        message = f"Rate limit is exceeded. Try again in {retry_after} seconds."
        return ResponseSpec(status_code=429, headers=headers, body=_json_throttle_body(429, message))


@dataclass(frozen=True)
class QuotaByKey(PolicyNode):
    calls: int
    renewal_period: int
    counter_key: str
    increment_condition: str | None = None
    increment_count: str | None = None
    first_period_start: str | None = None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        store = req.variables.get("quota_store")
        if not isinstance(store, dict):
            return None
        calls = self.calls
        renewal_period = self.renewal_period
        counter_key = render_policy_value(self.counter_key, req, runtime)
        now = _policy_now(runtime)
        entry, reset_at = _quota_window_state(
            store,
            f"quota-by-key:{counter_key}",
            now=now,
            renewal_period=renewal_period,
            first_period_start=self.first_period_start,
        )
        current = int(entry.get("count") or 0)
        if _is_deferred_expression(self.increment_condition) or _is_deferred_expression(self.increment_count):
            if current >= calls:
                return _quota_response(now=now, reset_at=reset_at, bandwidth_exceeded=False)
            if runtime is not None:
                runtime.deferred_actions.append(
                    QuotaByKeyDeferred(
                        calls=self.calls,
                        renewal_period=self.renewal_period,
                        counter_key=self.counter_key,
                        increment_condition=self.increment_condition,
                        increment_count=self.increment_count,
                        first_period_start=self.first_period_start,
                    )
                )
            _record_step(runtime, "quota-by-key", {"counter_key": counter_key, "deferred": True, "count": current})
            return None

        should_increment = _policy_bool(self.increment_condition, req, runtime, default=True)
        increment = _nonnegative_policy_int(self.increment_count, req, runtime, name="quota-by-key increment-count")
        would_exceed = should_increment and current + increment > calls
        if should_increment and increment and not would_exceed and _claim_quota_key_increment(req, counter_key):
            current += increment
            entry["count"] = current
        _record_step(runtime, "quota-by-key", {"counter_key": counter_key, "count": current})
        if would_exceed:
            return _quota_response(now=now, reset_at=reset_at, bandwidth_exceeded=False)
        return None


LLM_RATE_WINDOW_SECONDS = 60
LLM_QUOTA_PERIODS = ("hourly", "daily", "weekly", "monthly", "yearly")


def _estimate_llm_tokens_from_text(text: str) -> int:
    # Adapted heuristic: Azure estimates from the prompt schema; the simulator
    # approximates roughly four characters per token, which is close enough to
    # exercise limit behaviour locally.
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, math.ceil(len(stripped) / 4))


def _llm_message_part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get("text")
        if isinstance(text, str):
            return text
    return ""


def _llm_message_texts(messages: Any) -> list[str]:
    """Text from a chat `messages` array.

    A message's content is either a plain string or a list of typed parts, and
    both spellings are in use across the providers this gateway fronts.
    """
    if not isinstance(messages, list):
        return []
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            chunks.extend(_llm_message_part_text(part) for part in content)
    return chunks


def _llm_top_level_prompt_texts(payload: dict[str, Any]) -> list[str]:
    """Text from the non-chat prompt fields, each a string or a list of strings."""
    chunks: list[str] = []
    for field_name in ("prompt", "input", "system"):
        value = payload.get(field_name)
        if isinstance(value, str):
            chunks.append(value)
        elif isinstance(value, list):
            chunks.extend(part for part in value if isinstance(part, str))
    return chunks


def _llm_prompt_text(body: bytes) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", errors="replace")
    if not isinstance(payload, dict):
        return ""
    chunks = [*_llm_message_texts(payload.get("messages")), *_llm_top_level_prompt_texts(payload)]
    return "\n".join(chunk for chunk in chunks if chunk)


def _llm_usage_from_response(body: bytes) -> dict[str, int] | None:
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _llm_usage_counts(payload.get("usage"))


def _llm_usage_counts(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None

    def _usage_int(*names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return None

    prompt = _usage_int("prompt_tokens", "input_tokens")
    completion = _usage_int("completion_tokens", "output_tokens")
    total = _usage_int("total_tokens")
    if total is None and prompt is None and completion is None:
        return None
    if total is None:
        total = (prompt or 0) + (completion or 0)
    return {"prompt": prompt or 0, "completion": completion or 0, "total": total}


def _sse_json_payloads(body: bytes) -> Iterator[dict[str, Any]]:
    """Yield the JSON object carried by each `data:` line of an SSE stream.

    Lines that are not data, the `[DONE]` sentinel, and anything that is not a
    JSON object are skipped: a stream is read for what it happens to contain,
    never rejected wholesale.
    """
    for line in body.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload_text = stripped[len("data:") :].strip()
        if not payload_text or payload_text == "[DONE]":
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def _sse_delta_texts(payload: dict[str, Any]) -> list[str]:
    """Completion text from one stream chunk, in either provider's shape.

    OpenAI puts it at `choices[].delta.content`; Anthropic puts it at
    `delta.text`. A chunk may legitimately carry neither.
    """
    parts: list[str] = []
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                parts.append(delta["content"])
    delta = payload.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("text"), str):
        parts.append(delta["text"])
    return parts


def _llm_usage_from_sse(body: bytes) -> tuple[dict[str, int] | None, str]:
    """Scan an SSE stream for a usage payload and collect completion deltas.

    Returns (usage, delta_text): usage from the last chunk that carries one
    (OpenAI stream_options.include_usage or Anthropic message_delta), plus the
    concatenated completion text for estimation when no usage chunk exists.
    """
    usage: dict[str, int] | None = None
    delta_parts: list[str] = []
    for payload in _sse_json_payloads(body):
        chunk_usage = _llm_usage_counts(payload.get("usage"))
        if chunk_usage is not None:
            usage = chunk_usage
        delta_parts.extend(_sse_delta_texts(payload))
    return usage, "".join(delta_parts)


def _looks_like_sse(req: PolicyRequest) -> bool:
    media_type = (req.response_media_type or "").lower()
    if "text/event-stream" in media_type:
        return True
    return req.response_body.lstrip()[:5] == b"data:"


def _llm_observed_usage(req: PolicyRequest, *, estimated_prompt_tokens: int) -> dict[str, int]:
    usage = _llm_usage_from_response(req.response_body)
    if usage is not None:
        return usage
    if _looks_like_sse(req):
        sse_usage, delta_text = _llm_usage_from_sse(req.response_body)
        if sse_usage is not None:
            return sse_usage
        completion = _estimate_llm_tokens_from_text(delta_text)
        total = estimated_prompt_tokens + completion
        return {"prompt": estimated_prompt_tokens, "completion": completion, "total": total}
    status = req.response_status_code
    if status is not None and status >= 400:
        return {"prompt": 0, "completion": 0, "total": 0}
    # Non-JSON responses without a stream carry no usage payload, so fall back
    # to the prompt estimate, mirroring Azure's estimate-on-stream behaviour.
    return {"prompt": estimated_prompt_tokens, "completion": 0, "total": estimated_prompt_tokens}


def _llm_consumed_tokens(req: PolicyRequest, *, estimated_prompt_tokens: int) -> int:
    return _llm_observed_usage(req, estimated_prompt_tokens=estimated_prompt_tokens)["total"]


def _llm_rate_bucket(store: dict[str, Any], key: str) -> list[list[float]]:
    bucket = store.setdefault(key, [])
    if not isinstance(bucket, list):
        bucket = []
        store[key] = bucket
    return bucket


def _prune_llm_rate_bucket(bucket: list[list[float]], now: float) -> None:
    cutoff = now - LLM_RATE_WINDOW_SECONDS
    while bucket and bucket[0][0] <= cutoff:
        bucket.pop(0)


def _llm_rate_tokens_used(bucket: list[list[float]]) -> int:
    return sum(int(entry[1]) for entry in bucket)


def _llm_rate_retry_after(bucket: list[list[float]], now: float) -> int:
    if not bucket:
        return LLM_RATE_WINDOW_SECONDS
    oldest = bucket[0][0]
    return max(1, math.ceil(oldest + LLM_RATE_WINDOW_SECONDS - now))


def _llm_quota_window(now: float, period: str) -> tuple[float, float]:
    moment = datetime.fromtimestamp(now, tz=UTC)
    if period == "hourly":
        start = moment.replace(minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=1)
    elif period == "daily":
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif period == "weekly":
        day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day_start - timedelta(days=moment.weekday())
        end = start + timedelta(days=7)
    elif period == "monthly":
        start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + timedelta(days=32)).replace(day=1)
    else:
        start = moment.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
    return start.timestamp(), end.timestamp()


def _llm_quota_entry(store: dict[str, Any], key: str, *, now: float, period: str) -> dict[str, Any]:
    window_start, window_end = _llm_quota_window(now, period)
    entry = store.get(key)
    if not isinstance(entry, dict) or float(entry.get("window_start") or 0.0) != window_start:
        entry = {"window_start": window_start, "window_end": window_end, "tokens": 0}
        store[key] = entry
    return entry


@dataclass(frozen=True)
class LlmTokenLimitDeferred(DeferredPolicyAction):
    counter_key: str
    tokens_per_minute: int
    token_quota: int
    token_quota_period: str
    estimated_prompt_tokens: int
    remaining_tokens_header_name: str | None
    remaining_tokens_variable_name: str | None
    remaining_quota_tokens_header_name: str | None
    remaining_quota_tokens_variable_name: str | None
    tokens_consumed_header_name: str | None
    tokens_consumed_variable_name: str | None

    def _rate_remaining(self, req: PolicyRequest, *, consumed: int, now: float) -> int | None:
        """Charge the per-minute token bucket and report what is left of it."""
        rate_store = req.variables.get("rate_limit_store")
        if self.tokens_per_minute <= 0 or not isinstance(rate_store, dict):
            return None
        bucket = _llm_rate_bucket(rate_store, f"llm-token-limit:{self.counter_key}")
        _prune_llm_rate_bucket(bucket, now)
        if consumed:
            bucket.append([now, float(consumed)])
        return max(0, self.tokens_per_minute - _llm_rate_tokens_used(bucket))

    def _quota_remaining(self, req: PolicyRequest, *, consumed: int, now: float) -> int | None:
        """Charge the longer-period token quota and report what is left of it."""
        quota_store = req.variables.get("quota_store")
        if self.token_quota <= 0 or not self.token_quota_period or not isinstance(quota_store, dict):
            return None
        entry = _llm_quota_entry(
            quota_store,
            f"llm-token-quota:{self.counter_key}",
            now=now,
            period=self.token_quota_period,
        )
        if consumed:
            entry["tokens"] = int(entry.get("tokens") or 0) + consumed
        return max(0, self.token_quota - int(entry.get("tokens") or 0))

    def _publish(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        headers: dict[str, str],
        *,
        value: int | None,
        header_name: str | None,
        variable_name: str | None,
    ) -> None:
        """Expose one counter as a response header, a policy variable, or both."""
        if value is None:
            return
        if header_name:
            headers[header_name.lower()] = str(value)
        if variable_name:
            req.variables[variable_name] = value
            _record_variable_write(runtime, variable_name, value, "llm-token-limit")

    def finalize(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        """Charge the token budgets once the response is known, and report them.

        Deferred because the consumed-token count is only available after the
        upstream has answered.
        """
        consumed = _llm_consumed_tokens(req, estimated_prompt_tokens=self.estimated_prompt_tokens)
        now = time.time()
        headers = _response_header_target(req)

        remaining_rate = self._rate_remaining(req, consumed=consumed, now=now)
        remaining_quota = self._quota_remaining(req, consumed=consumed, now=now)

        for value, header_name, variable_name in (
            (consumed, self.tokens_consumed_header_name, self.tokens_consumed_variable_name),
            (remaining_rate, self.remaining_tokens_header_name, self.remaining_tokens_variable_name),
            (
                remaining_quota,
                self.remaining_quota_tokens_header_name,
                self.remaining_quota_tokens_variable_name,
            ),
        ):
            self._publish(req, runtime, headers, value=value, header_name=header_name, variable_name=variable_name)

        _record_step(
            runtime,
            "llm-token-limit",
            {
                "counter_key": self.counter_key,
                "deferred": True,
                "tokens_consumed": consumed,
                "remaining_tokens": remaining_rate,
                "remaining_quota_tokens": remaining_quota,
            },
        )


@dataclass(frozen=True)
class LlmTokenLimit(PolicyNode):
    counter_key: str
    estimate_prompt_tokens: str
    tokens_per_minute: str | None = None
    token_quota: str | None = None
    token_quota_period: str | None = None
    retry_after_header_name: str | None = None
    retry_after_variable_name: str | None = None
    remaining_tokens_header_name: str | None = None
    remaining_tokens_variable_name: str | None = None
    remaining_quota_tokens_header_name: str | None = None
    remaining_quota_tokens_variable_name: str | None = None
    tokens_consumed_header_name: str | None = None
    tokens_consumed_variable_name: str | None = None

    def _quota_block(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        quota_store: Any,
        counter_key: str,
        token_quota: int,
        token_quota_period: str,
        estimate: bool,
        estimated_prompt_tokens: int,
        now: float,
    ) -> tuple[int, ResponseSpec | None]:
        """Check the longer-period quota. Returns (tokens used, refusal or None).

        Estimating counts the prompt before the call and refuses when it would
        push past the quota; not estimating only refuses once already at it.
        """
        if token_quota <= 0 or not isinstance(quota_store, dict):
            return 0, None
        entry = _llm_quota_entry(quota_store, f"llm-token-quota:{counter_key}", now=now, period=token_quota_period)
        used = int(entry.get("tokens") or 0)
        blocked = (used + estimated_prompt_tokens > token_quota) if estimate else (used >= token_quota)
        if not blocked:
            return used, None
        retry_after = max(1, math.ceil(float(entry.get("window_end") or now) - now))
        return used, self._limit_response(
            req,
            runtime,
            status_code=403,
            retry_after=retry_after,
            body=f"Token quota is exceeded. Try again in {retry_after} seconds.",
            counter_key=counter_key,
        )

    def _rate_block(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        rate_store: Any,
        counter_key: str,
        tokens_per_minute: int,
        estimate: bool,
        estimated_prompt_tokens: int,
        now: float,
    ) -> tuple[int, ResponseSpec | None]:
        """Check the per-minute budget. Returns (tokens used, refusal or None)."""
        if tokens_per_minute <= 0 or not isinstance(rate_store, dict):
            return 0, None
        bucket = _llm_rate_bucket(rate_store, f"llm-token-limit:{counter_key}")
        _prune_llm_rate_bucket(bucket, now)
        used = _llm_rate_tokens_used(bucket)
        blocked = (used + estimated_prompt_tokens > tokens_per_minute) if estimate else (used >= tokens_per_minute)
        if not blocked:
            return used, None
        retry_after = _llm_rate_retry_after(bucket, now)
        return used, self._limit_response(
            req,
            runtime,
            status_code=429,
            retry_after=retry_after,
            body=f"Token limit is exceeded. Try again in {retry_after} seconds.",
            counter_key=counter_key,
        )

    def _defer(
        self,
        runtime: PolicyRuntime | None,
        *,
        counter_key: str,
        tokens_per_minute: int,
        token_quota: int,
        token_quota_period: str,
        estimated_prompt_tokens: int,
    ) -> None:
        """Queue the real token accounting for after the response is known."""
        if runtime is None:
            return
        runtime.deferred_actions.append(
            LlmTokenLimitDeferred(
                counter_key=counter_key,
                tokens_per_minute=tokens_per_minute,
                token_quota=token_quota,
                token_quota_period=token_quota_period,
                estimated_prompt_tokens=estimated_prompt_tokens,
                remaining_tokens_header_name=self.remaining_tokens_header_name,
                remaining_tokens_variable_name=self.remaining_tokens_variable_name,
                remaining_quota_tokens_header_name=self.remaining_quota_tokens_header_name,
                remaining_quota_tokens_variable_name=self.remaining_quota_tokens_variable_name,
                tokens_consumed_header_name=self.tokens_consumed_header_name,
                tokens_consumed_variable_name=self.tokens_consumed_variable_name,
            )
        )

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        """Refuse calls already over a token budget, and defer the real counting.

        Actual usage is only known once the model has answered, so this stage
        checks the budgets and queues the accounting.
        """
        rate_store = req.variables.get("rate_limit_store")
        quota_store = req.variables.get("quota_store")
        if not isinstance(rate_store, dict) and not isinstance(quota_store, dict):
            return None

        counter_key = render_policy_value(self.counter_key, req, runtime)
        if not counter_key:
            raise HTTPException(status_code=500, detail="llm-token-limit requires counter-key")
        estimate = _policy_bool(self.estimate_prompt_tokens, req, runtime, default=False)
        tokens_per_minute = max(0, _policy_int(self.tokens_per_minute, req, runtime, default=0))
        token_quota = max(0, _policy_int(self.token_quota, req, runtime, default=0))
        token_quota_period = render_policy_value(self.token_quota_period or "", req, runtime).strip().lower()
        if token_quota > 0 and token_quota_period not in LLM_QUOTA_PERIODS:
            raise HTTPException(status_code=500, detail="llm-token-limit token-quota-period is invalid")

        estimated_prompt_tokens = _estimate_llm_tokens_from_text(_llm_prompt_text(req.body)) if estimate else 0
        now = time.time()

        used_quota, refusal = self._quota_block(
            req,
            runtime,
            quota_store=quota_store,
            counter_key=counter_key,
            token_quota=token_quota,
            token_quota_period=token_quota_period,
            estimate=estimate,
            estimated_prompt_tokens=estimated_prompt_tokens,
            now=now,
        )
        if refusal is not None:
            return refusal

        used_rate, refusal = self._rate_block(
            req,
            runtime,
            rate_store=rate_store,
            counter_key=counter_key,
            tokens_per_minute=tokens_per_minute,
            estimate=estimate,
            estimated_prompt_tokens=estimated_prompt_tokens,
            now=now,
        )
        if refusal is not None:
            return refusal

        # The deferred accounting reads the response body, so it must be buffered.
        req.variables["_policy_response_buffering_required"] = True
        if self.tokens_consumed_variable_name:
            req.variables[self.tokens_consumed_variable_name] = estimated_prompt_tokens
        self._defer(
            runtime,
            counter_key=counter_key,
            tokens_per_minute=tokens_per_minute,
            token_quota=token_quota,
            token_quota_period=token_quota_period,
            estimated_prompt_tokens=estimated_prompt_tokens,
        )
        _record_step(
            runtime,
            "llm-token-limit",
            {
                "counter_key": counter_key,
                "estimate_prompt_tokens": estimate,
                "estimated_prompt_tokens": estimated_prompt_tokens,
                "tokens_used_minute": used_rate,
                "tokens_used_quota": used_quota,
            },
        )
        return None

    def _limit_response(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        status_code: int,
        retry_after: int,
        body: str,
        counter_key: str,
    ) -> ResponseSpec:
        header_name = self.retry_after_header_name or "Retry-After"
        if self.retry_after_variable_name:
            req.variables[self.retry_after_variable_name] = retry_after
            _record_variable_write(runtime, self.retry_after_variable_name, retry_after, "llm-token-limit")
        _record_step(
            runtime,
            "llm-token-limit",
            {"counter_key": counter_key, "blocked": True, "status_code": status_code, "retry_after": retry_after},
        )
        headers = {
            "content-type": "text/plain",
            header_name.lower(): str(retry_after),
        }
        return ResponseSpec(status_code=status_code, headers=headers, body=body.encode("utf-8"))


_LLM_DEFAULT_DIMENSION_SOURCES = {
    "api id": "api_id",
    "operation id": "operation_id",
    "subscription id": "subscription_id",
    "product id": "product_id",
    "product": "product_id",
    "client ip address": "client_ip",
}


def _default_llm_dimension_value(req: PolicyRequest, name: str) -> str:
    source = _LLM_DEFAULT_DIMENSION_SOURCES.get(name.strip().lower())
    if source is None:
        return ""
    return _stringify_policy_value(req.variables.get(source))


@dataclass(frozen=True)
class LlmEmitTokenMetricDeferred(DeferredPolicyAction):
    namespace: str
    dimensions: tuple[tuple[str, str], ...]
    estimated_prompt_tokens: int

    def finalize(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        usage = _llm_observed_usage(req, estimated_prompt_tokens=self.estimated_prompt_tokens)
        emitter = runtime.llm_metric_emitter if runtime is not None else None
        if emitter is not None:
            attributes = {
                "apim.llm.metric.namespace": self.namespace,
                **{f"apim.llm.dimension.{name}": value for name, value in self.dimensions},
            }
            for token_type in ("prompt", "completion", "total"):
                if usage[token_type]:
                    emitter(usage[token_type], {**attributes, "apim.llm.token.type": token_type})
        _record_step(
            runtime,
            "llm-emit-token-metric",
            {
                "namespace": self.namespace,
                "dimensions": dict(self.dimensions),
                "prompt_tokens": usage["prompt"],
                "completion_tokens": usage["completion"],
                "total_tokens": usage["total"],
            },
        )


@dataclass(frozen=True)
class LlmEmitTokenMetric(PolicyNode):
    namespace: str
    dimensions: tuple[tuple[str, str | None], ...]

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        resolved: list[tuple[str, str]] = []
        for name, value in self.dimensions:
            if value is None:
                resolved.append((name, _default_llm_dimension_value(req, name)))
            else:
                resolved.append((name, render_policy_value(value, req, runtime)))
        estimated_prompt_tokens = _estimate_llm_tokens_from_text(_llm_prompt_text(req.body))
        req.variables["_policy_response_buffering_required"] = True
        if runtime is not None:
            runtime.deferred_actions.append(
                LlmEmitTokenMetricDeferred(
                    namespace=self.namespace,
                    dimensions=tuple(resolved),
                    estimated_prompt_tokens=estimated_prompt_tokens,
                )
            )
        _record_step(
            runtime,
            "llm-emit-token-metric",
            {"namespace": self.namespace, "dimensions": dict(resolved)},
        )
        return None


@dataclass(frozen=True)
class EmitMetric(PolicyNode):
    name: str
    namespace: str
    value: str | None
    dimensions: tuple[tuple[str, str | None], ...]

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        amount = _policy_int(self.value, req, runtime, default=1) if self.value else 1
        resolved: dict[str, str] = {}
        for dim_name, dim_value in self.dimensions:
            if dim_value is None:
                resolved[dim_name] = _default_llm_dimension_value(req, dim_name)
            else:
                resolved[dim_name] = render_policy_value(dim_value, req, runtime)
        emitter = runtime.custom_metric_emitter if runtime is not None else None
        if emitter is not None and amount:
            attributes = {
                "apim.metric.name": self.name,
                "apim.metric.namespace": self.namespace,
                **{f"apim.metric.dimension.{name}": value for name, value in resolved.items()},
            }
            emitter(amount, attributes)
        _record_step(
            runtime,
            "emit-metric",
            {"name": self.name, "namespace": self.namespace, "value": amount, "dimensions": resolved},
        )
        return None


VALIDATION_ACTIONS = ("ignore", "prevent", "detect")


def _validation_action(value: str | None, *, default: str) -> str:
    action = (value or default).strip().lower()
    if action not in VALIDATION_ACTIONS:
        raise HTTPException(status_code=500, detail=f"Unsupported validation action: {action}")
    return action


def _record_validation_error(
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    *,
    policy: str,
    errors_variable_name: str | None,
    message: str,
) -> None:
    if errors_variable_name:
        existing = req.variables.get(errors_variable_name)
        errors = existing if isinstance(existing, list) else []
        errors.append({"source": policy, "message": message})
        req.variables[errors_variable_name] = errors
    _record_step(runtime, policy, {"error": message})


def _operation_request_metadata(req: PolicyRequest, runtime: PolicyRuntime | None) -> Any:
    if runtime is None or runtime.gateway_config is None:
        return None
    api = runtime.gateway_config.apis.get(str(req.variables.get("api_id") or ""))
    if api is None:
        return None
    operation = api.operations.get(str(req.variables.get("operation_id") or ""))
    if operation is None:
        return None
    return operation


# Public response for a validation failure on a response
# https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy
_INTERNAL_ERROR_PUBLIC_MESSAGE = "The request could not be processed due to an internal error. Contact the API owner."


@dataclass(frozen=True)
class ValidateContentType:
    content_type: str
    validate_as: str
    action: str


@dataclass(frozen=True)
class ValidateContent(PolicyNode):
    unspecified_content_type_action: str = "ignore"
    max_size: int | None = None
    size_exceeded_action: str = "prevent"
    errors_variable_name: str | None = None
    content_types: tuple[ValidateContentType, ...] = ()

    @staticmethod
    def _message(req: PolicyRequest) -> tuple[bytes, str, str]:
        """The body, content type and noun this policy validates in this section.

        https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy
        Inbound validates the request; outbound validates the response.
        """
        if req.in_outbound:
            headers = req.response_headers if req.response_headers is not None else req.headers
            content_type = headers.get("content-type") or req.response_media_type or ""
            return req.response_body, content_type, "Response"
        return req.body, req.headers.get("content-type") or "", "Request"

    def _size_failure(
        self, req: PolicyRequest, runtime: PolicyRuntime | None, *, body: bytes, noun: str
    ) -> ResponseSpec | None:
        if self.max_size is None or len(body) <= self.max_size:
            return None
        return self._fail(
            req,
            runtime,
            action=self.size_exceeded_action,
            message=f"{noun} body is larger than max-size ({self.max_size} bytes)",
        )

    def _json_failure(
        self, req: PolicyRequest, runtime: PolicyRuntime | None, *, matched: Any, body: bytes, content_type: str
    ) -> ResponseSpec | None:
        if matched.validate_as != "json":
            return None
        try:
            json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._fail(
                req,
                runtime,
                action=matched.action,
                message=f"Body is not valid JSON for content type {content_type}",
            )
        return None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        """Validate the request (inbound) or response (outbound) body.

        An empty body is never validated. A content type the policy does not
        declare is handled by unspecified-content-type-action, which may well be
        to ignore it.
        """
        body, raw_content_type, noun = self._message(req)
        if not body:
            return None

        outcome = self._size_failure(req, runtime, body=body, noun=noun)
        if outcome is not None:
            return outcome

        content_type = raw_content_type.split(";", 1)[0].strip().lower()
        matched = next(
            (item for item in self.content_types if item.content_type.lower() == content_type),
            None,
        )
        if matched is None:
            if content_type and self.unspecified_content_type_action != "ignore":
                return self._fail(
                    req,
                    runtime,
                    action=self.unspecified_content_type_action,
                    message=f"Content type {content_type} is not specified for validation",
                )
            return None
        if matched.action == "ignore":
            return None

        outcome = self._json_failure(req, runtime, matched=matched, body=body, content_type=content_type)
        if outcome is not None:
            return outcome

        _record_step(runtime, "validate-content", {"content_type": content_type, "valid": True})
        return None

    def _fail(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        action: str,
        message: str,
    ) -> ResponseSpec | None:
        if action == "ignore":
            return None
        _record_validation_error(
            req, runtime, policy="validate-content", errors_variable_name=self.errors_variable_name, message=message
        )
        if action != "prevent":
            return None
        if req.in_outbound:
            # 502 in outbound, with the detail withheld from the client.
            return ResponseSpec(
                status_code=502,
                headers={"content-type": "text/plain"},
                body=_INTERNAL_ERROR_PUBLIC_MESSAGE.encode("utf-8"),
            )
        return ResponseSpec(
            status_code=400,
            headers={"content-type": "text/plain"},
            body=message.encode("utf-8"),
        )


# Headers every HTTP client sends. validate-parameters must not reject these as
# "unspecified", or it fails every request rather than catching a mistake.
_ALWAYS_ALLOWED_HEADERS = frozenset({"host", "content-type", "content-length", "accept", "connection", "user-agent"})


@dataclass(frozen=True)
class ValidateParameters(PolicyNode):
    specified_parameter_action: str = "prevent"
    unspecified_parameter_action: str = "ignore"
    errors_variable_name: str | None = None
    headers_specified_action: str | None = None
    headers_unspecified_action: str | None = None
    query_specified_action: str | None = None
    query_unspecified_action: str | None = None

    def _missing_required_failure(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        kind: str,
        declared: list[Any],
        present: set[str],
        normalise: Any,
        action: str,
    ) -> ResponseSpec | None:
        """Refuse when a parameter the operation declares required is absent."""
        if action == "ignore":
            return None
        for param in declared:
            if param.required and normalise(param.name) not in present:
                outcome = self._fail(
                    req,
                    runtime,
                    action=action,
                    message=f"Required {kind} parameter {param.name} is missing",
                )
                if outcome is not None:
                    return outcome
        return None

    def _unspecified_failure(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        kind: str,
        declared_names: set[str],
        present: set[str],
        action: str,
    ) -> ResponseSpec | None:
        """Refuse parameters the operation never declared.

        Headers every HTTP client sends are exempt: rejecting `host` or
        `user-agent` would fail every request rather than catch a mistake.
        """
        if action == "ignore":
            return None
        for name in sorted(present):
            if name in declared_names or (kind == "header" and name in _ALWAYS_ALLOWED_HEADERS):
                continue
            outcome = self._fail(
                req,
                runtime,
                action=action,
                message=f"Unspecified {kind} parameter {name} is not allowed",
            )
            if outcome is not None:
                return outcome
        return None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        """Check headers and query parameters against the operation's contract."""
        operation = _operation_request_metadata(req, runtime)
        request_meta = getattr(operation, "request", None)

        checks = (
            (
                "header",
                list(getattr(request_meta, "headers", []) or []),
                {name.lower() for name in req.headers},
                lambda name: name.lower(),
                self.headers_specified_action or self.specified_parameter_action,
                self.headers_unspecified_action or self.unspecified_parameter_action,
            ),
            (
                "query",
                list(getattr(request_meta, "query_parameters", []) or []),
                set(req.query),
                lambda name: name,
                self.query_specified_action or self.specified_parameter_action,
                self.query_unspecified_action or self.unspecified_parameter_action,
            ),
        )

        for kind, declared, present, normalise, specified_action, unspecified_action in checks:
            outcome = self._missing_required_failure(
                req,
                runtime,
                kind=kind,
                declared=declared,
                present=present,
                normalise=normalise,
                action=specified_action,
            )
            if outcome is not None:
                return outcome

            outcome = self._unspecified_failure(
                req,
                runtime,
                kind=kind,
                declared_names={normalise(param.name) for param in declared},
                present=present,
                action=unspecified_action,
            )
            if outcome is not None:
                return outcome
        return None

    def _fail(
        self,
        req: PolicyRequest,
        runtime: PolicyRuntime | None,
        *,
        action: str,
        message: str,
    ) -> ResponseSpec | None:
        _record_validation_error(
            req, runtime, policy="validate-parameters", errors_variable_name=self.errors_variable_name, message=message
        )
        if action == "prevent":
            return ResponseSpec(
                status_code=400,
                headers={"content-type": "text/plain"},
                body=message.encode("utf-8"),
            )
        return None


@dataclass(frozen=True)
class ValidateStatusCode(PolicyNode):
    unspecified_status_code_action: str = "prevent"
    errors_variable_name: str | None = None
    status_codes: tuple[tuple[int, str], ...] = ()

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        status = req.response_status_code
        if status is None:
            return None
        # A status declared for the operation is valid; the per-code override
        # "doesn't take effect" for it.
        # https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy
        operation = _operation_request_metadata(req, runtime)
        declared = {resp.status_code for resp in getattr(operation, "responses", []) or []}
        if status in declared:
            _record_step(runtime, "validate-status-code", {"status_code": status, "declared": True})
            return None
        action = dict(self.status_codes).get(status, self.unspecified_status_code_action)
        if action == "ignore":
            return None
        _record_validation_error(
            req,
            runtime,
            policy="validate-status-code",
            errors_variable_name=self.errors_variable_name,
            message=f"Response status code {status} is not specified for this operation",
        )
        if action != "prevent":
            return None
        # prevent in outbound answers 502 and never leaks the backend response.
        return ResponseSpec(
            status_code=502,
            headers={"content-type": "text/plain"},
            body=_INTERNAL_ERROR_PUBLIC_MESSAGE.encode("utf-8"),
            media_type="text/plain",
        )


@dataclass(frozen=True)
class CacheLookup(PolicyNode):
    vary_by_headers: list[str] = field(default_factory=list)
    vary_by_query_parameters: list[str] = field(default_factory=list)
    vary_by_developer: str = "false"
    vary_by_developer_groups: str = "false"
    downstream_caching_type: str = "none"
    must_revalidate: str = "true"
    allow_private_response_caching: str = "false"
    caching_type: str = "prefer-external"

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None:
            return None
        _, adapted = _normalize_cache_caching_type(self.caching_type)
        req.variables["_policy_response_cache_active"] = True
        # Learn specifies GET-only lookup but is silent on GET request bodies;
        # the local adaptation checks the method and does not add body filtering.
        if req.method.upper() != "GET":
            _record_step(runtime, "cache-lookup", {"status": "skipped", "reason": "method_not_get"})
            return None
        allow_private = _policy_bool(self.allow_private_response_caching, req, runtime, default=False)
        request_headers = _request_headers(req)
        if request_headers.get("authorization") and not allow_private:
            _record_step(runtime, "cache-lookup", {"status": "skipped", "reason": "private_response_caching_disabled"})
            return None
        vary_by_developer = _policy_bool(self.vary_by_developer, req, runtime, default=False)
        vary_by_developer_groups = _policy_bool(self.vary_by_developer_groups, req, runtime, default=False)
        downstream_caching_type = _normalize_downstream_caching_type(
            render_policy_value(self.downstream_caching_type or "none", req, runtime)
        )
        must_revalidate = _policy_bool(self.must_revalidate, req, runtime, default=True)
        cache_key = _build_response_cache_key(
            req,
            vary_by_headers=[item.lower() for item in self.vary_by_headers],
            vary_by_query_parameters=self.vary_by_query_parameters,
            vary_by_developer=vary_by_developer,
            vary_by_developer_groups=vary_by_developer_groups,
        )
        req.variables["_policy_response_cache_context"] = ResponseCachePolicyContext(
            cache_key=cache_key,
            downstream_caching_type=downstream_caching_type,
            must_revalidate=must_revalidate,
            allow_private_response_caching=allow_private,
        )
        entry = runtime.response_cache.get(cache_key)
        if isinstance(entry, ResponseCacheEntry):
            if entry.expires_at <= _policy_now(runtime):
                runtime.response_cache.pop(cache_key, None)
            else:
                headers = dict(entry.headers)
                _apply_downstream_cache_headers(
                    headers,
                    downstream_caching_type=downstream_caching_type,
                    must_revalidate=must_revalidate,
                )
                _record_step(runtime, "cache-lookup", {"status": "hit", "adapted": adapted, "cache_key": cache_key})
                return ResponseSpec(
                    status_code=entry.status_code,
                    headers=headers,
                    body=entry.body,
                    media_type=entry.media_type,
                )
        _record_step(runtime, "cache-lookup", {"status": "miss", "adapted": adapted, "cache_key": cache_key})
        return None


@dataclass(frozen=True)
class CacheStore(PolicyNode):
    duration: str
    cache_response: str | None = None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None:
            return None
        context = req.variables.get("_policy_response_cache_context")
        if not isinstance(context, ResponseCachePolicyContext):
            return None
        if req.method.upper() != "GET":
            _record_step(runtime, "cache-store", {"status": "skipped", "reason": "method_not_get"})
            return None
        should_store = (
            _policy_bool(self.cache_response, req, runtime, default=False)
            if self.cache_response is not None
            else req.response_status_code == 200
        )
        if not should_store:
            _record_step(runtime, "cache-store", {"status": "skipped", "reason": "response_not_cacheable"})
            return None
        ttl = max(0, _policy_int(self.duration, req, runtime, default=0))
        if ttl <= 0:
            _record_step(runtime, "cache-store", {"status": "skipped", "reason": "non_positive_ttl"})
            return None
        headers = dict(_response_header_target(req))
        _apply_downstream_cache_headers(
            headers,
            downstream_caching_type=context.downstream_caching_type,
            must_revalidate=context.must_revalidate,
        )
        runtime.response_cache[context.cache_key] = ResponseCacheEntry(
            expires_at=_policy_now(runtime) + ttl,
            status_code=req.response_status_code or 200,
            headers=headers,
            body=req.response_body,
            media_type=req.response_media_type,
        )
        _record_step(runtime, "cache-store", {"status": "stored", "cache_key": context.cache_key, "ttl_seconds": ttl})
        return None


@dataclass(frozen=True)
class CacheLookupValue(PolicyNode):
    key: str
    variable_name: str
    default_value: str | None = None
    caching_type: str = "prefer-external"

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None:
            return None
        _, adapted = _normalize_cache_caching_type(self.caching_type)
        key = render_policy_value(self.key, req, runtime)
        now = _policy_now(runtime)
        entry = _cleanup_value_cache(runtime.value_cache, key, now)
        if entry is not None:
            req.variables[self.variable_name] = entry.value
            _record_variable_write(runtime, self.variable_name, entry.value, "cache-lookup-value")
            _record_step(runtime, "cache-lookup-value", {"status": "hit", "cache_key": key, "adapted": adapted})
            return None
        if self.default_value is not None:
            value = evaluate_policy_value(self.default_value, req, runtime)
            req.variables[self.variable_name] = value
            _record_variable_write(runtime, self.variable_name, value, "cache-lookup-value-default")
        _record_step(runtime, "cache-lookup-value", {"status": "miss", "cache_key": key, "adapted": adapted})
        return None


@dataclass(frozen=True)
class CacheStoreValue(PolicyNode):
    key: str
    value: str
    duration: str
    caching_type: str = "prefer-external"

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None:
            return None
        _, adapted = _normalize_cache_caching_type(self.caching_type)
        key = render_policy_value(self.key, req, runtime)
        value = evaluate_policy_value(self.value, req, runtime)
        ttl = max(0, _policy_int(self.duration, req, runtime, default=0))
        # APIM stores this value asynchronously; the local in-memory adaptation
        # writes it synchronously and deliberately does not emulate latency.
        runtime.value_cache[key] = ValueCacheEntry(expires_at=_policy_now(runtime) + ttl, value=value)
        _record_step(
            runtime, "cache-store-value", {"status": "stored", "cache_key": key, "ttl_seconds": ttl, "adapted": adapted}
        )
        return None


@dataclass(frozen=True)
class CacheRemoveValue(PolicyNode):
    key: str
    caching_type: str = "prefer-external"
    fail_on_cache_removal_error: str = "false"

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if runtime is None:
            return None
        _, adapted = _normalize_cache_caching_type(self.caching_type)
        key = render_policy_value(self.key, req, runtime)
        fail_on_error = _policy_bool(self.fail_on_cache_removal_error, req, runtime, default=False)
        try:
            removed = runtime.value_cache.pop(key, None) is not None
        except Exception:
            # The local dictionary has no removal failure path in normal use.
            if fail_on_error:
                raise
            _record_step(
                runtime,
                "cache-remove-value",
                {"status": "ignored-removal-error", "cache_key": key, "adapted": adapted},
            )
            return None
        _record_step(
            runtime,
            "cache-remove-value",
            {"status": "removed" if removed else "miss", "cache_key": key, "adapted": adapted},
        )
        return None


@dataclass(frozen=True)
class RequiredClaim:
    name: str
    values: list[str]
    match: str = "all"
    separator: str | None = None


@dataclass(frozen=True)
class ValidateJwt(PolicyNode):
    header_name: str | None
    query_parameter_name: str | None
    token_value: str | None
    failed_validation_httpcode: int = 401
    failed_validation_error_message: str = "JWT validation failed"
    require_scheme: str | None = None
    require_expiration_time: bool = True
    output_token_variable_name: str | None = None
    openid_config_urls: list[str] = field(default_factory=list)
    issuers: list[str] = field(default_factory=list)
    audiences: list[str] = field(default_factory=list)
    required_claims: list[RequiredClaim] = field(default_factory=list)

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        raise RuntimeError("validate-jwt must be executed through apply_async")

    def _extract_token(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> str | None:
        """Find the bearer token, wherever the policy says it lives.

        A named header wins, then a named query parameter, then an explicit
        token expression. When a scheme is required on Authorization, a value
        without that exact prefix counts as no token at all.
        """
        header_name = render_policy_value(self.header_name or "", req, runtime) if self.header_name else None
        if header_name:
            header_value = req.headers.get(header_name.lower())
            if header_value is None:
                return None
            if self.require_scheme and header_name.lower() == "authorization":
                expected_prefix = f"{self.require_scheme} "
                if not header_value.startswith(expected_prefix):
                    return None
                return header_value[len(expected_prefix) :].strip()
            return header_value.strip()

        query_name = (
            render_policy_value(self.query_parameter_name or "", req, runtime) if self.query_parameter_name else None
        )
        if query_name:
            return req.query.get(query_name)

        return render_policy_value(self.token_value or "", req, runtime) if self.token_value else None

    def _publish_claims(
        self, req: PolicyRequest, runtime: PolicyRuntime | None, *, claims: dict[str, Any], token: str
    ) -> None:
        """Expose the validated claims to later policy and to route authorization."""
        req.variables["_last_jwt_claims"] = claims
        _record_variable_write(runtime, "_last_jwt_claims", claims, "validate-jwt")
        if self.output_token_variable_name:
            jwt_value = JwtValue(claims, token)
            req.variables[self.output_token_variable_name] = jwt_value
            _record_variable_write(runtime, self.output_token_variable_name, jwt_value, "validate-jwt")
        _record_jwt_validation(
            runtime,
            {
                "status": "valid",
                "issuer": claims.get("iss"),
                "audience": claims.get("aud"),
                "output_variable": self.output_token_variable_name,
            },
        )

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        """Validate a JWT and publish its claims, or refuse the call."""
        token = self._extract_token(req, runtime)
        if not token:
            return self._failure(req, runtime, "JWT not present.")

        if runtime is None or runtime.http_client is None:
            raise HTTPException(status_code=500, detail="validate-jwt requires an HTTP client")

        try:
            claims = await self._decode_token(token, req, runtime)
            self._validate_claims(claims, req, runtime)
        except HTTPException as exc:
            _record_jwt_validation(runtime, {"status": "invalid", "detail": exc.detail})
            req.variables["_policy_error_detail"] = str(exc.detail)
            return ResponseSpec(
                status_code=self.failed_validation_httpcode,
                headers={"content-type": "text/plain"},
                body=str(self.failed_validation_error_message or exc.detail).encode("utf-8"),
            )

        self._publish_claims(req, runtime, claims=claims, token=token)
        return None

    def _failure(self, req: PolicyRequest, runtime: PolicyRuntime | None, detail: str) -> ResponseSpec:
        _record_jwt_validation(runtime, {"status": "invalid", "detail": detail})
        req.variables["_policy_error_detail"] = detail
        return ResponseSpec(
            status_code=self.failed_validation_httpcode,
            headers={"content-type": "text/plain"},
            body=str(self.failed_validation_error_message or detail).encode("utf-8"),
        )

    async def _decode_token(
        self,
        token: str,
        req: PolicyRequest,
        runtime: PolicyRuntime,
    ) -> dict[str, Any]:
        urls = [render_policy_value(url, req, runtime) for url in self.openid_config_urls]
        if not urls:
            raise HTTPException(status_code=500, detail="validate-jwt requires at least one openid-config url")

        unverified = jwt.get_unverified_header(token)
        kid = unverified.get("kid")
        last_error: Exception | None = None

        for url in urls:
            metadata, jwks = await _load_openid_configuration(url, runtime)
            keys = jwks.get("keys") or []
            candidates = [item for item in keys if isinstance(item, dict)]
            if kid:
                candidates = [item for item in candidates if item.get("kid") == kid] or candidates
            for jwk in candidates:
                try:
                    key = RSAAlgorithm.from_jwk(json.dumps(jwk))
                    claims = jwt.decode(
                        token,
                        key,
                        algorithms=["RS256", "RS384", "RS512", "PS256", "ES256"],
                        options={
                            "verify_aud": False,
                            "verify_iss": False,
                            "require": ["exp"] if self.require_expiration_time else [],
                        },
                    )
                    if not self.issuers and metadata.get("issuer"):
                        claims.setdefault("_metadata_issuer", metadata.get("issuer"))
                    return claims
                except Exception as exc:  # pragma: no cover - exercised indirectly via failure path
                    last_error = exc
                    continue

        raise HTTPException(status_code=401, detail="Invalid or expired access token") from last_error

    def _check_issuer(self, claims: dict[str, Any], req: PolicyRequest, runtime: PolicyRuntime | None) -> None:
        """The issuer must be one the policy names, or the one discovered from metadata."""
        expected = [render_policy_value(item, req, runtime) for item in self.issuers]
        if not expected and claims.get("_metadata_issuer"):
            expected = [str(claims.get("_metadata_issuer"))]
        if expected and str(claims.get("iss") or "") not in expected:
            raise HTTPException(status_code=401, detail="Issuer validation failed")

    def _check_audience(self, claims: dict[str, Any], req: PolicyRequest, runtime: PolicyRuntime | None) -> None:
        """`aud` may be a single value or a list; one overlap is enough."""
        expected = [render_policy_value(item, req, runtime) for item in self.audiences]
        if not expected:
            return
        actual_aud = claims.get("aud")
        if isinstance(actual_aud, list):
            audiences = [str(item) for item in actual_aud]
        else:
            audiences = [str(actual_aud)] if actual_aud else []
        if not set(expected).intersection(audiences):
            raise HTTPException(status_code=401, detail="Audience validation failed")

    @staticmethod
    def _claim_values(actual: Any, separator: str | None) -> list[str]:
        """A claim's values, whether it is a list, a delimited string, or a scalar."""
        if isinstance(actual, list):
            return [str(item) for item in actual]
        if separator and isinstance(actual, str):
            return [item.strip() for item in actual.split(separator) if item.strip()]
        return [str(actual)]

    def _check_required_claims(self, claims: dict[str, Any], req: PolicyRequest, runtime: PolicyRuntime | None) -> None:
        """Each required claim must be present and match, by `any` or by `all`."""
        for claim in self.required_claims:
            actual = claims.get(claim.name)
            if actual is None:
                raise HTTPException(status_code=401, detail=f"Missing required claim: {claim.name}")

            actual_values = set(self._claim_values(actual, claim.separator))
            expected = {render_policy_value(item, req, runtime) for item in claim.values}
            satisfied = (
                bool(expected.intersection(actual_values)) if claim.match == "any" else expected.issubset(actual_values)
            )
            if not satisfied:
                raise HTTPException(status_code=401, detail=f"Claim validation failed: {claim.name}")

    def _validate_claims(self, claims: dict[str, Any], req: PolicyRequest, runtime: PolicyRuntime | None) -> None:
        self._check_issuer(claims, req, runtime)
        self._check_audience(claims, req, runtime)
        self._check_required_claims(claims, req, runtime)


@dataclass(frozen=True)
class SetBackendService(PolicyNode):
    base_url: str | None = None
    backend_id: str | None = None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        resolved_backend_id = render_policy_value(self.backend_id or "", req, runtime) if self.backend_id else None
        resolved_base_url = render_policy_value(self.base_url or "", req, runtime) if self.base_url else None

        if resolved_backend_id:
            req.variables["selected_backend_id"] = resolved_backend_id
            if runtime and runtime.gateway_config:
                backend = runtime.gateway_config.backends.get(resolved_backend_id)
                if backend is None:
                    raise HTTPException(status_code=500, detail=f"Unknown backend: {resolved_backend_id}")
                req.variables["selected_backend_url"] = backend.url
            if runtime and runtime.trace is not None:
                runtime.trace.selected_backend = {"backend_id": resolved_backend_id}
            _record_step(runtime, "set-backend-service", {"backend_id": resolved_backend_id})
            return None

        if not resolved_base_url:
            raise HTTPException(status_code=500, detail="set-backend-service requires backend-id or base-url")
        req.variables["selected_backend_url"] = resolved_base_url
        if runtime and runtime.trace is not None:
            runtime.trace.selected_backend = {"base_url": resolved_base_url}
        _record_step(runtime, "set-backend-service", {"base_url": resolved_base_url})
        return None


@dataclass(frozen=True)
class ForwardRequest(PolicyNode):
    """Configure the backend forward operation for this policy request."""

    timeout: str | None = None
    timeout_ms: str | None = None
    follow_redirects: str | None = None
    buffer_request_body: str | None = None
    buffer_response: str | None = None
    fail_on_error_status_code: str | None = None
    http_version: str | None = None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        if self.timeout_ms is not None:
            timeout_seconds = _policy_float(self.timeout_ms, req, runtime) / 1000
        else:
            timeout_seconds = _policy_float(self.timeout, req, runtime, default=300.0)
        if timeout_seconds < 0:
            raise HTTPException(status_code=500, detail="forward-request timeout must be non-negative")

        http_version = render_policy_value(self.http_version or "1", req, runtime).strip().lower()
        if http_version not in {"1", "2", "2or1"}:
            raise HTTPException(status_code=500, detail=f"Unsupported forward-request http-version: {http_version}")

        req.variables["_forward_request_timeout_seconds"] = timeout_seconds
        req.variables["_forward_request_follow_redirects"] = _policy_bool(
            self.follow_redirects, req, runtime, default=False
        )
        req.variables["_forward_request_buffer_request_body"] = _policy_bool(
            self.buffer_request_body, req, runtime, default=False
        )
        req.variables["_forward_request_buffer_response"] = _policy_bool(
            self.buffer_response, req, runtime, default=True
        )
        req.variables["_forward_request_fail_on_error_status_code"] = _policy_bool(
            self.fail_on_error_status_code, req, runtime, default=False
        )
        req.variables["_forward_request_http_version"] = http_version
        req.variables["_forward_request_present"] = True
        _record_step(
            runtime,
            "forward-request",
            {
                "timeout_seconds": timeout_seconds,
                "follow_redirects": req.variables["_forward_request_follow_redirects"],
                "buffer_request_body": req.variables["_forward_request_buffer_request_body"],
                "buffer_response": req.variables["_forward_request_buffer_response"],
                "fail_on_error_status_code": req.variables["_forward_request_fail_on_error_status_code"],
                "http_version": http_version,
            },
        )
        return None


@dataclass(frozen=True)
class SendRequest(PolicyNode):
    mode: str
    response_variable_name: str
    timeout: str | None = None
    ignore_error: bool = False
    url: str | None = None
    method: str | None = None
    headers: list[SetHeader] = field(default_factory=list)
    body: str | None = None
    authentication_certificate_thumbprint: str | None = None
    authentication_managed_identity_resource: str | None = None

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        raise RuntimeError("send-request must be executed through apply_async")

    def _build_callout_request(
        self, req: PolicyRequest, runtime: PolicyRuntime | None
    ) -> tuple[str, str, PolicyRequest]:
        """Assemble the outbound call: its URL, method, headers and body.

        `mode="copy"` starts from the inbound request; anything else starts from
        an empty GET. Either way the policy's own set-header and set-body nodes
        are applied on top.
        """
        mode = (render_policy_value(self.mode, req, runtime) or "new").lower()
        copying = mode == "copy"

        url = str(req.variables.get("original_request_url") or "")
        if self.url is not None:
            url = render_policy_value(self.url, req, runtime)
        if not url:
            raise HTTPException(status_code=500, detail="send-request requires set-url")

        method = req.method if copying else "GET"
        if self.method is not None:
            method = render_policy_value(self.method, req, runtime).upper()

        temp_req = PolicyRequest(
            method=req.method,
            path=req.path,
            query=dict(req.query),
            headers=dict(req.headers) if copying else {},
            variables=req.variables,
            body=req.body if copying else b"",
        )
        for header in self.headers:
            header.apply(temp_req, runtime)
        if self.body is not None:
            temp_req.body = render_policy_value(self.body, req, runtime).encode("utf-8")

        self._apply_callout_authentication(temp_req, req, runtime)
        return url, method, temp_req

    def _apply_callout_authentication(
        self, temp_req: PolicyRequest, req: PolicyRequest, runtime: PolicyRuntime | None
    ) -> None:
        """Signal managed identity or client certificate to the callout target.

        The simulator has no real credential to present, so it says which one
        would have been used rather than presenting one.
        """
        if self.authentication_managed_identity_resource is not None:
            temp_req.headers["x-apim-managed-identity"] = "true"
            temp_req.headers["x-apim-managed-identity-resource"] = render_policy_value(
                self.authentication_managed_identity_resource, req, runtime
            )
        if self.authentication_certificate_thumbprint is not None:
            temp_req.headers["x-apim-authentication-certificate-thumbprint"] = render_policy_value(
                self.authentication_certificate_thumbprint, req, runtime
            )

    def _record_ignored_error(
        self, req: PolicyRequest, runtime: PolicyRuntime | None, *, url: str, method: str, exc: Exception
    ) -> None:
        """Record a failed callout the policy asked to tolerate."""
        req.variables[self.response_variable_name] = None
        _record_variable_write(runtime, self.response_variable_name, None, "send-request")
        _record_send_request(
            runtime,
            {
                "url": url,
                "method": method,
                "status": "ignored-error",
                "error": str(exc),
                "response_variable_name": self.response_variable_name,
            },
        )

    async def apply_async(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        """Make a side call and store its response in a policy variable."""
        if runtime is None or runtime.http_client is None:
            raise HTTPException(status_code=500, detail="send-request requires an HTTP client")

        url, method, temp_req = self._build_callout_request(req, runtime)
        timeout = float(render_policy_value(self.timeout or "60", req, runtime)) if self.timeout else 60.0

        try:
            response = await runtime.http_client.request(
                method, url, headers=temp_req.headers, content=temp_req.body, timeout=timeout
            )
        except httpx.RequestError as exc:
            if not self.ignore_error:
                raise HTTPException(status_code=500, detail=f"send-request failed: {exc}") from exc
            self._record_ignored_error(req, runtime, url=url, method=method, exc=exc)
            return None

        callout = CalloutResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            reason=response.reason_phrase,
        )
        req.variables[self.response_variable_name] = callout
        _record_variable_write(runtime, self.response_variable_name, callout, "send-request")
        _record_send_request(
            runtime,
            {
                "url": url,
                "method": method,
                "status_code": response.status_code,
                "response_variable_name": self.response_variable_name,
            },
        )
        return None


@dataclass(frozen=True)
class PolicyDocument:
    inbound: list[PolicyNode]
    backend: list[PolicyNode]
    outbound: list[PolicyNode]
    on_error: list[PolicyNode]
    sections_present: frozenset[str] = frozenset()
    # Where the document was authored (global, product:<id>, api:<id>,
    # operation:<api>/<op>). Subscription throttles count per scope.
    scope: str = ""


POLICY_VALUE_PATTERN = re.compile(r"\{([^{}]+)\}")


def _stringify_policy_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _trace_safe_value(runtime: PolicyRuntime | None, value: Any) -> Any:
    if isinstance(value, JwtValue):
        value = {
            "type": "jwt",
            "subject": value.Subject,
            "issuer": value.Issuer,
            "audiences": value.Audiences,
            "claims": sorted(value.Claims.keys()),
        }
    elif isinstance(value, CalloutResponse):
        value = {
            "type": "response",
            "status_code": value.StatusCode,
            "reason": value.ReasonPhrase,
            "headers": dict(value.Headers),
            "body_text": value.Body.AsString()[:512],
        }
    if runtime and runtime.gateway_config:
        return mask_secret_data(value, runtime.gateway_config)
    return value


def _record_step(runtime: PolicyRuntime | None, step: str, detail: dict[str, Any]) -> None:
    if runtime is None or runtime.trace is None:
        return
    runtime.trace.steps.append({"step": step, **_trace_safe_value(runtime, detail)})


def _record_variable_write(runtime: PolicyRuntime | None, name: str, value: Any, source: str) -> None:
    if runtime is None or runtime.trace is None:
        return
    runtime.trace.variable_writes.append({"name": name, "source": source, "value": _trace_safe_value(runtime, value)})


def _record_send_request(runtime: PolicyRuntime | None, payload: dict[str, Any]) -> None:
    if runtime is None or runtime.trace is None:
        return
    runtime.trace.send_requests.append(_trace_safe_value(runtime, payload))


def _record_jwt_validation(runtime: PolicyRuntime | None, payload: dict[str, Any]) -> None:
    if runtime is None or runtime.trace is None:
        return
    runtime.trace.jwt_validations.append(_trace_safe_value(runtime, payload))


def _resolve_policy_token(req: PolicyRequest, token: str) -> str | None:
    normalized = token.strip()
    lowered = normalized.lower()

    if lowered == "method":
        return req.method
    if lowered == "path":
        return req.path
    if lowered == "subscription_id":
        return _stringify_policy_value(req.variables.get("subscription_id"))
    if lowered.startswith("header:"):
        name = lowered.split(":", 1)[1].strip()
        return _stringify_policy_value(req.headers.get(name))
    if lowered.startswith("query:"):
        name = normalized.split(":", 1)[1].strip()
        return _stringify_policy_value(req.query.get(name))
    if lowered.startswith("var:") or lowered.startswith("variable:"):
        name = normalized.split(":", 1)[1].strip()
        return _stringify_policy_value(req.variables.get(name))
    return None


def evaluate_policy_value(template: str, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> Any:
    source = template or ""
    if runtime and runtime.gateway_config:
        source = resolve_named_values_in_text(source, runtime.gateway_config)
    if is_apim_expression(source):
        return evaluate_apim_expression(source, build_expression_context(req))

    def _replace(match: re.Match[str]) -> str:
        resolved = _resolve_policy_token(req, match.group(1))
        if resolved is None:
            return match.group(0)
        return resolved

    return POLICY_VALUE_PATTERN.sub(_replace, source)


def render_policy_value(template: str, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> str:
    return _stringify_policy_value(evaluate_policy_value(template, req, runtime))


def _text_or_empty(el: ElementTree.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _policy_value_or_empty(el: ElementTree.Element) -> str:
    attr_value = el.attrib.get("value")
    if attr_value is not None:
        return attr_value.strip()
    value_el = el.find("value")
    if value_el is not None:
        return _text_or_empty(value_el)
    return _text_or_empty(el)


def _parse_set_header(el: ElementTree.Element) -> SetHeader:
    name = el.attrib.get("name")
    if not name:
        raise HTTPException(status_code=500, detail="set-header missing name")
    exists_action = el.attrib.get("exists-action", "override")
    value = _policy_value_or_empty(el)
    return SetHeader(name=name.lower(), value=value, exists_action=exists_action)


def _parse_set_variable(el: ElementTree.Element) -> SetVariable:
    name = (el.attrib.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=500, detail="set-variable missing name")
    return SetVariable(name=name, value=_policy_value_or_empty(el))


def _parse_set_query_parameter(el: ElementTree.Element) -> SetQueryParameter:
    name = (el.attrib.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=500, detail="set-query-parameter missing name")
    exists_action = el.attrib.get("exists-action", "override")
    return SetQueryParameter(name=name, value=_policy_value_or_empty(el), exists_action=exists_action)


def _parse_set_body(el: ElementTree.Element) -> SetBody:
    return SetBody(value=_policy_value_or_empty(el))


def _parse_rewrite_uri(el: ElementTree.Element) -> RewriteUri:
    template = el.attrib.get("template")
    if not template:
        raise HTTPException(status_code=500, detail="rewrite-uri missing template")
    return RewriteUri(template=template)


def _parse_return_response(el: ElementTree.Element) -> ReturnResponse:
    status_el = el.find("set-status")
    if status_el is None:
        raise HTTPException(status_code=500, detail="return-response missing set-status")
    code = int(status_el.attrib.get("code") or "200")
    reason = status_el.attrib.get("reason")
    headers = [_parse_set_header(h) for h in el.findall("set-header")]
    body_el = el.find("body")
    set_body_el = el.find("set-body")
    body = (
        _parse_set_body(set_body_el).value
        if set_body_el is not None
        else (_text_or_empty(body_el) if body_el is not None else None)
    )
    return ReturnResponse(status_code=code, reason=reason, headers=headers, body=body)


def _parse_mock_response(el: ElementTree.Element) -> MockResponse:
    status_code = int(el.attrib.get("status-code") or "200")
    content_type = el.attrib.get("content-type")
    return MockResponse(status_code=status_code, content_type=content_type)


def _required_attr(el: ElementTree.Element, name: str, policy_name: str) -> str:
    value = el.attrib.get(name)
    if value is None or not value.strip():
        raise HTTPException(status_code=500, detail=f"{policy_name} requires {name}")
    return value


def _parse_check_header(el: ElementTree.Element) -> CheckHeader:
    _reject_unknown_attributes(
        el, {"name", "failed-check-httpcode", "failed-check-error-message", "ignore-case"}, "check-header"
    )
    name = _required_attr(el, "name", "check-header").strip()
    status_code = _required_attr(el, "failed-check-httpcode", "check-header").strip()
    ignore_case = _required_attr(el, "ignore-case", "check-header").strip()
    if not is_apim_expression(status_code) and not status_code.isdigit():
        raise HTTPException(status_code=500, detail="check-header failed-check-httpcode must be an integer")
    if not is_apim_expression(ignore_case) and ignore_case.lower() not in {"true", "false"}:
        raise HTTPException(status_code=500, detail="check-header ignore-case must be true or false")
    message = el.attrib.get("failed-check-error-message")
    if message is None:
        raise HTTPException(status_code=500, detail="check-header requires failed-check-error-message")
    values = tuple(_text_or_empty(item) for item in el.findall("value"))
    return CheckHeader(name=name, status_code=status_code, message=message, ignore_case=ignore_case, values=values)


def _parse_ip_range(el: ElementTree.Element) -> tuple[IpAddress, IpAddress]:
    try:
        low = ipaddress.ip_address((el.attrib.get("from") or "").strip())
        high = ipaddress.ip_address((el.attrib.get("to") or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="ip-filter address-range needs valid from and to") from exc
    if low.version != high.version or low > high:
        raise HTTPException(status_code=500, detail="ip-filter address-range from must not exceed to")
    return low, high


def _parse_ip_address(el: ElementTree.Element) -> tuple[IpAddress, IpAddress]:
    try:
        ip = ipaddress.ip_address(_text_or_empty(el))
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="ip-filter address must be a single IP address") from exc
    return ip, ip


def _cors_values(el: ElementTree.Element, section: str, child: str) -> tuple[str, ...]:
    parent = el.find(section)
    if parent is None:
        return ()
    return tuple(value for item in parent.findall(child) if (value := _text_or_empty(item)))


def _parse_cors(el: ElementTree.Element) -> Cors:
    methods_el = el.find("allowed-methods")
    methods = _cors_values(el, "allowed-methods", "method") or ("GET", "POST")
    return Cors(
        origins=_cors_values(el, "allowed-origins", "origin"),
        methods=methods,
        headers=_cors_values(el, "allowed-headers", "header"),
        expose_headers=_cors_values(el, "expose-headers", "header"),
        allow_credentials=el.attrib.get("allow-credentials"),
        terminate_unmatched_request=el.attrib.get("terminate-unmatched-request"),
        preflight_max_age=methods_el.attrib.get("preflight-result-max-age") if methods_el is not None else None,
    )


def _parse_ip_filter(el: ElementTree.Element) -> IpFilter:
    _reject_unknown_attributes(el, {"action"}, "ip-filter")
    action = _required_attr(el, "action", "ip-filter").strip()
    if not is_apim_expression(action) and action.lower() not in {"allow", "forbid"}:
        raise HTTPException(status_code=500, detail="ip-filter action must be allow or forbid")
    unsupported = [child.tag for child in el if child.tag not in {"address", "address-range"}]
    if unsupported:
        raise HTTPException(status_code=500, detail=f"ip-filter unsupported element: {unsupported[0]}")
    ranges = tuple(_parse_ip_address(child) for child in el.findall("address")) + tuple(
        _parse_ip_range(child) for child in el.findall("address-range")
    )
    if not ranges:
        raise HTTPException(status_code=500, detail="ip-filter requires an address or address-range")
    return IpFilter(action=action, ranges=ranges)


def _reject_unknown_attributes(el: ElementTree.Element, allowed: set[str], policy_name: str) -> None:
    unknown = sorted(set(el.attrib) - allowed)
    if unknown:
        raise HTTPException(status_code=500, detail=f"{policy_name} unsupported attribute: {unknown[0]}")


def _static_policy_name(el: ElementTree.Element, name: str, policy_name: str) -> str | None:
    value = el.attrib.get(name)
    if value is not None and is_apim_expression(value):
        raise HTTPException(status_code=500, detail=f"{policy_name} {name} does not allow policy expressions")
    return value.strip() if value is not None else None


def _static_positive_int(
    el: ElementTree.Element,
    name: str,
    policy_name: str,
    *,
    required: bool = True,
    maximum: int | None = None,
) -> int | None:
    value = el.attrib.get(name)
    if value is None:
        if required:
            raise HTTPException(status_code=500, detail=f"{policy_name} requires {name}")
        return None
    if is_apim_expression(value):
        raise HTTPException(status_code=500, detail=f"{policy_name} {name} does not allow policy expressions")
    try:
        number = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"{policy_name} {name} must be an integer") from exc
    if number <= 0:
        raise HTTPException(status_code=500, detail=f"{policy_name} {name} must be > 0")
    if maximum is not None and number > maximum:
        raise HTTPException(status_code=500, detail=f"{policy_name} {name} must be <= {maximum}")
    return number


def _static_period(
    el: ElementTree.Element,
    policy_name: str,
    *,
    minimum: int = 1,
    allow_zero: bool = False,
    maximum: int | None = None,
) -> int:
    value = el.attrib.get("renewal-period")
    if value is None:
        raise HTTPException(status_code=500, detail=f"{policy_name} requires renewal-period")
    if is_apim_expression(value):
        raise HTTPException(status_code=500, detail=f"{policy_name} renewal-period does not allow policy expressions")
    try:
        period = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"{policy_name} renewal-period must be an integer") from exc
    if (period == 0 and allow_zero) or period >= minimum:
        if maximum is None or period <= maximum:
            return period
    if maximum is not None:
        raise HTTPException(
            status_code=500, detail=f"{policy_name} renewal-period must be between {minimum} and {maximum}"
        )
    raise HTTPException(status_code=500, detail=f"{policy_name} renewal-period must be >= {minimum}")


def _throttle_target(
    el: ElementTree.Element,
    kind: str,
    policy_name: str,
    *,
    allow_bandwidth: bool,
) -> tuple[str | None, str | None]:
    allowed = {"name", "id", "calls", "renewal-period"}
    if allow_bandwidth:
        allowed.add("bandwidth")
    _reject_unknown_attributes(
        el,
        allowed,
        policy_name,
    )
    target_id = (el.attrib.get("id") or "").strip() or None
    target_name = (el.attrib.get("name") or "").strip() or None
    if target_id is None and target_name is None:
        raise HTTPException(status_code=500, detail=f"{policy_name} {kind} requires name or id")
    return target_name, target_id


def _rate_limit_nested_rules(el: ElementTree.Element) -> tuple[ThrottleRule, ...]:
    rules: list[ThrottleRule] = []
    for api in el:
        if api.tag != "api":
            raise HTTPException(status_code=500, detail=f"rate-limit unsupported child element: {api.tag}")
        api_name, api_id = _throttle_target(api, "api", "rate-limit", allow_bandwidth=False)
        api_calls = _static_positive_int(api, "calls", "rate-limit", maximum=None)
        api_period = _static_period(api, "rate-limit", maximum=300)
        rules.append(ThrottleRule("api", api_name, api_id, api_calls or 0, api_period))
        for operation in api:
            if operation.tag != "operation":
                raise HTTPException(status_code=500, detail=f"rate-limit unsupported child element: {operation.tag}")
            operation_name, operation_id = _throttle_target(operation, "operation", "rate-limit", allow_bandwidth=False)
            operation_calls = _static_positive_int(operation, "calls", "rate-limit", maximum=None)
            operation_period = _static_period(operation, "rate-limit", maximum=300)
            rules.append(
                ThrottleRule("operation", operation_name, operation_id, operation_calls or 0, operation_period)
            )
    return tuple(rules)


def _parse_rate_limit(el: ElementTree.Element) -> RateLimit:
    _reject_unknown_attributes(
        el,
        {
            "id",
            "calls",
            "renewal-period",
            "retry-after-header-name",
            "retry-after-variable-name",
            "remaining-calls-header-name",
            "remaining-calls-variable-name",
            "total-calls-header-name",
        },
        "rate-limit",
    )
    calls = _static_positive_int(el, "calls", "rate-limit") or 0
    renewal = _static_period(el, "rate-limit", maximum=300)
    names = {
        name: _static_policy_name(el, name, "rate-limit")
        for name in (
            "retry-after-header-name",
            "retry-after-variable-name",
            "remaining-calls-header-name",
            "remaining-calls-variable-name",
            "total-calls-header-name",
        )
    }
    return RateLimit(
        calls=calls,
        renewal_period=renewal,
        retry_after_header_name=names["retry-after-header-name"],
        retry_after_variable_name=names["retry-after-variable-name"],
        remaining_calls_header_name=names["remaining-calls-header-name"],
        remaining_calls_variable_name=names["remaining-calls-variable-name"],
        total_calls_header_name=names["total-calls-header-name"],
        rules=_rate_limit_nested_rules(el),
    )


def _quota_nested_rules(el: ElementTree.Element) -> tuple[ThrottleRule, ...]:
    rules: list[ThrottleRule] = []
    for api in el:
        if api.tag != "api":
            raise HTTPException(status_code=500, detail=f"quota unsupported child element: {api.tag}")
        api_name, api_id = _throttle_target(api, "api", "quota", allow_bandwidth=True)
        api_calls = _static_positive_int(api, "calls", "quota", required=False)
        api_bandwidth = _static_positive_int(api, "bandwidth", "quota", required=False)
        if api_calls is None and api_bandwidth is None:
            raise HTTPException(status_code=500, detail="quota api requires calls or bandwidth")
        api_period = _static_period(api, "quota", allow_zero=True)
        rules.append(ThrottleRule("api", api_name, api_id, api_calls, api_period, api_bandwidth))
        for operation in api:
            if operation.tag != "operation":
                raise HTTPException(status_code=500, detail=f"quota unsupported child element: {operation.tag}")
            operation_name, operation_id = _throttle_target(operation, "operation", "quota", allow_bandwidth=True)
            operation_calls = _static_positive_int(operation, "calls", "quota", required=False)
            operation_bandwidth = _static_positive_int(operation, "bandwidth", "quota", required=False)
            if operation_calls is None and operation_bandwidth is None:
                raise HTTPException(status_code=500, detail="quota operation requires calls or bandwidth")
            operation_period = _static_period(operation, "quota", allow_zero=True)
            rules.append(
                ThrottleRule(
                    "operation", operation_name, operation_id, operation_calls, operation_period, operation_bandwidth
                )
            )
    return tuple(rules)


def _parse_quota(el: ElementTree.Element) -> Quota:
    _reject_unknown_attributes(el, {"id", "calls", "bandwidth", "renewal-period"}, "quota")
    calls = _static_positive_int(el, "calls", "quota", required=False)
    bandwidth = _static_positive_int(el, "bandwidth", "quota", required=False)
    if calls is None and bandwidth is None:
        raise HTTPException(status_code=500, detail="quota requires calls or bandwidth")
    renewal = _static_period(el, "quota", allow_zero=True)
    return Quota(calls=calls, bandwidth=bandwidth, renewal_period=renewal, rules=_quota_nested_rules(el))


def _parse_rate_limit_by_key(el: ElementTree.Element) -> RateLimitByKey:
    _reject_unknown_attributes(
        el,
        {
            "id",
            "calls",
            "renewal-period",
            "increment-condition",
            "increment-count",
            "counter-key",
            "retry-after-header-name",
            "retry-after-variable-name",
            "remaining-calls-header-name",
            "remaining-calls-variable-name",
            "total-calls-header-name",
        },
        "rate-limit-by-key",
    )
    calls = (el.attrib.get("calls") or "").strip()
    renewal_period = (el.attrib.get("renewal-period") or "").strip()
    counter_key = (el.attrib.get("counter-key") or "").strip()
    if not calls:
        raise HTTPException(status_code=500, detail="rate-limit-by-key requires calls")
    if not renewal_period:
        raise HTTPException(status_code=500, detail="rate-limit-by-key requires renewal-period")
    if not counter_key:
        raise HTTPException(status_code=500, detail="rate-limit-by-key requires counter-key")
    if not is_apim_expression(calls):
        _static_positive_int(el, "calls", "rate-limit-by-key")
    if not is_apim_expression(renewal_period):
        _static_period(el, "rate-limit-by-key", maximum=300)
    names = {
        name: _static_policy_name(el, name, "rate-limit-by-key")
        for name in (
            "retry-after-header-name",
            "retry-after-variable-name",
            "remaining-calls-header-name",
            "remaining-calls-variable-name",
            "total-calls-header-name",
        )
    }
    return RateLimitByKey(
        calls=calls,
        renewal_period=renewal_period,
        counter_key=counter_key,
        increment_condition=el.attrib.get("increment-condition"),
        increment_count=el.attrib.get("increment-count"),
        retry_after_header_name=names["retry-after-header-name"],
        retry_after_variable_name=names["retry-after-variable-name"],
        remaining_calls_header_name=names["remaining-calls-header-name"],
        remaining_calls_variable_name=names["remaining-calls-variable-name"],
        total_calls_header_name=names["total-calls-header-name"],
    )


def _parse_quota_by_key(el: ElementTree.Element) -> QuotaByKey:
    _reject_unknown_attributes(
        el,
        {
            "id",
            "calls",
            "bandwidth",
            "renewal-period",
            "increment-condition",
            "increment-count",
            "counter-key",
            "first-period-start",
        },
        "quota-by-key",
    )
    if "bandwidth" in el.attrib:
        raise HTTPException(status_code=500, detail="quota-by-key bandwidth is not supported")
    calls = (el.attrib.get("calls") or "").strip()
    renewal_period = (el.attrib.get("renewal-period") or "").strip()
    counter_key = (el.attrib.get("counter-key") or "").strip()
    if not calls:
        raise HTTPException(status_code=500, detail="quota-by-key requires calls")
    if not renewal_period:
        raise HTTPException(status_code=500, detail="quota-by-key requires renewal-period")
    if not counter_key:
        raise HTTPException(status_code=500, detail="quota-by-key requires counter-key")
    calls_value = _static_positive_int(el, "calls", "quota-by-key")
    renewal_value = _static_period(el, "quota-by-key", allow_zero=True, minimum=300)
    first_period_start = el.attrib.get("first-period-start") or "0001-01-01T00:00:00Z"
    if first_period_start:
        try:
            datetime.strptime(first_period_start, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="quota-by-key first-period-start must be UTC ISO-8601") from exc
    return QuotaByKey(
        calls=calls_value or 0,
        renewal_period=renewal_value,
        counter_key=counter_key,
        increment_condition=el.attrib.get("increment-condition"),
        increment_count=el.attrib.get("increment-count"),
        first_period_start=first_period_start,
    )


def _parse_llm_token_limit(el: ElementTree.Element) -> LlmTokenLimit:
    counter_key = el.attrib.get("counter-key")
    if not counter_key:
        raise HTTPException(status_code=500, detail="llm-token-limit missing counter-key")
    estimate_prompt_tokens = el.attrib.get("estimate-prompt-tokens")
    if estimate_prompt_tokens is None:
        raise HTTPException(status_code=500, detail="llm-token-limit missing estimate-prompt-tokens")
    tokens_per_minute = el.attrib.get("tokens-per-minute")
    token_quota = el.attrib.get("token-quota")
    token_quota_period = el.attrib.get("token-quota-period")
    if not tokens_per_minute and not token_quota:
        raise HTTPException(
            status_code=500,
            detail="llm-token-limit requires tokens-per-minute or token-quota",
        )
    if token_quota and not token_quota_period:
        raise HTTPException(status_code=500, detail="llm-token-limit token-quota requires token-quota-period")
    return LlmTokenLimit(
        counter_key=counter_key,
        estimate_prompt_tokens=estimate_prompt_tokens,
        tokens_per_minute=tokens_per_minute,
        token_quota=token_quota,
        token_quota_period=token_quota_period,
        retry_after_header_name=el.attrib.get("retry-after-header-name"),
        retry_after_variable_name=el.attrib.get("retry-after-variable-name"),
        remaining_tokens_header_name=el.attrib.get("remaining-tokens-header-name"),
        remaining_tokens_variable_name=el.attrib.get("remaining-tokens-variable-name"),
        remaining_quota_tokens_header_name=el.attrib.get("remaining-quota-tokens-header-name"),
        remaining_quota_tokens_variable_name=el.attrib.get("remaining-quota-tokens-variable-name"),
        tokens_consumed_header_name=el.attrib.get("tokens-consumed-header-name"),
        tokens_consumed_variable_name=el.attrib.get("tokens-consumed-variable-name"),
    )


def _parse_llm_emit_token_metric(el: ElementTree.Element) -> LlmEmitTokenMetric:
    dimensions: list[tuple[str, str | None]] = []
    for child in el.findall("dimension"):
        name = (child.attrib.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=500, detail="llm-emit-token-metric dimension missing name")
        dimensions.append((name, child.attrib.get("value")))
    return LlmEmitTokenMetric(
        namespace=(el.attrib.get("namespace") or "llm").strip() or "llm",
        dimensions=tuple(dimensions),
    )


def _parse_emit_metric(el: ElementTree.Element) -> EmitMetric:
    name = (el.attrib.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=500, detail="emit-metric missing name")
    dimensions: list[tuple[str, str | None]] = []
    for child in el.findall("dimension"):
        dim_name = (child.attrib.get("name") or "").strip()
        if not dim_name:
            raise HTTPException(status_code=500, detail="emit-metric dimension missing name")
        dimensions.append((dim_name, child.attrib.get("value")))
    if not dimensions:
        raise HTTPException(status_code=500, detail="emit-metric requires at least one dimension")
    return EmitMetric(
        name=name,
        namespace=(el.attrib.get("namespace") or "apim").strip() or "apim",
        value=el.attrib.get("value"),
        dimensions=tuple(dimensions),
    )


def _parse_validate_content(el: ElementTree.Element) -> ValidateContent:
    max_size_raw = (el.attrib.get("max-size") or "").strip()
    content_types: list[ValidateContentType] = []
    for child in el.findall("content"):
        content_type = (child.attrib.get("type") or "").strip()
        if not content_type:
            raise HTTPException(status_code=500, detail="validate-content content element missing type")
        validate_as = (child.attrib.get("validate-as") or "json").strip().lower()
        if validate_as != "json":
            raise HTTPException(status_code=500, detail=f"Unsupported validate-as: {validate_as}")
        content_types.append(
            ValidateContentType(
                content_type=content_type,
                validate_as=validate_as,
                action=_validation_action(child.attrib.get("action"), default="prevent"),
            )
        )
    return ValidateContent(
        unspecified_content_type_action=_validation_action(
            el.attrib.get("unspecified-content-type-action"), default="ignore"
        ),
        max_size=int(max_size_raw) if max_size_raw else None,
        size_exceeded_action=_validation_action(el.attrib.get("size-exceeded-action"), default="prevent"),
        errors_variable_name=el.attrib.get("errors-variable-name"),
        content_types=tuple(content_types),
    )


def _parse_validate_parameters(el: ElementTree.Element) -> ValidateParameters:
    headers_el = el.find("headers")
    query_el = el.find("query")

    def _child_action(child: ElementTree.Element | None, attr: str) -> str | None:
        if child is None or child.attrib.get(attr) is None:
            return None
        return _validation_action(child.attrib.get(attr), default="ignore")

    return ValidateParameters(
        specified_parameter_action=_validation_action(el.attrib.get("specified-parameter-action"), default="prevent"),
        unspecified_parameter_action=_validation_action(
            el.attrib.get("unspecified-parameter-action"), default="ignore"
        ),
        errors_variable_name=el.attrib.get("errors-variable-name"),
        headers_specified_action=_child_action(headers_el, "specified-parameter-action"),
        headers_unspecified_action=_child_action(headers_el, "unspecified-parameter-action"),
        query_specified_action=_child_action(query_el, "specified-parameter-action"),
        query_unspecified_action=_child_action(query_el, "unspecified-parameter-action"),
    )


def _parse_validate_status_code(el: ElementTree.Element) -> ValidateStatusCode:
    status_codes: list[tuple[int, str]] = []
    for child in el.findall("status-code"):
        code_raw = (child.attrib.get("code") or "").strip()
        if not code_raw:
            raise HTTPException(status_code=500, detail="validate-status-code status-code element missing code")
        status_codes.append((int(code_raw), _validation_action(child.attrib.get("action"), default="ignore")))
    return ValidateStatusCode(
        unspecified_status_code_action=_validation_action(
            el.attrib.get("unspecified-status-code-action"), default="prevent"
        ),
        errors_variable_name=el.attrib.get("errors-variable-name"),
        status_codes=tuple(status_codes),
    )


def _parse_cache_lookup(el: ElementTree.Element) -> CacheLookup:
    for attribute in ("vary-by-developer", "vary-by-developer-groups"):
        if attribute not in el.attrib:
            # Learn marks both attributes required but does not define the
            # policy-configuration error status/message.
            raise HTTPException(status_code=500, detail=f"cache-lookup requires {attribute}")
    _validate_cache_enum(
        el.attrib.get("caching-type"),
        name="caching-type",
        allowed={"internal", "external", "prefer-external"},
    )
    _validate_cache_enum(
        el.attrib.get("downstream-caching-type"),
        name="downstream-caching-type",
        allowed={"none", "private", "public"},
        allow_expression=True,
    )
    return CacheLookup(
        vary_by_headers=_vary_values(
            [_text_or_empty(item) for item in el.findall("vary-by-header") if _text_or_empty(item)]
        ),
        vary_by_query_parameters=_vary_values(
            [_text_or_empty(item) for item in el.findall("vary-by-query-parameter") if _text_or_empty(item)]
        ),
        vary_by_developer=str(el.attrib.get("vary-by-developer") or "false"),
        vary_by_developer_groups=str(el.attrib.get("vary-by-developer-groups") or "false"),
        downstream_caching_type=str(el.attrib.get("downstream-caching-type") or "none"),
        must_revalidate=str(el.attrib.get("must-revalidate") or "true"),
        allow_private_response_caching=str(el.attrib.get("allow-private-response-caching") or "false"),
        caching_type=str(el.attrib.get("caching-type") or "prefer-external"),
    )


def _parse_cache_store(el: ElementTree.Element) -> CacheStore:
    duration = (el.attrib.get("duration") or "").strip()
    if not duration:
        raise HTTPException(status_code=500, detail="cache-store requires duration")
    return CacheStore(duration=duration, cache_response=el.attrib.get("cache-response"))


def _parse_cache_lookup_value(el: ElementTree.Element) -> CacheLookupValue:
    key = (el.attrib.get("key") or "").strip()
    variable_name = (el.attrib.get("variable-name") or "").strip()
    if not key:
        raise HTTPException(status_code=500, detail="cache-lookup-value requires key")
    if not variable_name:
        raise HTTPException(status_code=500, detail="cache-lookup-value requires variable-name")
    _validate_cache_enum(
        el.attrib.get("caching-type"),
        name="caching-type",
        allowed={"internal", "external", "prefer-external"},
    )
    return CacheLookupValue(
        key=key,
        variable_name=variable_name,
        default_value=el.attrib.get("default-value"),
        caching_type=str(el.attrib.get("caching-type") or "prefer-external"),
    )


def _parse_cache_store_value(el: ElementTree.Element) -> CacheStoreValue:
    key = (el.attrib.get("key") or "").strip()
    value = (el.attrib.get("value") or "").strip()
    duration = (el.attrib.get("duration") or "").strip()
    if not key:
        raise HTTPException(status_code=500, detail="cache-store-value requires key")
    if value == "":
        raise HTTPException(status_code=500, detail="cache-store-value requires value")
    if not duration:
        raise HTTPException(status_code=500, detail="cache-store-value requires duration")
    _validate_cache_enum(
        el.attrib.get("caching-type"),
        name="caching-type",
        allowed={"internal", "external", "prefer-external"},
    )
    return CacheStoreValue(
        key=key,
        value=value,
        duration=duration,
        caching_type=str(el.attrib.get("caching-type") or "prefer-external"),
    )


def _parse_cache_remove_value(el: ElementTree.Element) -> CacheRemoveValue:
    key = (el.attrib.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=500, detail="cache-remove-value requires key")
    _validate_cache_enum(
        el.attrib.get("caching-type"),
        name="caching-type",
        allowed={"internal", "external", "prefer-external"},
    )
    return CacheRemoveValue(
        key=key,
        caching_type=str(el.attrib.get("caching-type") or "prefer-external"),
        fail_on_cache_removal_error=str(el.attrib.get("fail-on-cache-removal-error") or "false"),
    )


def _parse_validate_jwt(el: ElementTree.Element) -> ValidateJwt:
    required_claims: list[RequiredClaim] = []
    required_claims_el = el.find("required-claims")
    if required_claims_el is not None:
        for claim_el in required_claims_el.findall("claim"):
            name = (claim_el.attrib.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=500, detail="validate-jwt claim missing name")
            values = [_text_or_empty(value_el) for value_el in claim_el.findall("value") if _text_or_empty(value_el)]
            required_claims.append(
                RequiredClaim(
                    name=name,
                    values=values,
                    match=str(claim_el.attrib.get("match") or "all"),
                    separator=str(claim_el.attrib.get("separator")) if claim_el.attrib.get("separator") else None,
                )
            )

    return ValidateJwt(
        header_name=el.attrib.get("header-name"),
        query_parameter_name=el.attrib.get("query-parameter-name"),
        token_value=el.attrib.get("token-value"),
        failed_validation_httpcode=int(el.attrib.get("failed-validation-httpcode") or "401"),
        failed_validation_error_message=str(
            el.attrib.get("failed-validation-error-message") or "JWT validation failed"
        ),
        require_scheme=el.attrib.get("require-scheme"),
        require_expiration_time=str(el.attrib.get("require-expiration-time") or "true").lower() != "false",
        output_token_variable_name=el.attrib.get("output-token-variable-name"),
        openid_config_urls=[
            str(item.attrib.get("url")) for item in el.findall("openid-config") if item.attrib.get("url")
        ],
        issuers=[_text_or_empty(item) for item in el.findall("./issuers/issuer") if _text_or_empty(item)],
        audiences=[_text_or_empty(item) for item in el.findall("./audiences/audience") if _text_or_empty(item)],
        required_claims=required_claims,
    )


def _parse_set_backend_service(el: ElementTree.Element) -> SetBackendService:
    base_url = el.attrib.get("base-url")
    backend_id = el.attrib.get("backend-id")
    if not base_url and not backend_id:
        raise HTTPException(status_code=500, detail="set-backend-service requires backend-id or base-url")
    return SetBackendService(base_url=base_url, backend_id=backend_id)


def _parse_forward_request(el: ElementTree.Element) -> ForwardRequest:
    timeout = el.attrib.get("timeout")
    timeout_ms = el.attrib.get("timeout-ms")
    if timeout is not None and timeout_ms is not None:
        raise HTTPException(status_code=500, detail="forward-request cannot specify both timeout and timeout-ms")
    return ForwardRequest(
        timeout=timeout,
        timeout_ms=timeout_ms,
        follow_redirects=el.attrib.get("follow-redirects"),
        buffer_request_body=el.attrib.get("buffer-request-body"),
        buffer_response=el.attrib.get("buffer-response"),
        fail_on_error_status_code=el.attrib.get("fail-on-error-status-code"),
        http_version=el.attrib.get("http-version"),
    )


def _parse_send_request(el: ElementTree.Element) -> SendRequest:
    response_variable_name = (el.attrib.get("response-variable-name") or "").strip()
    if not response_variable_name:
        raise HTTPException(status_code=500, detail="send-request missing response-variable-name")
    auth_cert_el = el.find("authentication-certificate")
    auth_mi_el = el.find("authentication-managed-identity")
    return SendRequest(
        mode=str(el.attrib.get("mode") or "new"),
        response_variable_name=response_variable_name,
        timeout=el.attrib.get("timeout"),
        ignore_error=str(el.attrib.get("ignore-error") or "false").lower() == "true",
        url=_text_or_empty(el.find("set-url")) if el.find("set-url") is not None else None,
        method=_text_or_empty(el.find("set-method")) if el.find("set-method") is not None else None,
        headers=[_parse_set_header(item) for item in el.findall("set-header")],
        body=(_parse_set_body(el.find("set-body")).value if el.find("set-body") is not None else None),
        authentication_certificate_thumbprint=(
            str(auth_cert_el.attrib.get("thumbprint"))
            if auth_cert_el is not None and auth_cert_el.attrib.get("thumbprint")
            else None
        ),
        authentication_managed_identity_resource=(
            str(auth_mi_el.attrib.get("resource"))
            if auth_mi_el is not None and auth_mi_el.attrib.get("resource")
            else None
        ),
    )


def _rate_limit_key(req: PolicyRequest) -> str | None:
    """Return the subscription counter key used by the subscription rate policy."""
    return _subscription_throttle_key(req, "rate-limit")


def _parse_choose(
    el: ElementTree.Element,
    *,
    policy_fragments: dict[str, str],
    section_name: str,
    seen_fragments: set[str],
) -> Choose:
    branches: list[tuple[Condition, list[PolicyNode]]] = []
    for when in el.findall("when"):
        cond = parse_condition(when.attrib.get("condition"))
        steps = _parse_children(
            list(when),
            policy_fragments=policy_fragments,
            section_name=section_name,
            seen_fragments=set(seen_fragments),
            allow_base=False,
        )
        branches.append((cond, steps))
    otherwise_el = el.find("otherwise")
    otherwise_steps = (
        _parse_children(
            list(otherwise_el),
            policy_fragments=policy_fragments,
            section_name=section_name,
            seen_fragments=set(seen_fragments),
            allow_base=False,
        )
        if otherwise_el is not None
        else []
    )
    return Choose(branches=branches, otherwise=otherwise_steps)


def _fragment_elements(xml: str, *, section_name: str) -> list[ElementTree.Element]:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        try:
            root = ElementTree.fromstring(f"<fragment>{xml}</fragment>")
        except ElementTree.ParseError as exc:
            raise HTTPException(status_code=500, detail="Invalid policy fragment XML") from exc

    if root.tag == "policies":
        section = root.find(section_name)
        return list(section) if section is not None else []
    if root.tag == "fragment":
        return list(root)
    return [root]


def _parse_children(
    children: list[ElementTree.Element],
    *,
    policy_fragments: dict[str, str],
    section_name: str,
    seen_fragments: set[str],
    allow_base: bool = True,
) -> list[PolicyNode]:
    out: list[PolicyNode] = []
    for child in children:
        # APIM's base marker controls the containing section; it is not a
        # policy statement that can be deferred inside choose branches.
        if child.tag == "base" and not allow_base:
            raise HTTPException(status_code=500, detail="base element is only allowed directly inside a policy section")
        if child.tag == "include-fragment":
            fragment_id = (
                child.attrib.get("fragment-id") or child.attrib.get("name") or child.attrib.get("id") or ""
            ).strip()
            if not fragment_id:
                raise HTTPException(status_code=500, detail="include-fragment missing fragment-id")
            if fragment_id in seen_fragments:
                raise HTTPException(status_code=500, detail=f"Circular policy fragment include: {fragment_id}")
            fragment_xml = policy_fragments.get(fragment_id)
            if fragment_xml is None:
                raise HTTPException(status_code=500, detail=f"Unknown policy fragment: {fragment_id}")
            fragment_children = _fragment_elements(fragment_xml, section_name=section_name)
            out.extend(
                _parse_children(
                    fragment_children,
                    policy_fragments=policy_fragments,
                    section_name=section_name,
                    seen_fragments=seen_fragments | {fragment_id},
                    allow_base=allow_base,
                )
            )
            continue
        out.append(
            _parse_node(
                child,
                policy_fragments=policy_fragments,
                section_name=section_name,
                seen_fragments=seen_fragments,
            )
        )
    return out


# Policy elements whose parser needs nothing but the element itself. Some Azure
# elements have two spellings (the vendor-neutral `llm-*` and the older
# `azure-openai-*`); both map to the same parser rather than to two nodes.
_ELEMENT_PARSERS: dict[str, Callable[[ElementTree.Element], PolicyNode]] = {
    "set-header": _parse_set_header,
    "set-variable": _parse_set_variable,
    "set-query-parameter": _parse_set_query_parameter,
    "set-body": _parse_set_body,
    "rewrite-uri": _parse_rewrite_uri,
    "check-header": _parse_check_header,
    "ip-filter": _parse_ip_filter,
    "cors": _parse_cors,
    "rate-limit": _parse_rate_limit,
    "rate-limit-by-key": _parse_rate_limit_by_key,
    "quota": _parse_quota,
    "quota-by-key": _parse_quota_by_key,
    "llm-token-limit": _parse_llm_token_limit,
    "azure-openai-token-limit": _parse_llm_token_limit,
    "llm-emit-token-metric": _parse_llm_emit_token_metric,
    "azure-openai-emit-token-metric": _parse_llm_emit_token_metric,
    "emit-metric": _parse_emit_metric,
    "validate-content": _parse_validate_content,
    "validate-parameters": _parse_validate_parameters,
    "validate-status-code": _parse_validate_status_code,
    "cache-lookup": _parse_cache_lookup,
    "cache-store": _parse_cache_store,
    "cache-lookup-value": _parse_cache_lookup_value,
    "cache-store-value": _parse_cache_store_value,
    "cache-remove-value": _parse_cache_remove_value,
    "return-response": _parse_return_response,
    "mock-response": _parse_mock_response,
    "validate-jwt": _parse_validate_jwt,
    "set-backend-service": _parse_set_backend_service,
    "forward-request": _parse_forward_request,
    "send-request": _parse_send_request,
}

# Elements that carry no attributes worth reading.
_CONSTANT_ELEMENTS: dict[str, Callable[[], PolicyNode]] = {
    "base": NoOp,
}


def _parse_node(
    el: ElementTree.Element,
    *,
    policy_fragments: dict[str, str],
    section_name: str,
    seen_fragments: set[str],
) -> PolicyNode:
    """One policy element to one node.

    `choose` is the only element that can contain other elements, so it is the
    only one that needs the fragment table and the recursion guard.
    """
    tag = el.tag
    if tag == "forward-request" and section_name != "backend":
        raise HTTPException(status_code=500, detail="forward-request is only supported in the backend section")
    constant = _CONSTANT_ELEMENTS.get(tag)
    if constant is not None:
        return constant()
    if tag == "choose":
        return _parse_choose(
            el,
            policy_fragments=policy_fragments,
            section_name=section_name,
            seen_fragments=seen_fragments,
        )
    parser = _ELEMENT_PARSERS.get(tag)
    if parser is None:
        raise HTTPException(status_code=500, detail=f"Unsupported policy element: {tag}")
    return parser(el)


def parse_policies_xml(xml: str, *, policy_fragments: dict[str, str] | None = None) -> PolicyDocument:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=500, detail="Invalid policies XML") from exc
    if root.tag != "policies":
        raise HTTPException(status_code=500, detail="Policies XML must have <policies> root")

    fragments = policy_fragments or {}
    sections_present: set[str] = set()

    def section(name: str) -> list[PolicyNode]:
        sec = root.find(name)
        if sec is None:
            return []
        sections_present.add(name)
        return _parse_children(list(sec), policy_fragments=fragments, section_name=name, seen_fragments=set())

    return PolicyDocument(
        inbound=section("inbound"),
        backend=section("backend"),
        outbound=section("outbound"),
        on_error=section("on-error"),
        sections_present=frozenset(sections_present),
    )


async def _load_openid_configuration(url: str, runtime: PolicyRuntime) -> tuple[dict[str, Any], dict[str, Any]]:
    cached = runtime.openid_cache.get(url)
    if cached is not None:
        return cached
    if runtime.http_client is None:
        raise HTTPException(status_code=500, detail="validate-jwt requires an HTTP client")
    metadata_response = await runtime.http_client.get(url, timeout=runtime.timeout_seconds)
    metadata_response.raise_for_status()
    metadata = metadata_response.json()
    if not isinstance(metadata, dict) or not metadata.get("jwks_uri"):
        raise HTTPException(status_code=500, detail="Invalid openid-config document")
    jwks_response = await runtime.http_client.get(str(metadata["jwks_uri"]), timeout=runtime.timeout_seconds)
    jwks_response.raise_for_status()
    jwks = jwks_response.json()
    if not isinstance(jwks, dict):
        raise HTTPException(status_code=500, detail="Invalid JWKS document")
    runtime.openid_cache[url] = (metadata, jwks)
    return metadata, jwks


async def _apply_steps_async(
    steps: list[PolicyNode],
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
) -> ResponseSpec | None:
    for step in steps:
        req.variables["_policy_step"] = element_name(step)
        out = await step.apply_async(req, runtime)
        if out is not None:
            return out
    return None


ScopedStep = tuple[str, PolicyNode]


def _replace_base_steps(local: list[PolicyNode], parent: list[ScopedStep], scope: str) -> list[ScopedStep]:
    """Replace each direct base marker with the effective parent steps."""
    out: list[ScopedStep] = []
    for step in local:
        if isinstance(step, NoOp):
            out.extend(parent)
        else:
            out.append((scope, step))
    return out


def _effective_section_steps(docs: list[PolicyDocument], section_name: str) -> list[ScopedStep]:
    """Resolve one section across the broad-to-narrow document stack.

    Each step keeps the scope of the document that authored it.
    """
    effective: list[ScopedStep] = []
    section_key = "on-error" if section_name == "on_error" else section_name
    for doc in docs:
        # A section present without base drops the parent. An omitted section
        # is taken as the default, which Learn says includes base in every
        # section; the page doesn't state the omitted case outright.
        # https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
        if section_key not in doc.sections_present:
            continue
        local = getattr(doc, section_name.replace("-", "_"))
        if any(isinstance(step, NoOp) for step in local):
            effective = _replace_base_steps(local, effective, doc.scope)
        else:
            effective = [(doc.scope, step) for step in local]
    return effective


async def _apply_section_async(
    docs: list[PolicyDocument],
    section_name: str,
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
) -> ResponseSpec | None:
    for scope, step in _effective_section_steps(docs, section_name):
        req.variables["_policy_scope"] = scope
        req.variables["_policy_step"] = element_name(step)
        out = await step.apply_async(req, runtime)
        if out is not None:
            return out
    return None


def _reads_response_body(step: PolicyNode) -> bool:
    if isinstance(step, Choose):
        nested = [item for _cond, steps in step.branches for item in steps] + list(step.otherwise)
        return any(_reads_response_body(item) for item in nested)
    return isinstance(step, SetBody | ValidateContent)


def outbound_reads_response_body(docs: list[PolicyDocument]) -> bool:
    """Whether an outbound step needs the response body in memory.

    Streaming leaves the body unread, so set-body and validate-content would see
    nothing unless the gateway buffers first.
    """
    return any(_reads_response_body(step) for _scope, step in _effective_section_steps(docs, "outbound"))


async def apply_inbound_async(
    docs: list[PolicyDocument],
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
) -> ResponseSpec | None:
    return await _apply_section_async(docs, "inbound", req, runtime)


async def apply_backend_async(
    docs: list[PolicyDocument],
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
) -> ResponseSpec | None:
    return await _apply_section_async(docs, "backend", req, runtime)


async def apply_outbound_async(
    docs: list[PolicyDocument],
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
) -> ResponseSpec | None:
    """Run the outbound section; a returned spec replaces the response.

    return-response and mock-response are valid in outbound and end the section.
    https://learn.microsoft.com/en-us/azure/api-management/return-response-policy
    """
    req.section = "outbound"
    return await _apply_section_async(docs, "outbound", req, runtime)


async def apply_on_error_async(
    docs: list[PolicyDocument],
    req: PolicyRequest,
    runtime: PolicyRuntime | None = None,
) -> ResponseSpec | None:
    return await _apply_section_async(docs, "on_error", req, runtime)


def finalize_deferred_actions(req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
    if runtime is None or not runtime.deferred_actions:
        apply_pending_response_headers(req, _response_header_target(req))
        return
    actions = list(runtime.deferred_actions)
    runtime.deferred_actions.clear()
    for action in actions:
        action.finalize(req, runtime)
    apply_pending_response_headers(req, _response_header_target(req))


def apply_inbound(
    docs: list[PolicyDocument], req: PolicyRequest, runtime: PolicyRuntime | None = None
) -> ResponseSpec | None:
    return asyncio.run(apply_inbound_async(docs, req, runtime))


def apply_backend(
    docs: list[PolicyDocument], req: PolicyRequest, runtime: PolicyRuntime | None = None
) -> ResponseSpec | None:
    return asyncio.run(apply_backend_async(docs, req, runtime))


def apply_outbound(
    docs: list[PolicyDocument],
    *,
    headers: dict[str, str],
    variables: dict[str, Any] | None = None,
    response_status_code: int | None = None,
    response_body: bytes = b"",
    response_media_type: str | None = None,
    runtime: PolicyRuntime | None = None,
) -> None:
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers=headers,
        variables=variables or {},
        response_status_code=response_status_code,
        response_headers=headers,
        response_body=response_body,
        response_media_type=response_media_type,
    )
    asyncio.run(apply_outbound_async(docs, req, runtime))
    finalize_deferred_actions(req, runtime)


def apply_on_error(
    docs: list[PolicyDocument], req: PolicyRequest, runtime: PolicyRuntime | None = None
) -> ResponseSpec | None:
    return asyncio.run(apply_on_error_async(docs, req, runtime))
