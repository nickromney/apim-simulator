"""A signed local workload identity with explicit audience grants."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import jwt
from pydantic import BaseModel, Field


class WorkloadIdentityConfig(BaseModel):
    mode: Literal["signed", "demo"] = "signed"
    issuer: str = "https://apim.local/workload-identity"
    private_key_file: str | None = None
    public_key_file: str | None = None
    audience_grants: dict[str, list[str]] = Field(default_factory=dict)
    token_lifetime_seconds: int = Field(default=300, ge=1, le=3600)


def issue_workload_token(settings: WorkloadIdentityConfig, resource: str, client_id: str | None = None) -> str:
    identity = client_id or "system-assigned"
    if resource not in settings.audience_grants.get(identity, []):
        raise ValueError("Workload identity is not granted access to this audience")
    filename = settings.private_key_file or os.environ.get("APIM_WORKLOAD_IDENTITY_PRIVATE_KEY_FILE")
    if not filename:
        raise ValueError("Signed workload identity private key is not configured")
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {
            "iss": settings.issuer,
            "aud": resource,
            "sub": identity,
            "iat": now,
            "nbf": now,
            "exp": now + settings.token_lifetime_seconds,
        },
        Path(filename).read_bytes(),
        algorithm="RS256",
    )


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
