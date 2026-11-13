"""Portable authored API contract and policies, with target-local configuration excluded."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import GatewayConfig
from app.named_values import mask_secret_data
from app.openapi_export import build_openapi

if TYPE_CHECKING:
    from app.management_service import ManagementService


class ApiArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int
    openapi: dict[str, Any]
    api_policy: str | None
    operation_policies: dict[str, str]


class ApiArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact: ApiArtifact
    upstream_base_url: str | None = None


def _validate_portable_operations(api: Any) -> None:
    for operation_id, operation in api.operations.items():
        if operation.authz is not None:
            raise HTTPException(
                status_code=400, detail=f"Operation {operation_id} has authorization that artifacts cannot represent"
            )
        path, separator, query = operation.url_template.partition("?")
        path_names = {name.casefold() for name in re.findall(r"\{([^{}]+)\}", path)}
        params = {
            item.name
            for item in operation.template_parameters
            if item.required and item.name.casefold() not in path_names
        }
        if operation.request is not None:
            params.update(item.name for item in operation.request.query_parameters if item.required)
        expected = (
            {f"{name}={{{name}}}" for name in params}
            if api.translate_required_query_parameters == "template"
            else set()
        )
        parts = query.split("&") if separator else []
        if set(parts) != expected or len(parts) != len(set(parts)):
            raise HTTPException(
                status_code=400,
                detail=f"Operation {operation_id} has query constraints that artifacts cannot represent",
            )


def export_artifact(cfg: GatewayConfig, api_id: str) -> dict[str, Any]:
    api = cfg.apis.get(api_id)
    if api is None:
        raise HTTPException(status_code=404, detail="API not found")
    if api.graphql is not None:
        raise HTTPException(status_code=400, detail="GraphQL APIs are not supported by artifacts")
    _validate_portable_operations(api)
    try:
        artifact = {
            "schema_version": 1,
            "openapi": build_openapi(api),
            "api_policy": api.policies_xml,
            "operation_policies": {
                key: op.policies_xml for key, op in api.operations.items() if op.policies_xml is not None
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    artifact["openapi"]["x-apim-simulator-query-mode"] = api.translate_required_query_parameters
    exported_ids = [
        operation["operationId"] for path in artifact["openapi"]["paths"].values() for operation in path.values()
    ]
    if exported_ids != list(api.operations):
        raise HTTPException(status_code=400, detail="Artifact cannot preserve operation order for this API")
    if mask_secret_data(artifact, cfg) != artifact:
        raise HTTPException(
            status_code=400, detail="Artifact contains a known secret; replace it with a named value reference"
        )
    return artifact


def artifact_etag(cfg: GatewayConfig, api_id: str) -> str:
    api = cfg.apis.get(api_id)
    value = api.model_dump(mode="json") if api else None
    raw = json.dumps([api_id, list(api.operations) if api else None, value], sort_keys=True, separators=(",", ":"))
    return f'"{hashlib.sha256(raw.encode()).hexdigest()}"'


def _title(document: dict[str, Any]) -> str:
    info = document.get("info")
    if not isinstance(info, dict) or not isinstance(info.get("title"), str) or not info["title"].strip():
        raise HTTPException(status_code=400, detail="OpenAPI info.title must be a nonempty string")
    return info["title"]


def _path(document: dict[str, Any]) -> str:
    servers = document.get("servers")
    if not isinstance(servers, list) or len(servers) != 1 or not isinstance(servers[0], dict):
        raise HTTPException(status_code=400, detail="Artifact requires one relative server URL")
    url = servers[0].get("url")
    if not isinstance(url, str) or not url.startswith("/") or url.startswith("//") or any(c in url for c in "?#{}"):
        raise HTTPException(status_code=400, detail="Artifact server URL must be an absolute relative path")
    return url.strip("/")


def _validate_operation_ids(doc: dict[str, Any], policies: dict[str, str]) -> None:
    paths = doc.get("paths")
    if not isinstance(paths, dict) or any(not isinstance(path, dict) for path in paths.values()):
        raise HTTPException(status_code=400, detail="OpenAPI paths and path items must be objects")
    ids = [
        op.get("operationId")
        for path in paths.values()
        if isinstance(path, dict)
        for method, op in path.items()
        if isinstance(method, str)
        and method.lower() in {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
        and isinstance(op, dict)
    ]
    if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="Every operation requires a unique operationId")
    if set(policies) - set(ids):
        raise HTTPException(status_code=400, detail="Operation policy references an unknown operationId")


def _validate_upstream(url: str | None) -> None:
    if url is None:
        return
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.port != 0
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and not any(char.isspace() for char in url)
            and "?" not in url
            and "#" not in url
        )
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(
            status_code=400,
            detail="upstream_base_url must be an absolute HTTP(S) URL without credentials, query, or fragment",
        )


def _query_mode(doc: dict[str, Any]) -> str:
    mode = doc.get("x-apim-simulator-query-mode", "template")
    if not isinstance(mode, str) or mode not in {"template", "query"}:
        raise HTTPException(status_code=400, detail="Invalid artifact query mode")
    return mode


def stage_artifact(  # noqa: C901 - shared staging preserves existing API artifact validation
    manager: ManagementService,
    cfg: GatewayConfig,
    api_id: str,
    body: ApiArtifactRequest,
    *,
    defer_validation: bool = False,
    backend: str | None = None,
) -> tuple[GatewayConfig, dict[str, Any]]:
    artifact = body.artifact.model_dump()
    if artifact["schema_version"] != 1:
        raise HTTPException(status_code=400, detail="Unsupported artifact schema version")
    if mask_secret_data(artifact, cfg) != artifact:
        raise HTTPException(status_code=400, detail="Artifact contains a known secret")
    existing = cfg.apis.get(api_id)
    if existing is not None and existing.graphql is not None:
        raise HTTPException(status_code=400, detail="GraphQL APIs are not supported by artifacts")
    _validate_upstream(body.upstream_base_url)
    if existing is None and not body.upstream_base_url and not backend:
        raise HTTPException(status_code=400, detail="upstream_base_url is required for a new API")
    doc = artifact["openapi"]
    _validate_operation_ids(doc, artifact["operation_policies"])
    query_mode = _query_mode(doc)
    api_path = _path(doc)
    params = {
        "name": _title(doc),
        "path": api_path,
        "content_format": "openapi+json",
        "content_value": json.dumps(doc),
        "upstream_base_url": body.upstream_base_url
        if body.upstream_base_url is not None
        else (existing.upstream_base_url if existing else None),
        "upstream_path_prefix": existing.upstream_path_prefix if existing else "",
        "protocols": None,
        "backend": None,
        "products": None,
        "api_version_set": None,
        "api_version": None,
        "subscription_header_names": None,
        "subscription_query_param_names": None,
        "policies_xml": None,
        "translate_required_query_parameters": query_mode,
    }
    staged, _ = manager.import_api(cfg.model_copy(deep=True), api_id, SimpleNamespace(**params), stage=True)
    api = staged.apis[api_id]
    api.path = api_path
    if backend is not None:
        api.backend = backend
    if body.upstream_base_url is not None:
        api.backend = None
    api.policies_xml = artifact["api_policy"]
    if not defer_validation:
        manager.validate_policy_xml(staged, api.policies_xml)
    for op_id, operation in api.operations.items():
        operation.policies_xml = artifact["operation_policies"].get(op_id)
        if not defer_validation:
            manager.validate_policy_xml(staged, operation.policies_xml)
    if defer_validation:
        return staged, artifact
    try:
        from app.management_service import _validate_open_product_associations
        from app.security_governance import validate_governance_mutation

        validate_governance_mutation(manager.app.state.gateway_config, staged)
        _validate_open_product_associations(staged)
        manager._prepare_config(staged.model_copy(deep=True))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid config update: {exc}") from exc
    return staged, export_artifact(staged, api_id)


def plan_artifact(
    manager: ManagementService, cfg: GatewayConfig, api_id: str, body: ApiArtifactRequest
) -> dict[str, Any]:
    _, proposed = stage_artifact(manager, cfg, api_id, body)
    current = export_artifact(cfg, api_id) if api_id in cfg.apis else None

    def lines(value):
        return json.dumps(value, indent=2, ensure_ascii=False).splitlines()

    return {
        "api_id": api_id,
        "etag": artifact_etag(cfg, api_id),
        "upstream_override": "set" if body.upstream_base_url is not None else "preserved",
        "diff": "\n".join(
            difflib.unified_diff(lines(current), lines(proposed), fromfile="current", tofile="proposed", lineterm="")
        ),
    }
