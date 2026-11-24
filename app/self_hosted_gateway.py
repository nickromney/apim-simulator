"""Configuration-pull self-hosted gateway mode.

The managed instance remains the configuration authority.  A self-hosted
instance makes outbound snapshot requests and executes API traffic locally;
it never forwards data-plane requests through the managed instance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import MANAGED_GATEWAY_ID, GatewayConfig
from app.management_service import ManagementService
from app.security import require_tenant_access

logger = logging.getLogger("apim-simulator.self-hosted-gateway")

ENV_ENABLED = "APIM_SELF_HOSTED_GATEWAY"
ENV_ID = "APIM_SELF_HOSTED_GATEWAY_ID"
ENV_CONFIG_URL = "APIM_SELF_HOSTED_GATEWAY_CONFIG_URL"
ENV_TOKEN = "APIM_SELF_HOSTED_GATEWAY_TOKEN"
ENV_POLL_INTERVAL = "APIM_SELF_HOSTED_GATEWAY_POLL_INTERVAL"
STATUS_PATH = "/apim/self-hosted-gateway/status"
TOKEN_HEADER = "x-apim-gateway-token"
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SelfHostedGatewaySettings:
    gateway_id: str
    config_url: str
    token: str
    poll_interval: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SelfHostedGatewaySettings | None:
        env = os.environ if env is None else env
        if env.get(ENV_ENABLED, "").strip().lower() not in _TRUTHY:
            return None
        gateway_id = env.get(ENV_ID, "").strip()
        config_url = env.get(ENV_CONFIG_URL, "").strip()
        token = env.get(ENV_TOKEN, "").strip()
        if not gateway_id or not config_url or not token:
            raise ValueError(f"{ENV_ID}, {ENV_CONFIG_URL}, and {ENV_TOKEN} are required when {ENV_ENABLED} is on")
        parsed = httpx.URL(config_url)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError(f"{ENV_CONFIG_URL} must be an absolute http(s) URL; got {config_url!r}")
        interval = float(env.get(ENV_POLL_INTERVAL, "30"))
        if interval <= 0:
            raise ValueError(f"{ENV_POLL_INTERVAL} must be greater than zero")
        return cls(gateway_id=gateway_id, config_url=config_url, token=token, poll_interval=interval)


@dataclass
class SelfHostedSyncState:
    last_sync_at: str | None = None
    etag: str | None = None
    last_error: str | None = None
    successful_syncs: int = 0

    def payload(self, gateway_id: str) -> dict[str, Any]:
        return {
            "mode": "self-hosted-gateway",
            "gateway_id": gateway_id,
            "is_managed": False,
            "last_sync_at": self.last_sync_at,
            "etag": self.etag,
            "last_error": self.last_error,
            "successful_syncs": self.successful_syncs,
        }


def _gateway_token_valid(cfg: GatewayConfig, gateway_id: str, supplied: str) -> bool:
    import secrets

    gateway = cfg.gateways.get(gateway_id)
    if gateway is None or gateway.access_token is None:
        return False
    if gateway.access_token_expires_at:
        try:
            expires_at = datetime.fromisoformat(gateway.access_token_expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                raise HTTPException(status_code=401, detail="Gateway access token expired")
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Invalid gateway access token configuration") from exc
    return secrets.compare_digest(gateway.access_token.get_secret_value(), supplied)


def _supplied_gateway_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get(TOKEN_HEADER, "").strip()


def _effective_api_gateways(cfg: GatewayConfig, api: Any) -> list[str]:
    if api.gateways is not None:
        return list(api.gateways)
    if api.workspace is None:
        return [MANAGED_GATEWAY_ID]
    workspace = cfg.workspaces.get(api.workspace)
    return list(workspace.gateways) if workspace is not None else [MANAGED_GATEWAY_ID]


# Management-plane settings stay on the managed instance. A self-hosted gateway
# gets what it needs to serve traffic (APIs, products, subscriptions, named
# values, backends, certificates, identity providers), never the credentials
# that administer the managed instance.
_MANAGEMENT_ONLY_FIELDS = (
    "admin_token",
    "tenant_access",
    "control_plane",
    "secret_storage",
    "portal",
    "portal_content",
    "api_center",
    "security_governance",
    "security_observability",
)


def _snapshot_payload(cfg: GatewayConfig, gateway_id: str) -> dict[str, Any]:
    selected_api_ids = {api_id for api_id, api in cfg.apis.items() if gateway_id in _effective_api_gateways(cfg, api)}
    payload = cfg.model_dump(mode="json")
    for field in _MANAGEMENT_ONLY_FIELDS:
        payload.pop(field, None)
    payload["apis"] = {api_id: payload["apis"][api_id] for api_id in selected_api_ids}
    payload["routes"] = []
    payload["gateways"] = {gateway_id: payload["gateways"][gateway_id]} if gateway_id in payload["gateways"] else {}
    for gateway in payload["gateways"].values():
        gateway.pop("access_token", None)
        gateway.pop("access_token_expires_at", None)
    workspace_ids = {api["workspace"] for api in payload["apis"].values() if api.get("workspace") is not None}
    payload["workspaces"] = {
        workspace_id: workspace
        for workspace_id, workspace in payload.get("workspaces", {}).items()
        if workspace_id in workspace_ids
    }
    workspace_gateway_ids = {
        gateway
        for workspace in payload["workspaces"].values()
        for gateway in workspace.get("gateways", [])
        if gateway != MANAGED_GATEWAY_ID
    }
    payload["workspace_gateways"] = {
        gateway_id: gateway
        for gateway_id, gateway in payload.get("workspace_gateways", {}).items()
        if gateway_id in workspace_gateway_ids
    }
    return payload


def _snapshot_etag(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f'"{hashlib.sha256(encoded).hexdigest()}"'


def build_self_hosted_router() -> APIRouter:
    router = APIRouter()

    @router.get("/apim/management/gateways")
    async def list_gateways(request: Request) -> list[dict[str, Any]]:
        require_tenant_access(request, global_only=True)
        cfg: GatewayConfig = request.app.state.gateway_config
        return [
            {
                "id": gateway_id,
                "description": gateway.description,
                "location": gateway.location.model_dump(mode="json") if gateway.location else None,
                "last_heartbeat": getattr(request.app.state, "gateway_heartbeats", {}).get(gateway_id),
            }
            for gateway_id, gateway in cfg.gateways.items()
        ]

    @router.get("/apim/management/gateways/{gateway_id}/configuration")
    async def gateway_configuration(gateway_id: str, request: Request) -> Response:
        cfg: GatewayConfig = request.app.state.gateway_config
        supplied = _supplied_gateway_token(request)
        if not supplied:
            raise HTTPException(status_code=401, detail="Gateway access token required")
        if not _gateway_token_valid(cfg, gateway_id, supplied):
            raise HTTPException(status_code=403, detail="Invalid gateway access token")
        # Each authenticated pull doubles as the gateway's heartbeat.
        request.app.state.gateway_heartbeats[gateway_id] = datetime.now(UTC).isoformat()
        payload = _snapshot_payload(cfg, gateway_id)
        etag = _snapshot_etag(payload)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=json.dumps(payload, separators=(",", ":")),
            media_type="application/json",
            headers={"ETag": etag, "Cache-Control": "no-store"},
        )

    @router.get(STATUS_PATH)
    async def self_hosted_status(request: Request) -> dict[str, Any]:
        settings: SelfHostedGatewaySettings | None = getattr(request.app.state, "self_hosted_gateway", None)
        if settings is None:
            raise HTTPException(status_code=404, detail="Not found")
        state: SelfHostedSyncState = request.app.state.self_hosted_sync
        return state.payload(settings.gateway_id)

    return router


async def sync_once(
    *,
    app: Any,
    settings: SelfHostedGatewaySettings,
    manager: ManagementService,
) -> bool:
    """Pull and apply one snapshot, preserving the current config on failure."""
    state: SelfHostedSyncState = app.state.self_hosted_sync
    headers = {"Authorization": f"Bearer {settings.token}", TOKEN_HEADER: settings.token}
    if state.etag:
        headers["If-None-Match"] = state.etag
    try:
        response = await app.state.http_client.get(settings.config_url, headers=headers)
        if response.status_code == 304:
            state.last_sync_at = datetime.now(UTC).isoformat()
            state.last_error = None
            return True
        response.raise_for_status()
        snapshot = GatewayConfig.model_validate(response.json())
        manager.apply_runtime_config(snapshot)
        state.etag = response.headers.get("etag") or _snapshot_etag(_snapshot_payload(snapshot, settings.gateway_id))
        state.last_sync_at = datetime.now(UTC).isoformat()
        state.last_error = None
        state.successful_syncs += 1
        return True
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        state.last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("self-hosted gateway config sync failed | gateway=%s | error=%s", settings.gateway_id, exc)
        return False


async def poll_config(
    *,
    app: Any,
    settings: SelfHostedGatewaySettings,
    manager: ManagementService,
) -> None:
    while True:
        await asyncio.sleep(settings.poll_interval)
        await sync_once(app=app, settings=settings, manager=manager)
