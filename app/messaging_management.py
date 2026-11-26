"""Local logger authoring used by policy-based advanced logging."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import model_validator

from app.config import LoggerConfig
from app.management_body import ManagementBody
from app.management_service import ManagementService
from app.named_values import mask_secret_data
from app.security import require_tenant_access


class LoggerUpsert(ManagementBody, LoggerConfig):
    """The PUT body: a logger, with unknown fields rejected rather than dropped."""

    @model_validator(mode="before")
    @classmethod
    def _target_from_projection(cls, data: Any) -> Any:
        # A GET shows the stored resource_id (the logger's target) as
        # target_resource_id and puts the logger's own ARM ID in resource_id.
        # Accept that shape back, so GET, edit, PUT keeps the target.
        if isinstance(data, dict) and "target_resource_id" in data:
            data = dict(data)
            data["resource_id"] = data.pop("target_resource_id")
        return data


def build_logger_authoring_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:
    router = APIRouter()

    @router.put("/apim/management/loggers/{logger_id}")
    async def upsert(logger_id: str, body: LoggerUpsert, request: Request) -> dict:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        cfg.loggers[logger_id] = body
        updated = require_management_plane().persist_or_apply_config(cfg)
        return mask_secret_data({"id": logger_id, **updated.loggers[logger_id].model_dump(mode="json")}, updated)

    @router.delete("/apim/management/loggers/{logger_id}")
    async def delete(logger_id: str, request: Request) -> dict:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config.model_copy(deep=True)
        if logger_id not in cfg.loggers:
            raise HTTPException(404, "Logger not found")
        del cfg.loggers[logger_id]
        require_management_plane().persist_or_apply_config(cfg)
        return {"deleted": True}

    return router
