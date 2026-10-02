"""Export the simulator's public API contract as an OpenAPI 3 document."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request

from app.config import (
    ApiConfig,
    ApiVersionSetConfig,
    OperationConfig,
    OperationParameterConfig,
    OperationRepresentationConfig,
)


def _normalize_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: item.replace("#/definitions/", "#/components/schemas/", 1)
            if key == "$ref" and isinstance(item, str)
            else _normalize_refs(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_refs(item) for item in value]
    return value


def _schemas(api: ApiConfig) -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    for schema_id, schema in api.schemas.items():
        schemas.update(deepcopy(schema.definitions))
        schemas.update(deepcopy(schema.components.get("schemas", {})))
        if schema.value:
            value = json.loads(schema.value)
            if not isinstance(value, dict):
                raise ValueError(f"Schema {schema_id} must contain a JSON object")
            nested = value.get("components", {}).get("schemas") or value.get("definitions")
            schemas.update(nested if nested is not None else {schema_id: value})
    return _normalize_refs(schemas)


def _typed_value(value: str, type_name: str) -> Any:
    if type_name in {"integer", "number", "boolean", "array"}:
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _parameter_schema(parameter: OperationParameterConfig, schemas: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(schemas.get(parameter.schema_id, {}))
    schema.setdefault("type", parameter.type)
    if parameter.type == "array":
        schema.setdefault("items", {"type": parameter.type_name or "string"})
    if parameter.default_value is not None:
        schema.setdefault("default", _typed_value(parameter.default_value, parameter.type))
    if parameter.values:
        schema.setdefault("enum", [_typed_value(value, parameter.type) for value in parameter.values])
    return schema


def _parameter(parameter: OperationParameterConfig, location: str, schemas: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": parameter.name,
        "in": location,
        "required": location == "path" or parameter.required,
        "schema": _parameter_schema(parameter, schemas),
    }
    if parameter.description is not None:
        payload["description"] = parameter.description
    if parameter.examples:
        payload["example"] = parameter.examples[0].value
    return payload


def _media(representation: OperationRepresentationConfig, schemas: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if representation.schema_id is not None:
        if representation.schema_id not in schemas:
            raise ValueError(f"Representation references unknown schema {representation.schema_id}")
        escaped = representation.schema_id.replace("~", "~0").replace("/", "~1")
        payload["schema"] = {"$ref": f"#/components/schemas/{escaped}"}
    if representation.form_parameters:
        payload["schema"] = {
            "type": "object",
            "properties": {item.name: _parameter_schema(item, schemas) for item in representation.form_parameters},
            "required": [item.name for item in representation.form_parameters if item.required],
        }
    if representation.examples:
        payload["examples"] = {
            example.name: example.model_dump(mode="json", exclude={"name"}, exclude_none=True)
            for example in representation.examples
        }
        for example in payload["examples"].values():
            if "external_value" in example:
                example["externalValue"] = example.pop("external_value")
    return payload


def _content(representations: list[OperationRepresentationConfig], schemas: dict[str, Any]) -> dict[str, Any]:
    return {representation.content_type: _media(representation, schemas) for representation in representations}


def _operation_parameters(operation: OperationConfig, path: str, schemas: dict[str, Any]) -> list[dict[str, Any]]:
    path_names = set(re.findall(r"\{([^{}]+)\}", path))
    parameters = {
        ("path" if item.name in path_names else "query", item.name): item for item in operation.template_parameters
    }
    if operation.request is not None:
        parameters.update({("query", item.name): item for item in operation.request.query_parameters})
        parameters.update({("header", item.name): item for item in operation.request.headers})
    # Hand-authored URL templates may omit parameter metadata.
    for name in path_names:
        parameters.setdefault(("path", name), OperationParameterConfig(name=name, required=True, type="string"))
    return [_parameter(item, location, schemas) for (location, _), item in parameters.items()]


def _operation(operation_id: str, operation: OperationConfig, path: str, schemas: dict[str, Any]) -> dict[str, Any]:
    responses: dict[str, Any] = {}
    for response in operation.responses:
        headers = {item.name: _parameter(item, "header", schemas) for item in response.headers}
        for header in headers.values():
            header.pop("name")
            header.pop("in")
        responses[str(response.status_code)] = {
            "description": response.description or "",
            **({"headers": headers} if headers else {}),
            **({"content": _content(response.representations, schemas)} if response.representations else {}),
        }
    payload = {
        "operationId": operation_id,
        "summary": operation.name or operation_id,
        "parameters": _operation_parameters(operation, path, schemas),
        "responses": responses or {"default": {"description": "Response"}},
    }
    if operation.description is not None:
        payload["description"] = operation.description
    if operation.request is not None and operation.request.representations:
        payload["requestBody"] = {
            "content": _content(operation.request.representations, schemas),
            **({"description": operation.request.description} if operation.request.description is not None else {}),
        }
    return payload


def _version_selector(payload: dict[str, Any], api: ApiConfig, version_set: ApiVersionSetConfig | None) -> None:
    if api.api_version is None or version_set is None or version_set.versioning_scheme == "Segment":
        return
    location = "header" if version_set.versioning_scheme == "Header" else "query"
    name = version_set.version_header_name if location == "header" else version_set.version_query_name
    parameters = [
        item for item in payload["parameters"] if item["in"] != location or item["name"].casefold() != name.casefold()
    ]
    parameters.append(
        {
            "name": name,
            "in": location,
            "required": True,
            "schema": {"type": "string", "enum": [api.api_version], "default": api.api_version},
        }
    )
    payload["parameters"] = parameters


def build_openapi(
    api: ApiConfig, gateway_url: str | None = None, *, version_set: ApiVersionSetConfig | None = None
) -> dict[str, Any]:
    """Build public contract metadata; backend URLs and policy configuration stay private."""
    schemas = _schemas(api)
    paths: dict[str, Any] = {}
    for operation_id, operation in api.operations.items():
        path = "/" + operation.url_template.split("?", 1)[0].lstrip("/")
        method = operation.method.lower()
        path_item = paths.setdefault(path, {})
        if method in path_item:
            raise ValueError(f"OpenAPI cannot represent multiple {operation.method} operations at {path}")
        payload = _operation(operation_id, operation, path, schemas)
        _version_selector(payload, api, version_set)
        path_item[method] = payload
    server = f"{gateway_url.rstrip('/') if gateway_url else ''}/{api.path.strip('/')}".rstrip("/") or "/"
    if api.api_version is not None and version_set is not None and version_set.versioning_scheme == "Segment":
        server = server.rstrip("/") + "/" + quote(api.api_version, safe="")
    return {
        "openapi": "3.0.3",
        "info": {"title": api.name or api.path or "API", "version": api.api_version or "1.0"},
        "servers": [{"url": server}],
        "paths": paths,
        "components": {"schemas": schemas},
    }


def build_openapi_export_router() -> APIRouter:
    from app.named_values import mask_secret_data
    from app.security import require_tenant_access

    router = APIRouter()

    @router.get("/apim/management/apis/{api_id}/export")
    async def export_api(api_id: str, request: Request) -> dict[str, Any]:
        require_tenant_access(request)
        cfg = request.app.state.gateway_config
        api = cfg.apis.get(api_id)
        if api is None:
            raise HTTPException(status_code=404, detail="API not found")
        try:
            version_set = cfg.api_version_sets.get(api.api_version_set)
            return mask_secret_data(build_openapi(api, str(request.base_url), version_set=version_set), cfg)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Cannot export API: {exc}") from exc

    return router
