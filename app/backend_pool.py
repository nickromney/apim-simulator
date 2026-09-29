"""Backend pool selection, health, and failover.

Houses ADR 0003 D2: deterministic weighted round-robin, priority groups,
and in-memory circuit breakers.
"""

from __future__ import annotations

import base64
from typing import Any

from app.config import BackendCircuitBreakerConfig, BackendConfig, GatewayConfig
from app.policy import PolicyRequest, PolicyRuntime, issue_local_managed_identity_token, render_policy_value

_DEFAULT_POOL_CIRCUIT_BREAKER = BackendCircuitBreakerConfig()


def pool_member_breaker(pool_backend: BackendConfig, member_backend: BackendConfig) -> BackendCircuitBreakerConfig:
    return member_backend.circuit_breaker or pool_backend.circuit_breaker or _DEFAULT_POOL_CIRCUIT_BREAKER


def _status_reason(status_code: int) -> str:
    if 500 <= status_code <= 599:
        return "Server errors"
    if 400 <= status_code <= 499:
        return "Client errors"
    return ""


def backend_failure_condition_matches(breaker: BackendCircuitBreakerConfig, status_code: int) -> bool:
    """Apply the backend rule's status range and error-reason conditions."""
    in_range = (
        any(item.min <= status_code <= item.max for item in breaker.status_code_ranges)
        if breaker.status_code_ranges
        else status_code in breaker.error_statuses
    )
    if not in_range:
        return False
    if not breaker.error_reasons:
        return True
    reason = _status_reason(status_code)
    return any(expected.casefold() == reason.casefold() for expected in breaker.error_reasons)


def backend_connection_failure_matches(breaker: BackendCircuitBreakerConfig) -> bool:
    """Match transport failures when a rule names a connection error reason.

    The Learn article documents ``errorReasons`` but does not enumerate its
    strings; these are the simulator's explicit mappings for transport errors.
    """
    transport_reasons = {
        "backend connection failure",
        "backend connection failures",
        "connection failure",
        "connection failures",
        "timeout",
        "timeouts",
    }
    return any(reason.casefold() in transport_reasons for reason in breaker.error_reasons)


def backend_health_entry(health: dict[str, Any], backend_id: str) -> dict[str, Any]:
    entry = health.setdefault(backend_id, {"failures": [], "open_until": 0.0})
    if not isinstance(entry, dict):
        entry = {"failures": [], "open_until": 0.0}
        health[backend_id] = entry
    return entry


def record_backend_result(
    health: dict[str, Any],
    breaker: BackendCircuitBreakerConfig,
    backend_id: str,
    *,
    now: float,
    failed: bool,
    trip_duration_seconds: float | None = None,
) -> None:
    entry = backend_health_entry(health, backend_id)
    if not failed:
        entry["failures"] = []
        return
    failures = [t for t in entry.get("failures", []) if t > now - breaker.interval_seconds]
    failures.append(now)
    if len(failures) >= max(1, breaker.failure_count):
        duration = breaker.trip_duration_seconds if trip_duration_seconds is None else trip_duration_seconds
        entry["open_until"] = now + duration
        entry["failures"] = []
    else:
        entry["failures"] = failures


