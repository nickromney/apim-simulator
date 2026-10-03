"""Local signed operator and portal identities with explicit, scoped permissions.

HS256 uses an environment-only laboratory issuer key. This is a local identity
provider contract; issuer verification and administrator claims model the
local sign-in boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import unquote

import jwt
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Role = Literal["reader", "operator", "contributor", "content-editor"]
Permission = Literal["read", "write", "operate", "debug", "content-read", "content-write", "sensitive-read"]


class SignedIdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    issuer: str = "https://local-issuer.example.test"
    audience: str = "apim-management"
    signing_key_env: str = "APIM_CONTROL_PLANE_SIGNING_KEY"


class PortalIdentityConfig(SignedIdentityConfig):
    audience: str = "apim-portal"
    signing_key_env: str = "APIM_PORTAL_SIGNING_KEY"
    allow_legacy_user_header: bool = False


class ControlPlaneConfig(SignedIdentityConfig):
    allow_legacy_tenant_keys: bool = True
    require_mfa: bool = False
    require_compliant_device: bool = False
    administrator_roles: list[Role] = Field(default_factory=lambda: ["contributor", "operator"])
    workspace_apis: dict[str, list[str]] = Field(default_factory=dict)


class ScopedGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["reader", "operator", "contributor"]
    api_ids: list[str] = Field(default_factory=list)
    workspace_id: str | None = None

    @model_validator(mode="after")
    def scoped(self) -> ScopedGrant:
        if not self.api_ids and not self.workspace_id:
            raise ValueError("A grant needs API IDs or a workspace ID")
        return self


@dataclass(frozen=True)
class ManagementActor:
    subject: str
    roles: frozenset[str]
    grants: tuple[ScopedGrant, ...]
    authentication: str = "signed-jwt"

    def audit_record(self) -> dict[str, Any]:
        return {"subject": self.subject, "roles": sorted(self.roles), "authentication": self.authentication}


def decode_identity(request: Request, config: SignedIdentityConfig) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token or len(token) > 16384:
        raise HTTPException(status_code=401, detail="A signed bearer token is required")
    key = os.environ.get(config.signing_key_env, "")
    if len(key.encode()) < 32:
        raise HTTPException(status_code=503, detail="Local identity signing key is not configured")
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            issuer=config.issuer,
            audience=config.audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        if not isinstance(claims["sub"], str) or not claims["sub"].strip() or len(claims["sub"]) > 256:
            raise jwt.InvalidTokenError("Invalid subject")
        return claims
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired identity token") from None


def _actor(claims: dict[str, Any]) -> ManagementActor:
    roles = claims.get("roles", [])
    grants = claims.get("apim_grants", [])
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise HTTPException(status_code=403, detail="Invalid operator roles")
    if not isinstance(grants, list) or len(grants) > 100:
        raise HTTPException(status_code=403, detail="Invalid operator grants")
    try:
        parsed = tuple(ScopedGrant.model_validate(grant) for grant in grants)
    except ValidationError:
        raise HTTPException(status_code=403, detail="Invalid operator grants") from None
    return ManagementActor(claims["sub"], frozenset(roles), parsed)


def _require_administrator_claims(claims: dict[str, Any], actor: ManagementActor, config: ControlPlaneConfig) -> None:
    roles = actor.roles | {grant.role for grant in actor.grants}
    if not roles.intersection(config.administrator_roles):
        return
    amr = claims.get("amr", [])
    if config.require_mfa and (not isinstance(amr, list) or "mfa" not in amr):
        raise HTTPException(status_code=403, detail="Administrator MFA is required")
    if config.require_compliant_device and claims.get("device_compliant") is not True:
        raise HTTPException(status_code=403, detail="A compliant administrator device is required")


def request_permission(request: Request) -> Permission:
    path = request.url.path.lower()
    read = request.method in {"GET", "HEAD"}
    if path == "/apim/portal/editor" or path.startswith("/apim/portal/editor/"):
        return "content-read" if read else "content-write"
    if path.startswith("/apim/trace/") or _is_debug_resource(path):
        return "debug"
    if _is_sensitive_resource(path):
        return "sensitive-read" if read else "write"
    if path == "/apim/reload":
        return "operate"
    return "read" if read else "write"


def _management_parts(path: str) -> list[str]:
    prefix = "/apim/management/"
    return path.removeprefix(prefix).split("/") if path.startswith(prefix) else []


def _is_debug_resource(path: str) -> bool:
    parts = _management_parts(path)
    if not parts:
        return False
    return parts[0] in {"traces", "replay"} or (
        len(parts) == 3 and parts[0] == "gateways" and parts[2] in {"listtrace", "listdebugcredentials"}
    )


def _is_sensitive_resource(path: str) -> bool:
    parts = _management_parts(path)
    return bool(parts and parts[0] in {"subscriptions", "tenant-access", "summary"})


def request_api_id(request: Request) -> str | None:
    parts = [unquote(part) for part in request.url.path.split("/") if part]
    if parts[:3] == ["apim", "management", "apis"] and len(parts) > 3:
        return parts[3].split(";rev=", 1)[0]
    if parts[:4] == ["apim", "management", "policies", "api"] and len(parts) > 4:
        return parts[4].split(";rev=", 1)[0]
    if parts[:4] == ["apim", "management", "policies", "operation"] and len(parts) > 4:
        return parts[4].split(";rev=", 1)[0]
    return None


_ROLE_PERMISSIONS = {
    "reader": {"read"},
    "operator": {"read", "operate", "debug"},
    "contributor": {"read", "write", "operate", "debug", "content-read", "content-write", "sensitive-read"},
    "content-editor": {"content-read", "content-write"},
}


def _grant_matches(grant: ScopedGrant, api_id: str, config: ControlPlaneConfig) -> bool:
    workspace_ids = config.workspace_apis.get(grant.workspace_id, []) if grant.workspace_id else None
    # Both constraints intersect when present; an API list cannot expand a workspace.
    return (not grant.api_ids or api_id in grant.api_ids) and (workspace_ids is None or api_id in workspace_ids)


def authorize_operator(
    request: Request,
    config: ControlPlaneConfig,
    *,
    permission: Permission | None = None,
    api_id: str | None = None,
) -> None:
    claims = decode_identity(request, config)
    actor = _actor(claims)
    _require_administrator_claims(claims, actor, config)
    request.state.management_actor = actor.audit_record()
    required = permission or request_permission(request)
    target = api_id or request_api_id(request)
    global_allowed = any(required in _ROLE_PERMISSIONS.get(role, set()) for role in actor.roles)
    scoped_allowed = target and any(
        _grant_matches(grant, target, config) and required in _ROLE_PERMISSIONS[grant.role] for grant in actor.grants
    )
    if not global_allowed and not scoped_allowed:
        raise HTTPException(status_code=403, detail="Operator does not have permission for this resource")
