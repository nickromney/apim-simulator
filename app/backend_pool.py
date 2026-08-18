"""Backend pool selection, health, and failover.

Houses ADR 0003 D2: deterministic weighted round-robin, priority groups,
and in-memory circuit breakers.
"""

from __future__ import annotations

from typing import Any

from app.config import BackendCircuitBreakerConfig, BackendConfig, GatewayConfig
from app.policy import PolicyRequest, PolicyRuntime, render_policy_value

_DEFAULT_POOL_CIRCUIT_BREAKER = BackendCircuitBreakerConfig()


def pool_member_breaker(pool_backend: BackendConfig, member_backend: BackendConfig) -> BackendCircuitBreakerConfig:
    return member_backend.circuit_breaker or pool_backend.circuit_breaker or _DEFAULT_POOL_CIRCUIT_BREAKER


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
) -> None:
    entry = backend_health_entry(health, backend_id)
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


def select_pool_member(
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


def apply_backend_credentials(
    backend: BackendConfig,
    policy_req: PolicyRequest,
    cfg: GatewayConfig,
) -> tuple[str, str] | None:
    upstream_auth: tuple[str, str] | None = None
    auth_type = (backend.auth_type or "none").lower()
    if auth_type == "basic":
        username = render_backend_value(backend.basic_username, policy_req, cfg)
        password = render_backend_value(backend.basic_password, policy_req, cfg)
        if "authorization" not in policy_req.headers and username and password:
            upstream_auth = (username, password)
    elif auth_type == "managed_identity":
        policy_req.headers.setdefault("x-apim-managed-identity", "true")
        if backend.managed_identity_resource:
            policy_req.headers.setdefault(
                "x-apim-managed-identity-resource",
                render_backend_value(backend.managed_identity_resource, policy_req, cfg),
            )
    elif auth_type == "client_certificate":
        policy_req.headers.setdefault("x-apim-client-certificate", "present")

    if backend.authorization_scheme and backend.authorization_parameter and "authorization" not in policy_req.headers:
        scheme = render_backend_value(backend.authorization_scheme, policy_req, cfg) or ""
        parameter = render_backend_value(backend.authorization_parameter, policy_req, cfg) or ""
        policy_req.headers["authorization"] = f"{scheme} {parameter}".strip()

    for header_name, header_value in backend.header_credentials.items():
        rendered = render_backend_value(header_value, policy_req, cfg)
        if rendered is not None:
            policy_req.headers[header_name.lower()] = rendered

    for query_name, query_value in backend.query_credentials.items():
        rendered = render_backend_value(query_value, policy_req, cfg)
        if rendered is not None:
            policy_req.query[query_name] = rendered

    if backend.client_certificate_thumbprints:
        policy_req.headers.setdefault(
            "x-apim-client-certificate-thumbprints",
            ",".join(backend.client_certificate_thumbprints),
        )
    return upstream_auth
