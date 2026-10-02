"""Local API Center inventory and continuous one-way APIM synchronization."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.config import GatewayConfig
    from app.management_service import ManagementService


class LocalApiCenter(BaseModel):
    name: str
    assets: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ApiCenterState(BaseModel):
    centers: dict[str, LocalApiCenter] = Field(default_factory=dict)
    linked_center_id: str | None = None
    include_definitions: bool = False
    lifecycle: str = "production"


class CenterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    center_id: str
    include_definitions: bool = False
    lifecycle: str = Field(default="production", min_length=1, max_length=100)


def synchronize_api_center(cfg: GatewayConfig) -> None:
    from app.named_values import mask_secret_data
    from app.openapi_export import build_openapi

    state = cfg.api_center
    if state.linked_center_id is None:
        return
    center = state.centers.get(state.linked_center_id)
    if center is None:
        raise ValueError("Linked API Center does not exist")
    assets = {}
    for api_id, api in cfg.apis.items():
        asset = {
            "id": api_id,
            "title": api.name or api_id,
            "version": api.api_version,
            "lifecycle": state.lifecycle,
            "source": cfg.service.name,
            "environment": {"kind": "Azure API Management", "server_type": "local-simulator"},
            "deployment": {"runtime_path": "/" + api.path.strip("/")},
        }
        if state.include_definitions:
            asset["definition"] = mask_secret_data(
                build_openapi(api, version_set=cfg.api_version_sets.get(api.api_version_set)), cfg
            )
        assets[api_id] = asset
    center.assets = assets


def build_api_center_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:  # noqa: C901 - route registration
    from app.security import require_tenant_access

    router = APIRouter()

    @router.put("/apim/management/api-centers/{center_id}")
    async def put_center(center_id: str, body: CenterRequest, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        current = cfg.api_center.centers.get(center_id)
        cfg.api_center.centers[center_id] = LocalApiCenter(name=body.name, assets=current.assets if current else {})
        updated = require_management_plane().persist_or_apply_config(cfg)
        return {"id": center_id, **updated.api_center.centers[center_id].model_dump(mode="json")}

    @router.get("/apim/management/api-centers")
    async def list_centers(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        return {
            "items": [
                {"id": key, "name": center.name}
                for key, center in request.app.state.gateway_config.api_center.centers.items()
            ]
        }

    @router.put("/apim/management/api-center/link")
    async def link_center(body: LinkRequest, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        if body.center_id not in cfg.api_center.centers:
            raise HTTPException(status_code=404, detail="API Center not found")
        if cfg.api_center.linked_center_id not in {None, body.center_id}:
            raise HTTPException(status_code=409, detail="Unlink the current API Center before linking another")
        cfg.api_center.linked_center_id = body.center_id
        cfg.api_center.include_definitions = body.include_definitions
        cfg.api_center.lifecycle = body.lifecycle
        require_management_plane().persist_or_apply_config(cfg)
        return {"center_id": body.center_id, "state": "Linked and syncing"}

    @router.get("/apim/management/api-center/link")
    async def get_link(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        state = request.app.state.gateway_config.api_center
        return {
            "center_id": state.linked_center_id,
            "state": "Linked and syncing" if state.linked_center_id else "Not linked",
            "include_definitions": state.include_definitions,
        }

    @router.get("/apim/management/api-centers/{center_id}/apis")
    async def inventory(center_id: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        center = request.app.state.gateway_config.api_center.centers.get(center_id)
        if center is None:
            raise HTTPException(status_code=404, detail="API Center not found")
        return {"items": list(center.assets.values())}

    @router.delete("/apim/management/api-center/link")
    async def unlink(request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        center_id = cfg.api_center.linked_center_id
        if center_id is not None:
            cfg.api_center.centers[center_id].assets.clear()
        cfg.api_center.linked_center_id = None
        require_management_plane().persist_or_apply_config(cfg)
        return {"state": "Not linked", "center_id": center_id}

    return router
