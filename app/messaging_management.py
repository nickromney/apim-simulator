"""Local logger authoring used by policy-based advanced logging."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request

from app.config import LoggerConfig
from app.management_service import ManagementService
from app.named_values import mask_secret_data
from app.security import require_tenant_access


def build_logger_authoring_router(*, require_management_plane: Callable[[], ManagementService]) -> APIRouter:
    router = APIRouter()

    @router.put("/apim/management/loggers/{logger_id}")
    async def upsert(logger_id: str, body: LoggerConfig, request: Request) -> dict:
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
