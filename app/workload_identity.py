"""A signed local workload identity with explicit audience grants.

The issuer publishes its verification keys as a JWKS so a backend (for example
the sibling aifoundry-simulator) can trust the gateway's managed identity the
way an Azure resource trusts Microsoft Entra: by issuer and discovered keys,
not by a shared secret.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from fastapi import APIRouter, HTTPException, Request
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel, Field

PRIVATE_KEY_ENV = "APIM_WORKLOAD_IDENTITY_PRIVATE_KEY_FILE"
DISCOVERY_PATH = "/.well-known/workload-identity/openid-configuration"
JWKS_PATH = "/.well-known/workload-identity/jwks.json"


class WorkloadIdentityConfig(BaseModel):
    mode: Literal["signed", "demo"] = "signed"
    issuer: str = "https://apim.local/workload-identity"
    private_key_file: str | None = None
    # Optional extra verification key published alongside the signing key,
    # e.g. the previous key while tokens it signed are still in flight.
    public_key_file: str | None = None
    audience_grants: dict[str, list[str]] = Field(default_factory=dict)
    token_lifetime_seconds: int = Field(default=300, ge=1, le=3600)
    # Placeholder tenant for the Entra-shaped `tid` claim; not a real tenant.
    tenant_id: str = "00000000-0000-0000-0000-000000000000"


def private_key_path(settings: WorkloadIdentityConfig) -> str | None:
    return settings.private_key_file or os.environ.get(PRIVATE_KEY_ENV) or None


def _load_private_key(filename: str) -> RSAPrivateKey:
    try:
        key = load_pem_private_key(Path(filename).read_bytes(), password=None)
    except TypeError as exc:  # encrypted PEM: no passphrase support
        raise ValueError("Signed workload identity private key must not be encrypted") from exc
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("Signed workload identity key must be an RSA private key")
    return key


def _public_jwk(public_key: RSAPublicKey) -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(public_key, as_dict=True)
    # RFC 7638 thumbprint: SHA-256 over the required members in lexical order.
    canonical = json.dumps({"e": jwk["e"], "kty": "RSA", "n": jwk["n"]}, separators=(",", ":"), sort_keys=True)
    kid = base64.urlsafe_b64encode(hashlib.sha256(canonical.encode("utf-8")).digest()).rstrip(b"=").decode("ascii")
    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": jwk["n"], "e": jwk["e"]}


def issue_workload_token(settings: WorkloadIdentityConfig, resource: str, client_id: str | None = None) -> str:
    identity = client_id or "system-assigned"
    if resource not in settings.audience_grants.get(identity, []):
        raise ValueError("Workload identity is not granted access to this audience")
    filename = private_key_path(settings)
    if not filename:
        raise ValueError("Signed workload identity private key is not configured")
    private_key = _load_private_key(filename)
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {
            "iss": settings.issuer,
            "aud": resource,
            "sub": identity,
            # Entra-shaped identity claims, so a backend can authorize on
            # `oid` as it would for a real managed identity token.
            "oid": identity,
            "appid": identity,
            "tid": settings.tenant_id,
            "iat": now,
            "nbf": now,
            "exp": now + settings.token_lifetime_seconds,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": _public_jwk(private_key.public_key())["kid"]},
    )


def published_jwks(settings: WorkloadIdentityConfig) -> dict[str, Any] | None:
    """Return the issuer's JWKS, or None when there is no signing key to publish.

    Raises OSError/ValueError when a configured key file cannot be read.
    """
    filename = private_key_path(settings)
    if settings.mode != "signed" or not filename:
        return None
    keys = [_public_jwk(_load_private_key(filename).public_key())]
    if settings.public_key_file:
        extra = load_pem_public_key(Path(settings.public_key_file).read_bytes())
        if not isinstance(extra, RSAPublicKey):
            raise ValueError("Workload identity public key must be an RSA public key")
        jwk = _public_jwk(extra)
        if jwk["kid"] != keys[0]["kid"]:
            keys.append(jwk)
    return {"keys": keys}


def build_workload_identity_router() -> APIRouter:
    """Publish the issuer's discovery document and verification keys.

    Only key material is published; tokens are still minted inside the
    gateway. Both endpoints return 404 in demo mode or without a signing key.
    """
    router = APIRouter()

    def _jwks_or_404(request: Request) -> dict[str, Any]:
        try:
            jwks = published_jwks(request.app.state.gateway_config.workload_identity)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="Workload identity keys could not be loaded") from exc
        if jwks is None:
            raise HTTPException(status_code=404, detail="Signed workload identity is not configured")
        return jwks

    @router.get(DISCOVERY_PATH)
    async def workload_identity_discovery(request: Request) -> dict[str, Any]:
        _jwks_or_404(request)
        settings: WorkloadIdentityConfig = request.app.state.gateway_config.workload_identity
        return {
            "issuer": settings.issuer,
            "jwks_uri": str(request.base_url).rstrip("/") + JWKS_PATH,
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": [],
            "claims_supported": ["iss", "aud", "sub", "oid", "appid", "tid", "iat", "nbf", "exp"],
        }

    @router.get(JWKS_PATH)
    async def workload_identity_jwks(request: Request) -> dict[str, Any]:
        return _jwks_or_404(request)

    return router


def verify_workload_token(
    token: str, *, public_key_file: str, issuer: str, audience: str, allowed_identities: list[str]
) -> dict:
    claims = jwt.decode(
        token,
        Path(public_key_file).read_bytes(),
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
        options={"require": ["iss", "aud", "sub", "exp", "iat", "nbf"]},
    )
    if claims["sub"] not in allowed_identities:
        raise jwt.InvalidTokenError("Workload identity is not granted access")
    return claims
