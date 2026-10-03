"""Authenticated encrypted backup and validated atomic recovery."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.config import GatewayConfig
from app.secret_storage import decrypt_config, encrypt_config
from app.security import require_tenant_access


def build_security_recovery_router(*, require_management_plane):
    router = APIRouter()

    @router.post("/apim/security/backups")
    async def backup(request: Request) -> dict[str, Any]:
        require_tenant_access(request, permission="operate")
        cfg = request.app.state.gateway_config
        try:
            return encrypt_config(cfg.model_dump(mode="json"), cfg.secret_storage.key_env)
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/apim/security/restore")
    async def restore(request: Request) -> dict[str, Any]:
        require_tenant_access(request, permission="write")
        raw = await _bounded_snapshot(request)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("format") != "apim-encrypted-config-v1":
                raise ValueError("Restore requires an encrypted recovery snapshot")
            candidate = GatewayConfig.model_validate(decrypt_config(payload))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Deployment location belongs to the destination process, even when
        # resource configuration comes from another gateway backup.
        candidate.service.region = request.app.state.gateway_config.service.region
        validate_recovery_security(request.app.state.gateway_config, candidate)
        require_management_plane().persist_or_apply_config(candidate)
        return {"status": "restored", "apis": len(candidate.apis), "routes": len(candidate.routes)}

    return router


async def _bounded_snapshot(request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 8 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Recovery snapshot exceeds 8 MiB")
        chunks.append(chunk)
    return b"".join(chunks)


def _disabled_flags(previous, candidate, names):
    return [name for name in names if getattr(previous, name) and not getattr(candidate, name)]


def validate_recovery_security(previous: GatewayConfig, candidate: GatewayConfig) -> None:
    """Recovery restores resources without silently weakening current controls."""
    failures = _disabled_flags(
        previous.control_plane, candidate.control_plane, ["enabled", "require_mfa", "require_compliant_device"]
    )
    if not previous.control_plane.allow_legacy_tenant_keys and candidate.control_plane.allow_legacy_tenant_keys:
        failures.append("allow_legacy_tenant_keys")
    failures += _disabled_flags(
        previous.security_governance,
        candidate.security_governance,
        ["encrypted_protocols", "backend_certificate_verification", "vault_secret_named_values", "private_gateway"],
    )
    failures += _recovery_governance_failures(previous.security_governance, candidate.security_governance)
    failures += _recovery_ingress_failures(previous.security_ingress, candidate.security_ingress)
    failures += _disabled_flags(previous.security_observability, candidate.security_observability, ["enabled"])
    failures += _disabled_flags(previous.secret_storage, candidate.secret_storage, ["enabled"])
    failures += _recovery_trust_failures(previous, candidate)
    if failures:
        raise HTTPException(
            status_code=409,
            detail={"message": "Recovery would weaken current security controls", "controls": sorted(set(failures))},
        )


def _recovery_governance_failures(previous, candidate):
    failures = []
    if previous.mode == "deny" and candidate.mode != "deny":
        failures.append("governance-mode")
    if not set(previous.required_api_tags).issubset(candidate.required_api_tags):
        failures.append("required-api-tags")
    for collection, identifiers in previous.delete_locks.items():
        if not set(identifiers).issubset(candidate.delete_locks.get(collection, [])):
            failures.append("delete-locks")
    return failures


def _recovery_trust_failures(previous, candidate):
    failures = []
    if previous.control_plane.enabled:
        for name in ["issuer", "audience", "signing_key_env"]:
            if getattr(previous.control_plane, name) != getattr(candidate.control_plane, name):
                failures.append("operator-trust-" + name)
    if previous.secret_storage.enabled and previous.secret_storage.key_env != candidate.secret_storage.key_env:
        failures.append("secret-storage-key")
    if (
        previous.security_observability.enabled
        and previous.security_observability.database_path != candidate.security_observability.database_path
    ):
        failures.append("audit-store")
    failures += _recovery_portal_failures(previous.portal, candidate.portal)
    failures += _recovery_network_failures(previous, candidate)
    failures += _recovery_operator_scope_failures(previous.control_plane, candidate.control_plane)
    failures += _recovery_gateway_identity_failures(previous, candidate)
    failures += _recovery_certificate_failures(previous.client_certificate, candidate.client_certificate)
    failures += _recovery_workload_failures(previous.workload_identity, candidate.workload_identity)
    failures += _recovery_backend_tls_failures(previous.backends, candidate.backends)
    return failures


def _recovery_portal_failures(previous, candidate):
    if not previous.identity.enabled:
        return []
    failures = []
    if not candidate.identity.enabled:
        failures.append("portal-signed-identity")
    if not previous.identity.allow_legacy_user_header and candidate.identity.allow_legacy_user_header:
        failures.append("portal-user-header")
    for name in ["issuer", "audience", "signing_key_env"]:
        if getattr(previous.identity, name) != getattr(candidate.identity, name):
            failures.append("portal-trust-" + name)
    return failures


def _private_gateway(config):
    return (
        config.service.public_network_access_enabled is False
        or str(config.service.virtual_network_type).casefold() == "internal"
    )


def _recovery_network_failures(previous, candidate):
    before, after = previous.network_security, candidate.network_security
    failures = []
    if _private_gateway(previous) and not _private_gateway(candidate):
        failures.append("private-network-boundary")
    if not before.allow_simulated_forwarded_headers and after.allow_simulated_forwarded_headers:
        failures.append("simulated-forwarding")
    if set(before.trusted_proxy_cidrs) != set(after.trusted_proxy_cidrs):
        failures.append("trusted-proxies")
    if _private_gateway(previous) and set(before.private_peer_cidrs) != set(after.private_peer_cidrs):
        failures.append("private-peers")
    for name in ["allowed_backend_hosts", "allowed_backend_cidrs"]:
        current, restored = getattr(before, name), getattr(after, name)
        if current is not None and (restored is None or set(current) != set(restored)):
            failures.append("egress-" + name)
    return failures


def _recovery_operator_scope_failures(previous, candidate):
    if not previous.enabled:
        return []
    failures = []
    if (previous.require_mfa or previous.require_compliant_device) and not set(previous.administrator_roles).issubset(
        candidate.administrator_roles
    ):
        failures.append("administrator-role-conditions")
    if previous.workspace_apis != candidate.workspace_apis:
        failures.append("workspace-permission-boundary")
    return failures


def _recovery_gateway_identity_failures(previous, candidate):
    compatibility = ["allow_anonymous", "trace_allow_unauthenticated", "allow_simulated_certificate_authentication"]
    failures = [name for name in compatibility if not getattr(previous, name) and getattr(candidate, name)]
    if not previous.allow_anonymous and (
        previous.oidc != candidate.oidc or previous.oidc_providers != candidate.oidc_providers
    ):
        failures.append("gateway-issuer-trust")
    if previous.subscription.required and not candidate.subscription.required:
        failures.append("subscription-required")
    if previous.subscription.bypass != candidate.subscription.bypass:
        failures.append("subscription-bypass")
    return failures


def _recovery_certificate_failures(previous, candidate):
    levels = {"disabled": 0, "optional": 1, "required": 2}
    failures = []
    if levels[candidate.mode] < levels[previous.mode]:
        failures.append("client-certificate-mode")
    if not previous.allow_simulated_headers and candidate.allow_simulated_headers:
        failures.append("simulated-certificate-headers")
    trust = ["ca_file", "crl_file", "trusted_certificates"] if previous.mode != "disabled" else []
    headers = ["subject_header", "issuer_header", "thumbprint_header", "cert_header"]
    for name in trust + headers:
        if getattr(previous, name) != getattr(candidate, name):
            failures.append("certificate-trust-" + name)
    return failures


def _recovery_workload_failures(previous, candidate):
    if previous.mode != "signed":
        return []
    failures = []
    if candidate.mode != "signed":
        failures.append("workload-signed-identity")
    for name in ["issuer", "private_key_file", "public_key_file", "audience_grants"]:
        if getattr(previous, name) != getattr(candidate, name):
            failures.append("workload-trust-" + name)
    if candidate.token_lifetime_seconds > previous.token_lifetime_seconds:
        failures.append("workload-token-lifetime")
    return failures


def _recovery_backend_tls_failures(previous, candidate):
    failures = []
    for identifier in set(previous) & set(candidate):
        before, after = previous[identifier], candidate[identifier]
        controls = ["verify_certificate_chain", "verify_certificate_name"]
        failures.extend("backend-" + identifier + "-" + name for name in _disabled_flags(before, after, controls))
        if not before.allow_simulated_certificate and after.allow_simulated_certificate:
            failures.append("backend-" + identifier + "-simulated-certificate")
        for name in ["ca_file", "crl_file"]:
            if getattr(before, name) and getattr(before, name) != getattr(after, name):
                failures.append("backend-" + identifier + "-" + name)
    return failures


def _recovery_ingress_failures(previous, candidate):
    if not previous.enabled:
        return []
    failures = []
    if not candidate.enabled:
        failures.append("ingress-enabled")
    if set(previous.exclude_prefixes) != set(candidate.exclude_prefixes):
        failures.append("ingress-excluded-paths")
    if previous.waf_mode == "block" and candidate.waf_mode != "block":
        failures.append("waf-mode")
    if not set(previous.waf_rules).issubset(candidate.waf_rules):
        failures.append("waf-rules")
    if previous.allowed_client_networks and previous.allowed_client_networks != candidate.allowed_client_networks:
        failures.append("allowed-client-networks")
    failures += _recovery_ingress_limit_failures(previous, candidate)
    return failures


def _recovery_ingress_limit_failures(previous, candidate):
    limits = ["max_body_bytes", "max_concurrent_requests", "requests_per_window", "max_query_bytes"]
    failures = [name for name in limits if getattr(candidate, name) > getattr(previous, name)]
    if candidate.window_seconds < previous.window_seconds:
        failures.append("ingress-rate-window")
    if candidate.body_read_timeout_seconds > previous.body_read_timeout_seconds:
        failures.append("ingress-body-timeout")
    return failures
