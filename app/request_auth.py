"""Shared request authentication and authorisation helpers.

Used by both the gateway core (app.main) and the management surface
(app.management_api). All helpers read configuration from
``request.app.state`` so they carry no construction-time state.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.config import GatewayConfig, Subscription


def _extract_scopes(claims: dict) -> set[str]:
    scopes: set[str] = set()
    raw = claims.get("scope") or claims.get("scp")
    if isinstance(raw, str):
        scopes.update(s for s in raw.split() if s)
    if isinstance(raw, list):
        scopes.update(str(s) for s in raw if s)
    return scopes


def _extract_roles(claims: dict) -> set[str]:
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

    # Keycloak client roles typically live under resource_access.{client}.roles.
    resource_access = claims.get("resource_access")
    if isinstance(resource_access, dict):
        for entry in resource_access.values():
            if not isinstance(entry, dict):
                continue
            cr = entry.get("roles")
            if isinstance(cr, list):
                roles.update(str(r) for r in cr if r)
    return roles


def _require_admin(request: Request) -> None:
    cfg: GatewayConfig = request.app.state.gateway_config
    if not cfg.admin_token:
        raise HTTPException(status_code=404, detail="Not found")
    provided = request.headers.get("x-apim-admin-token", "")
    if provided != cfg.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


def _require_tenant_access(request: Request) -> None:
    cfg: GatewayConfig = request.app.state.gateway_config
    if not cfg.tenant_access.enabled:
        raise HTTPException(status_code=404, detail="Not found")

    # Allow admin token as a super-user escape hatch for local dev.
    admin = request.headers.get("x-apim-admin-token", "")
    if cfg.admin_token and admin == cfg.admin_token:
        return

    provided = request.headers.get("x-apim-tenant-key", "")
    if not provided:
        raise HTTPException(status_code=403, detail="Forbidden")

    if provided == (cfg.tenant_access.primary_key or ""):
        return
    if provided == (cfg.tenant_access.secondary_key or ""):
        return
    raise HTTPException(status_code=403, detail="Forbidden")


def _find_subscription_entry(cfg: GatewayConfig, subscription_id: str) -> tuple[str, Subscription] | None:
    for config_key, sub in cfg.subscription.subscriptions.items():
        if sub.id == subscription_id:
            return config_key, sub
    return None


def _find_subscription_by_id(cfg: GatewayConfig, subscription_id: str) -> Subscription | None:
    entry = _find_subscription_entry(cfg, subscription_id)
    return entry[1] if entry is not None else None