def select_pool_member(
    cfg: GatewayConfig,
    health: dict[str, Any],
    pool_id: str,
    pool_backend: BackendConfig,
    *,
    now: float,
    session_id: str | None = None,
) -> tuple[str, BackendConfig] | None:
    members = [m for m in pool_backend.pool if cfg.backends.get(m.backend_id) is not None]
    if not members:
        return None
    if session_id and pool_backend.session_affinity is not None:
        affinity_member = next((member for member in members if member.backend_id == session_id), None)
        if affinity_member is not None:
            affinity_entry = backend_health_entry(health, affinity_member.backend_id)
            if float(affinity_entry.get("open_until", 0.0)) <= now:
                return affinity_member.backend_id, cfg.backends[affinity_member.backend_id]
    rotation = backend_health_entry(health, f"pool:{pool_id}")
    for priority in sorted({m.priority for m in members}):
        group = [m for m in members if m.priority == priority]
        # Deterministic weighted round-robin: expand by weight, then walk the
        # schedule from the pool's rotation cursor skipping open circuits.
        schedule = [m for m in group for _ in range(max(1, m.weight))]
        start = int(rotation.get(f"rr:{priority}", 0))
        for offset in range(len(schedule)):
            member = schedule[(start + offset) % len(schedule)]
            member_entry = backend_health_entry(health, member.backend_id)
            if float(member_entry.get("open_until", 0.0)) <= now:
                rotation[f"rr:{priority}"] = (start + offset + 1) % len(schedule)
                return member.backend_id, cfg.backends[member.backend_id]
    return None


def render_backend_value(value: str | None, policy_req: PolicyRequest, cfg: GatewayConfig) -> str | None:
    if value is None:
        return None
    runtime = PolicyRuntime(gateway_config=cfg)
    return render_policy_value(value, policy_req, runtime)


def _apply_auth_type(backend: BackendConfig, policy_req: PolicyRequest, cfg: GatewayConfig) -> tuple[str, str] | None:
    """Apply the backend's declared auth scheme.

    Basic auth and managed identity replace any caller Authorization header, as
    APIM authentication policies do. The local managed-identity token is an
    opaque simulator adaptation, not a Microsoft Entra token.
    """
    auth_type = (backend.auth_type or "none").lower()

    if auth_type == "basic":
        username = render_backend_value(backend.basic_username, policy_req, cfg)
        password = render_backend_value(backend.basic_password, policy_req, cfg)
        if username and password:
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            policy_req.headers["authorization"] = f"Basic {encoded}"
            return (username, password)
        return None

    if auth_type == "managed_identity":
        resource = render_backend_value(backend.managed_identity_resource, policy_req, cfg) or ""
        token = issue_local_managed_identity_token(resource)
        policy_req.headers["authorization"] = f"Bearer {token}"
    elif auth_type == "client_certificate":
        policy_req.headers.setdefault("x-apim-client-certificate", "present")
    return None


def _apply_authorization_header(backend: BackendConfig, policy_req: PolicyRequest, cfg: GatewayConfig) -> None:
    """Set an explicit `scheme parameter` Authorization, unless one already exists."""
    if not (backend.authorization_scheme and backend.authorization_parameter):
        return
    if "authorization" in policy_req.headers:
        return
    scheme = render_backend_value(backend.authorization_scheme, policy_req, cfg) or ""
    parameter = render_backend_value(backend.authorization_parameter, policy_req, cfg) or ""
    policy_req.headers["authorization"] = f"{scheme} {parameter}".strip()


def _apply_credential_pairs(backend: BackendConfig, policy_req: PolicyRequest, cfg: GatewayConfig) -> None:
    """Add the backend's header and query credentials to the upstream call."""
    for header_name, header_value in backend.header_credentials.items():
        rendered = render_backend_value(header_value, policy_req, cfg)
        if rendered is not None:
            policy_req.headers[header_name.lower()] = rendered

    for query_name, query_value in backend.query_credentials.items():
        rendered = render_backend_value(query_value, policy_req, cfg)
        if rendered is not None:
            policy_req.query[query_name] = rendered


def apply_backend_credentials(
    backend: BackendConfig,
    policy_req: PolicyRequest,
    cfg: GatewayConfig,
) -> tuple[str, str] | None:
    upstream_auth = _apply_auth_type(backend, policy_req, cfg)
    _apply_authorization_header(backend, policy_req, cfg)
    _apply_credential_pairs(backend, policy_req, cfg)

    if backend.client_certificate_thumbprints:
        policy_req.headers.setdefault(
            "x-apim-client-certificate-thumbprints",
            ",".join(backend.client_certificate_thumbprints),
        )
    return upstream_auth
