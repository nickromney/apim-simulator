"""Local equivalent of creating an independently editable API version."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import ApiConfig, ApiVersionSetConfig, GatewayConfig
from app.management_service import _ensure_initial_api_revision
from app.resource_projection import project_api

if TYPE_CHECKING:
    from app.management_service import ManagementService


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    api_version: str = Field(min_length=1, max_length=100, pattern=r"^[^/?#]+$")
    version_set_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    versioning_scheme: Literal["Path", "Segment", "Header", "Query"] | None = None
    version_header_name: str | None = None
    version_query_name: str | None = None
    products: list[str] | None = None


def _version_set(cfg: GatewayConfig, source: ApiConfig, body: VersionRequest) -> ApiVersionSetConfig:
    existing = cfg.api_version_sets.get(body.version_set_id)
    requested = "Segment" if body.versioning_scheme == "Path" else body.versioning_scheme
    if existing is not None:
        if requested is not None and requested != existing.versioning_scheme.value:
            raise HTTPException(status_code=409, detail="Version set already uses another versioning scheme")
        if body.version_header_name is not None and body.version_header_name != existing.version_header_name:
            raise HTTPException(status_code=409, detail="Version set already uses another version header")
        if body.version_query_name is not None and body.version_query_name != existing.version_query_name:
            raise HTTPException(status_code=409, detail="Version set already uses another version query parameter")
        return existing
    try:
        return ApiVersionSetConfig(
            display_name=source.name,
            versioning_scheme=requested or "Segment",
            version_header_name=body.version_header_name,
            version_query_name=body.version_query_name,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid version set: {exc}") from exc


def _check_conflicts(cfg: GatewayConfig, source_id: str, source: ApiConfig, body: VersionRequest) -> None:
    if body.version_id in cfg.apis:
        raise HTTPException(status_code=409, detail="API version identifier already exists")
    if source.api_version_set is not None and source.api_version_set != body.version_set_id:
        raise HTTPException(status_code=409, detail="Source API belongs to another version set")
    for api_id, api in cfg.apis.items():
        if api.api_version_set != body.version_set_id:
            continue
        if api.api_version == body.api_version:
            raise HTTPException(status_code=409, detail="API version already exists in this version set")
        if source.api_version_set is None and api_id != source_id and api.api_version is None:
            raise HTTPException(status_code=409, detail="Version set already contains an Original API")
    unknown = set(body.products or []) - set(cfg.products)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown products: {', '.join(sorted(unknown))}")


def create_version(cfg: GatewayConfig, source_id: str, body: VersionRequest) -> ApiConfig:
    """Mutate a staged config only; the management service validates and publishes it."""
    source = cfg.apis.get(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source API not found")
    _check_conflicts(cfg, source_id, source, body)
    version_set = _version_set(cfg, source, body)
    clone = source.model_copy(deep=True)
    clone.api_version_set = body.version_set_id
    clone.api_version = body.api_version
    clone.source_api_id = source_id
    clone.revision = "1"
    clone.revision_description = None
    clone.version_description = None
    clone.revisions = {}
    clone.releases = {}
    clone.is_current = True
    if body.products is not None:
        clone.products = list(body.products)
    for operation in clone.operations.values():
        operation.api_version_set = None
        operation.api_version = None
        if body.products is not None:
            operation.products = None
    _ensure_initial_api_revision(clone)
    if source.api_version_set is None:
        source.api_version_set = body.version_set_id
        source.api_version = None
        source.version_description = "Original"
        _ensure_initial_api_revision(source)
    cfg.api_version_sets[body.version_set_id] = version_set
    cfg.apis[body.version_id] = clone
    return clone


def build_version_workflow_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:
    from app.named_values import mask_secret_data
    from app.security import require_tenant_access

    router = APIRouter()

    @router.post("/apim/management/apis/{api_id}/versions", status_code=201)
    async def version_api(api_id: str, body: VersionRequest, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        manager = require_management_plane()
        manager.require_api_authoring_mode(cfg)
        create_version(cfg, api_id, body)
        updated = manager.persist_or_apply_config(cfg)
        return mask_secret_data(project_api(updated, body.version_id, updated.apis[body.version_id]), updated)

    return router
