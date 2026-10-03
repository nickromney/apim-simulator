"""API-scoped, time-limited local debug credentials, using APIM header names."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from app.management_service import ManagementService


class DebugRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    apiId: str
    purposes: list[str] = Field(default_factory=lambda: ["tracing"])
    credentialsExpireAfter: str = "PT1H"

    @field_validator("purposes")
    @classmethod
    def tracing_only(cls, value: list[str]) -> list[str]:
        if value != ["tracing"]:
            raise ValueError("Only tracing credentials are supported")
        return value

    @field_validator("credentialsExpireAfter")
    @classmethod
    def bounded_duration(cls, value: str) -> str:
        duration_seconds(value)
        return value


class TraceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    traceId: str


def duration_seconds(value: str) -> int:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if match is None:
        raise ValueError("Expiry must be an ISO 8601 duration between 1 second and 1 hour")
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    duration = hours * 3600 + minutes * 60 + seconds
    if not 1 <= duration <= 3600:
        raise ValueError("Expiry must be between 1 second and 1 hour")
    return duration


def authorize_debug(request: Request, api_id: str | None) -> bool:
    token = request.headers.get("Apim-Debug-Authorization")
    if not token:
        return False
    credential = request.app.state.debug_credentials.get(token)
    if credential is None:
        request.state.debug_response_header = ("Apim-Debug-Authorization-Invalid", "Unknown debug credential")
        return False
    if credential["expires_at"] <= time.time():
        request.state.debug_response_header = ("Apim-Debug-Authorization-Expired", credential["expires_at_iso"])
        return False
    if credential["api_id"] != api_id:
        request.state.debug_response_header = ("Apim-Debug-Authorization-WrongAPI", "Credential belongs to another API")
        return False
    request.state.debug_authorized = True
    return True


def _mint_credentials(request: Request, api_id: str, duration: int) -> dict[str, str]:
    store = request.app.state.debug_credentials
    now = time.time()
    for key in list(store):
        if store[key]["expires_at"] <= now:
            del store[key]
    if len(store) >= 1000:
        raise HTTPException(status_code=429, detail="Too many active debug credentials")
    expiry = now + duration
    expires_at = datetime.fromtimestamp(expiry, UTC).isoformat()
    token = secrets.token_urlsafe(32)
    store[token] = {"api_id": api_id, "expires_at": expiry, "expires_at_iso": expires_at}
    return {"token": token, "expiresAt": expires_at}


def build_debug_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:
    from app.security import require_tenant_access

    router = APIRouter()

    @router.post("/apim/management/gateways/{gateway_id}/listDebugCredentials")
    async def credentials(gateway_id: str, body: DebugRequest, request: Request) -> dict[str, str]:
        api_id = body.apiId.rsplit("/apis/", 1)[-1].split(";rev=", 1)[0]
        require_tenant_access(request, permission="debug", api_id=api_id)
        if gateway_id != "managed":
            raise HTTPException(status_code=404, detail="Gateway not found")
        if api_id not in request.app.state.gateway_config.apis:
            raise HTTPException(status_code=404, detail="API not found")
        return _mint_credentials(request, api_id, duration_seconds(body.credentialsExpireAfter))

    @router.post("/apim/management/gateways/{gateway_id}/listTrace")
    async def trace(gateway_id: str, body: TraceRequest, request: Request) -> dict[str, Any]:
        entry = request.app.state.trace_store.get(body.traceId)
        api_id = entry.get("api_id") if entry is not None else None
        require_tenant_access(request, permission="debug", api_id=api_id)
        if gateway_id != "managed":
            raise HTTPException(status_code=404, detail="Gateway not found")
        if entry is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        return entry

    return router
