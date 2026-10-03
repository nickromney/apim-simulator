"""Local equivalents of APIM configuration policy and endpoint posture checks."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from app.security import require_tenant_access
from app.security_settings import SecurityGovernanceConfig

POLICY_SOURCE = "https://learn.microsoft.com/en-us/azure/api-management/policy-reference"
LOCK_COLLECTIONS = frozenset(
    {"apis", "products", "backends", "named_values", "users", "groups", "loggers", "tags", "policy_fragments"}
)


def _finding(control: str, resource: str, recommendation: str) -> dict:
    return {"control": control, "resource": resource, "recommendation": recommendation, "source": POLICY_SOURCE}


def governance_findings(cfg: Any) -> list[dict]:
    settings = cfg.security_governance
    findings = []
    for identifier, api in cfg.apis.items():
        if settings.encrypted_protocols and any(protocol not in {"https", "wss"} for protocol in api.protocols):
            findings.append(_finding("encrypted-protocols", "apis/" + identifier, "Publish HTTPS/WSS protocols only."))
        missing = set(settings.required_api_tags) - set(api.tags)
        if missing:
            findings.append(
                _finding("required-tags", "apis/" + identifier, "Assign required tags: " + ", ".join(sorted(missing)))
            )
    findings.extend(_backend_findings(cfg))
    for identifier, value in cfg.named_values.items():
        if settings.vault_secret_named_values and value.secret and value.value_from_key_vault is None:
            findings.append(
                _finding(
                    "vault-secret-named-values",
                    "named_values/" + identifier,
                    "Reference a secret in the configured local vault.",
                )
            )
    if settings.private_gateway and not _private_configured(cfg):
        findings.append(
            _finding(
                "private-gateway",
                "service",
                "Disable public network metadata, use Internal mode, and configure explicit private socket-peer networks.",
            )
        )
    return findings


def _backend_findings(cfg: Any) -> list[dict]:
    findings = []
    if not cfg.security_governance.backend_certificate_verification:
        return findings
    for identifier, backend in cfg.backends.items():
        if backend.type != "pool" and (
            urlsplit(backend.url).scheme != "https"
            or not backend.verify_certificate_chain
            or not backend.verify_certificate_name
        ):
            findings.append(
                _finding(
                    "backend-certificate-verification",
                    "backends/" + identifier,
                    "Use HTTPS and retain certificate chain/name validation.",
                )
            )
    for route in cfg.materialize_routes():
        if not route.backend and urlsplit(route.upstream_base_url).scheme != "https":
            findings.append(
                _finding(
                    "backend-certificate-verification",
                    "routes/" + route.name,
                    "Use an HTTPS backend with certificate verification.",
                )
            )
    return findings


def _private_configured(cfg: Any) -> bool:
    return (
        cfg.service.public_network_access_enabled is False
        and (cfg.service.virtual_network_type or "").lower() == "internal"
        and bool(cfg.network_security.private_peer_cidrs)
        and all(ipaddress.ip_network(network).is_private for network in cfg.network_security.private_peer_cidrs)
    )


def validate_governance_mutation(previous: Any, candidate: Any) -> list[dict]:
    """Call before persisting/publishing candidate; failed changes leave state intact."""
    if previous is not None:
        _validate_delete_locks(previous, candidate)
    unknown = set(candidate.security_governance.delete_locks) - LOCK_COLLECTIONS
    if unknown:
        raise HTTPException(400, "Unknown delete-lock collection: " + sorted(unknown)[0])
    findings = governance_findings(candidate)
    if candidate.security_governance.mode == "deny" and findings:
        raise HTTPException(403, {"message": "Configuration denied by local governance", "findings": findings})
    return findings


def _validate_delete_locks(previous: Any, candidate: Any) -> None:
    for collection, identifiers in previous.security_governance.delete_locks.items():
        if collection not in LOCK_COLLECTIONS:
            raise HTTPException(400, "Unknown delete-lock collection: " + collection)
        before, after = getattr(previous, collection), getattr(candidate, collection)
        locked = set(identifiers) & (set(before) - set(after))
        if locked:
            raise HTTPException(409, "Resource deletion is locked: " + collection + "/" + sorted(locked)[0])
        if collection == "apis":
            for identifier in sorted(set(identifiers) & set(before) & set(after)):
                removed = _api_children(before[identifier]) - _api_children(after[identifier])
                if removed:
                    raise HTTPException(
                        409, "Resource deletion is locked: apis/" + identifier + "/" + sorted(removed)[0]
                    )


def _api_children(api: Any) -> set[str]:
    """Resource identities covered by an API lock, including revision snapshots."""
    return _definition_children(api.model_dump(exclude_none=True))


def _policy_resource(value: dict, prefix: str) -> set[str]:
    return {prefix + "policy"} if value.get("policies_xml") is not None else set()


def _definition_children(value: dict, prefix: str = "") -> set[str]:
    resources = _policy_resource(value, prefix)
    for collection in ("operations", "schemas", "revisions", "releases"):
        for identifier, child in value.get(collection, {}).items():
            path = prefix + collection + "/" + identifier
            resources.add(path)
            resources.update(_policy_resource(child, path + "/"))
            if collection == "revisions":
                resources.update(_definition_children(child.get("definition", {}), path + "/"))
    if value.get("graphql") is not None:
        resources.add(prefix + "graphql")
        for identifier in value["graphql"].get("resolvers", {}):
            resources.add(prefix + "graphql/resolvers/" + identifier)
    return resources


def enforce_backend_transport(cfg: Any, url: str, backend: Any = None) -> None:
    """Call after policy/backend selection, before every HTTP transport attempt."""
    settings = cfg.security_governance
    if settings.mode != "deny" or not settings.backend_certificate_verification:
        return
    if urlsplit(url).scheme != "https" or (
        backend is not None and (not backend.verify_certificate_chain or not backend.verify_certificate_name)
    ):
        raise HTTPException(403, "Backend transport denied by local certificate governance")


def security_posture(cfg: Any, observed_paths: dict[str, float] | None = None) -> list[dict]:
    findings = governance_findings(cfg)
    observed_paths = observed_paths or {}
    for route in cfg.materialize_routes():
        protected = _route_declares_authentication(cfg, route)
        if cfg.allow_anonymous and not protected:
            findings.append(
                _finding(
                    "unauthenticated-endpoint",
                    "routes/" + route.name,
                    "Require a subscription or an authentication policy; review intentionally public endpoints.",
                )
            )
        if route.name not in observed_paths and route.path_prefix not in observed_paths:
            findings.append(
                _finding(
                    "unobserved-endpoint",
                    "routes/" + route.name,
                    "No request has been recorded in the retained observation window; review whether this endpoint is still needed.",
                )
            )
    return findings


def _route_declares_authentication(cfg, route):
    products = set(route.products) | ({route.product} if route.product else set())
    open_product = any(not cfg.products[name].require_subscription for name in products if name in cfg.products)
    subscription = cfg.subscription.required and not cfg.subscription.bypass and not open_product
    authz = route.authz
    authorizes_identity = bool(authz and (authz.required_scopes or authz.required_roles or authz.required_claims))
    return subscription or authorizes_identity or _unconditional_token_validation(cfg, route)


def _unconditional_token_validation(cfg, route):
    from app.policy import ValidateJwt, _effective_section_steps, parse_policies_xml

    xmls = (
        ([cfg.policies_xml] if cfg.policies_xml else [])
        + cfg.policies_xml_documents
        + route.policies_xml_documents
        + ([route.policies_xml] if route.policies_xml else [])
    )
    try:
        documents = [parse_policies_xml(xml, policy_fragments=cfg.policy_fragments, gateway_config=cfg) for xml in xmls]
    except HTTPException:
        return False
    return any(
        isinstance(node, ValidateJwt) and str(node.require_signed_tokens).lower() == "true"
        for _scope, node in _effective_section_steps(documents, "inbound")
    )


def build_security_governance_router(*, require_management_plane: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/apim/management/security/governance")
    async def read(request: Request) -> dict:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config
        return {"settings": cfg.security_governance.model_dump(), "findings": governance_findings(cfg)}

    @router.put("/apim/management/security/governance")
    async def update(body: SecurityGovernanceConfig, request: Request) -> dict:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        cfg.security_governance = body
        updated = require_management_plane().persist_or_apply_config(cfg)
        return {"settings": updated.security_governance.model_dump(), "findings": governance_findings(updated)}

    @router.get("/apim/management/security/posture")
    async def posture(request: Request) -> dict:
        require_tenant_access(request)
        sink = getattr(request.app.state, "security_audit_sink", None)
        observed = sink.observed_paths() if sink else {}
        return {
            "findings": security_posture(request.app.state.gateway_config, observed),
            "observation": "retained local requests, not proof of lifetime disuse",
        }

    return router
