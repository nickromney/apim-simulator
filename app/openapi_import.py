from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from app.config import (
    ApiSchemaConfig,
    OperationExampleConfig,
    OperationParameterConfig,
    OperationRepresentationConfig,
    OperationRequestMetadataConfig,
    OperationResponseMetadataConfig,
)

SUPPORTED_API_IMPORT_FORMATS = {
    "openapi",
    "openapi+json",
    "openapi-link",
    "openapi+json-link",
    "swagger-json",
    "swagger-link-json",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
JSON_FORMATS = {"openapi+json", "swagger-json", "openapi+json-link", "swagger-link-json"}
INLINE_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class ImportedOperation:
    name: str
    method: str
    url_template: str
    display_name: str
    description: str | None = None
    template_parameters: list[OperationParameterConfig] = field(default_factory=list)
    request: OperationRequestMetadataConfig | None = None
    responses: list[OperationResponseMetadataConfig] = field(default_factory=list)
    source_operation_id: str | None = None
    generated_name: str | None = None


@dataclass(frozen=True)
class ApiImportResult:
    format: str
    operations: list[ImportedOperation] = field(default_factory=list)
    upstream_base_url: str | None = None
    schemas: dict[str, ApiSchemaConfig] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


def _default_fetcher(url: str) -> str:
    response = httpx.get(url, timeout=30.0)
    response.raise_for_status()
    return response.text


def _load_api_document(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError("API import document is not valid JSON or YAML") from exc
    if not isinstance(data, dict):
        raise ValueError("API import document must be an object")
    return data


def _swagger_schemas(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("swagger") != "2.0":
        raise ValueError(f"Unsupported Swagger version: {document.get('swagger')}")
    schemas = document.get("definitions", {})
    if not isinstance(schemas, dict):
        raise ValueError("Swagger definitions must be an object")
    return schemas


def _openapi_schemas(document: dict[str, Any], version: str) -> dict[str, Any]:
    if version.startswith("3.1"):
        raise ValueError("OpenAPI 3.1 is unsupported by the simulator import contract")
    if not re.fullmatch(r"3\.0\.[0-4]", version):
        raise ValueError(f"Unsupported OpenAPI version: {version}")
    components = document.get("components", {})
    if not isinstance(components, dict):
        raise ValueError("OpenAPI components must be an object")
    schemas = components.get("schemas", {})
    if not isinstance(schemas, dict):
        raise ValueError("OpenAPI component schemas must be an object")
    return schemas


def _api_version(document: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if "swagger" in document:
        return "2.0", _swagger_schemas(document)
    version = document.get("openapi")
    if not isinstance(version, str):
        raise ValueError("API import document must declare swagger or openapi version")
    return version, _openapi_schemas(document, version)


def _internal_ref(ref: Any, schemas: dict[str, Any]) -> str:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise ValueError("External or invalid $ref values are unsupported")
    parts = ref[2:].split("/")
    if len(parts) == 3 and parts[:2] == ["components", "schemas"]:
        name = parts[2]
    elif len(parts) == 2 and parts[0] in {"definitions", "schemas"}:
        name = parts[1]
    else:
        raise ValueError(f"Unsupported $ref target: {ref}")
    name = name.replace("~1", "/").replace("~0", "~")
    if name not in schemas:
        raise ValueError(f"Unresolved $ref: {ref}")
    return name


def _schema_refs(value: Any, schemas: dict[str, Any], owner: str) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        if "$ref" in value:
            refs.add(_internal_ref(value["$ref"], schemas))
        for child in value.values():
            refs.update(_schema_refs(child, schemas, owner))
    elif isinstance(value, list):
        for child in value:
            refs.update(_schema_refs(child, schemas, owner))
    return refs


def _validate_schema_graph(schemas: dict[str, Any]) -> None:
    if not isinstance(schemas, dict):
        raise ValueError("API schemas must be an object")
    if any(not isinstance(schema, dict) for schema in schemas.values()):
        raise ValueError("API schema definitions must be objects")
    graph = {name: _schema_refs(value, schemas, name) for name, value in schemas.items()}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError(f"Recursive schema definitions are unsupported: {name}")
        if name in visited:
            return
        visiting.add(name)
        for target in graph[name]:
            visit(target)
        visiting.remove(name)
        visited.add(name)

    for schema_name in graph:
        visit(schema_name)


def _schema_id(schema: Any, version: str, schemas: dict[str, Any]) -> str | None:
    if not isinstance(schema, dict) or "$ref" not in schema:
        return None
    ref = str(schema["$ref"])
    if version == "2.0" and ref.startswith("#/definitions/"):
        ref = "#/definitions/" + ref.rsplit("/", 1)[-1]
    return _internal_ref(ref, schemas)


def _project_schema(schema: Any, version: str, schemas: dict[str, Any]) -> str | None:
    """Preserve inline metadata in an API-scoped schema, rather than discard it."""
    if schema is None or schema == {}:
        return None
    if not isinstance(schema, dict):
        raise ValueError("Operation schema must be an object")
    referenced = _schema_id(schema, version, schemas)
    if referenced is not None:
        return referenced
    _schema_refs(schema, schemas, "inline schema")
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]
    name = f"imported-{digest}"
    while name in schemas and schemas[name] != schema:
        name += "-inline"
    schemas[name] = schema
    return name


def _schema_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "string"
    if schema.get("type") == "array":
        return "array"
    if schema.get("type") == "integer":
        return "integer"
    if schema.get("type") == "number":
        return "number"
    if schema.get("type") == "boolean":
        return "boolean"
    if schema.get("type") == "object" or "properties" in schema or "$ref" in schema:
        return "object"
    return str(schema.get("type") or "string")


def _example(
    name: str, value: Any, summary: str | None = None, description: str | None = None
) -> list[OperationExampleConfig]:
    if value is None:
        return []
    return [OperationExampleConfig(name=name, summary=summary, description=description, value=value)]


def _parameter(item: dict[str, Any], version: str, schemas: dict[str, Any]) -> OperationParameterConfig:
    name = str(item.get("name") or "").strip()
    location = str(item.get("in") or "")
    if not name:
        raise ValueError("OpenAPI parameter is missing its name")
    if location == "cookie":
        raise ValueError(f"Cookie parameter {name!r} is unsupported")
    if location not in {"path", "query", "header"} and not (version == "2.0" and location == "formData"):
        raise ValueError(f"Unsupported parameter location {location!r}")
    schema, values, default = _parameter_schema(item, version, location, name)
    return OperationParameterConfig(
        name=name,
        required=bool(item.get("required")),
        type=_schema_type(schema),
        description=item.get("description"),
        default_value=str(default) if default is not None else None,
        values=[str(value) for value in values] if isinstance(values, list) else [],
        examples=_example("default", item.get("example", schema.get("example") if isinstance(schema, dict) else None)),
        schema_id=_project_schema(schema, version, schemas),
    )


def _parameter_schema(item: dict[str, Any], version: str, location: str, name: str) -> tuple[dict[str, Any], Any, Any]:
    if version == "2.0":
        supported = {
            "name",
            "in",
            "required",
            "description",
            "type",
            "enum",
            "default",
            "example",
            "format",
            "minimum",
            "maximum",
            "items",
            "collectionFormat",
        }
        _reject_fields(item, supported, f"parameter {name!r}")
        schema = {
            key: item[key]
            for key in ("type", "enum", "default", "example", "format", "minimum", "maximum", "items")
            if key in item
        }
        schema.setdefault("type", "string")
        if schema["type"] == "file" and location == "formData":
            schema = {"type": "string", "format": "binary"}
        if schema["type"] == "array" and (location != "query" or item.get("collectionFormat") != "multi"):
            raise ValueError(f"Unsupported {location} array parameter serialization for {name!r}; use multi")
        _validate_parameter_schema(schema, name, location)
        return schema, item.get("enum", []), item.get("default")
    supported = {"name", "in", "required", "description", "schema", "example", "style", "explode"}
    _reject_fields(item, supported, f"parameter {name!r}")
    schema = item.get("schema", {})
    if not isinstance(schema, dict) or not schema:
        raise ValueError(f"Parameter {name!r} has an invalid schema")
    default_style = "form" if location == "query" else "simple"
    default_explode = default_style == "form"
    if item.get("style", default_style) != default_style or item.get("explode", default_explode) is not default_explode:
        raise ValueError(f"Unsupported {location} parameter serialization for {name!r}")
    _validate_parameter_schema(schema, name, location)
    return schema, schema.get("enum", []), schema.get("default")


def _validate_parameter_schema(schema: dict[str, Any], name: str, location: str) -> None:
    if schema.get("type") == "array":
        if location != "query":
            raise ValueError(f"Array parameter serialization is unsupported for {location} parameter {name!r}")
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict) or item_schema.get("type") == "array":
            raise ValueError(f"Array parameter {name!r} must have scalar items")
        _validate_parameter_schema(item_schema, name, location)
    if "$ref" in schema:
        raise ValueError(f"Referenced parameter schemas are unsupported for {name!r}")
    if schema.get("type", "string") not in {"string", "integer", "number", "boolean", "array"}:
        raise ValueError(f"Non-scalar parameter serialization is unsupported for {name!r}")
    unsupported = set(schema) - {"type", "enum", "default", "example", "format", "minimum", "maximum", "items"}
    if unsupported:
        fields = ", ".join(sorted(unsupported))
        raise ValueError(f"Unsupported parameter schema fields for {name!r}: {fields}")


def _reject_fields(value: dict[str, Any], supported: set[str], owner: str) -> None:
    unsupported = set(value) - supported
    if unsupported:
        fields = ", ".join(sorted(unsupported))
        raise ValueError(f"Unsupported fields for {owner}: {fields}")


def _merged_parameter_items(path_items: Any, operation_items: Any) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in [
        *(path_items if isinstance(path_items, list) else []),
        *(operation_items if isinstance(operation_items, list) else []),
    ]:
        if not isinstance(item, dict):
            raise ValueError("Parameter references are unsupported")
        key = (str(item.get("name") or "").casefold(), str(item.get("in") or ""))
        merged[key] = item
    return list(merged.values())


def _operation_name(method: str, path: str, payload: dict[str, Any]) -> str:
    operation_id = payload.get("operationId")
    if not isinstance(operation_id, str) or not operation_id.strip():
        operation_id = f"{method}-{path}"
    normalized = re.sub(r"[^a-z0-9]+", "-", operation_id.lower()).strip("-")[:76].rstrip("-")
    if not normalized:
        normalized = re.sub(r"[^a-z0-9]+", "-", f"{method}-{path}".lower()).strip("-")[:76].rstrip("-")
    return normalized


def _deduplicate_name(name: str, counts: dict[str, int], used: set[str]) -> str:
    count = counts.get(name, 0)
    if count == 0 and name not in used:
        counts[name] = 1
        used.add(name)
        return name
    if count == 0:
        count = 1
    while count <= 999:
        suffix = f"-{count}"
        candidate = f"{name[:76].rstrip('-')}{suffix}"
        count += 1
        if candidate not in used:
            counts[name] = count
            used.add(candidate)
            return candidate
    raise ValueError(f"More than 999 operations normalize to {name!r}")


def _content_examples(media: dict[str, Any], version: str) -> list[OperationExampleConfig]:
    if version == "2.0":
        return _example("default", media.get("example"))
    examples = media.get("examples")
    if isinstance(examples, dict):
        result = []
        for name, item in examples.items():
            if not isinstance(item, dict):
                continue
            if "$ref" in item:
                raise ValueError("Referenced OpenAPI examples are unsupported")
            result.extend(_example(str(name), item.get("value"), item.get("summary"), item.get("description")))
        return result
    return _example("default", media.get("example"))


def _swagger_representations(content: Any) -> list[OperationRepresentationConfig]:
    if not isinstance(content, list):
        return []
    return [OperationRepresentationConfig(content_type=item) for item in content if isinstance(item, str)]


def _openapi_representations(content: Any, schemas: dict[str, Any]) -> list[OperationRepresentationConfig]:
    if not isinstance(content, dict):
        return []
    result = []
    for media_type, media in content.items():
        if not isinstance(media, dict):
            continue
        schema = media.get("schema")
        schema_ref = _project_schema(schema, "3.0", schemas)
        result.append(
            OperationRepresentationConfig(
                content_type=str(media_type),
                schema_id=schema_ref,
                examples=_content_examples(media, "3.0"),
            )
        )
    return result


def _representations(content: Any, version: str, schemas: dict[str, Any]) -> list[OperationRepresentationConfig]:
    return _swagger_representations(content) if version == "2.0" else _openapi_representations(content, schemas)


def _parameter_groups(
    operation: dict[str, Any], path_items: Any, version: str, schemas: dict[str, Any], translate: str
) -> tuple[
    list[OperationParameterConfig], list[OperationParameterConfig], list[OperationParameterConfig], list[dict[str, Any]]
]:
    raw_parameters = _merged_parameter_items(path_items, operation.get("parameters"))
    body_parameters = [item for item in raw_parameters if item.get("in") in {"body", "formData"}]
    if body_parameters and version != "2.0":
        raise ValueError("OpenAPI 3 body parameters must use requestBody")
    url_raw = [item for item in raw_parameters if item.get("in") not in {"body", "formData"}]
    params = [_parameter(item, version, schemas) for item in url_raw]
    path_name_list = [name.casefold() for name in re.findall(r"\{([^{}]+)\}", str(operation.get("_path", "")))]
    if len(path_name_list) != len(set(path_name_list)):
        raise ValueError("URL template parameter names must be unique")
    path_names = set(path_name_list)
    path_params = [item for item, raw in zip(params, url_raw, strict=True) if raw.get("in") == "path"]
    query_params = [item for item, raw in zip(params, url_raw, strict=True) if raw.get("in") == "query"]
    header_params = [item for item, raw in zip(params, url_raw, strict=True) if raw.get("in") == "header"]
    for param in path_params:
        if param.name.casefold() not in path_names or not param.required:
            raise ValueError(f"Path parameter {param.name!r} must be required and part of the URL template")
    declared_path_names = {item.name.casefold() for item in path_params}
    if declared_path_names != path_names:
        raise ValueError("Every URL template parameter must have a matching path parameter definition")
    url_params = [*path_params, *(item for item in query_params if item.required)]
    normalized_names = [item.name.casefold() for item in url_params]
    if len(normalized_names) != len(set(normalized_names)):
        raise ValueError("Required path and query parameter names must be unique in the URL template")
    template_parameters = list(path_params)
    if translate != "query":
        template_parameters.extend(item for item in query_params if item.required)
    return template_parameters, query_params, header_params, body_parameters


def _swagger_request_body(
    operation: dict[str, Any], schemas: dict[str, Any], body_parameters: list[dict[str, Any]]
) -> OperationRequestMetadataConfig | None:
    if not body_parameters:
        return None
    form_parameters = [item for item in body_parameters if item.get("in") == "formData"]
    if form_parameters:
        if len(form_parameters) != len(body_parameters):
            raise ValueError("Swagger operations cannot mix body and formData parameters")
        return _swagger_form_body(operation, schemas, form_parameters)
    if len(body_parameters) > 1:
        raise ValueError("Swagger operations with multiple body parameters are unsupported")
    body_schema = body_parameters[0].get("schema", {})
    if not isinstance(body_schema, dict):
        raise ValueError("Swagger request body schema must be an object")
    schema_id = _project_schema(body_schema, "2.0", schemas)
    consumes = operation.get("consumes") or operation.get("_consumes") or []
    return OperationRequestMetadataConfig(
        description=body_parameters[0].get("description"),
        representations=[
            OperationRepresentationConfig(
                content_type=str(kind), schema_id=schema_id, examples=_example("default", body_schema.get("example"))
            )
            for kind in consumes
        ],
    )


def _swagger_form_body(
    operation: dict[str, Any], schemas: dict[str, Any], parameters: list[dict[str, Any]]
) -> OperationRequestMetadataConfig:
    consumes = operation.get("consumes") or operation.get("_consumes") or []
    if not consumes or any(
        kind not in {"multipart/form-data", "application/x-www-form-urlencoded"} for kind in consumes
    ):
        raise ValueError("Swagger formData requires multipart/form-data or application/x-www-form-urlencoded")
    projected = [_parameter(item, "2.0", schemas) for item in parameters]
    return OperationRequestMetadataConfig(
        representations=[
            OperationRepresentationConfig(content_type=kind, form_parameters=projected) for kind in consumes
        ]
    )


def _openapi_request_body(operation: dict[str, Any], schemas: dict[str, Any]) -> OperationRequestMetadataConfig | None:
    request_body = operation.get("requestBody")
    if request_body is None:
        return None
    if not isinstance(request_body, dict) or "$ref" in request_body:
        raise ValueError("Referenced requestBody components are unsupported")
    return OperationRequestMetadataConfig(
        description=request_body.get("description"),
        representations=_representations(request_body.get("content"), "3.0", schemas),
    )


def _request_body_metadata(
    operation: dict[str, Any], method: str, version: str, schemas: dict[str, Any], body_parameters: list[dict[str, Any]]
) -> OperationRequestMetadataConfig | None:
    if method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if version == "2.0":
        return _swagger_request_body(operation, schemas, body_parameters)
    return _openapi_request_body(operation, schemas)


def _request_metadata(
    operation: dict[str, Any], path_items: Any, method: str, version: str, schemas: dict[str, Any], translate: str
) -> tuple[list[OperationParameterConfig], OperationRequestMetadataConfig | None]:
    template, query, headers, body_parameters = _parameter_groups(operation, path_items, version, schemas, translate)
    request = (
        _request_body_metadata(operation, method, version, schemas, body_parameters) or OperationRequestMetadataConfig()
    )
    request.headers = headers
    request.query_parameters = query
    return template, request if request != OperationRequestMetadataConfig() else None


def _response_representations(
    response: dict[str, Any], operation: dict[str, Any], version: str, schemas: dict[str, Any]
) -> list[OperationRepresentationConfig]:
    if version != "2.0":
        return _representations(response.get("content"), version, schemas)
    content = response.get("schema")
    if content is not None and not isinstance(content, dict):
        raise ValueError("Operation response schema must be an object")
    schema_ref = _project_schema(content, version, schemas)
    produces = response.get("produces") or operation.get("_produces") or []
    examples = response.get("examples", {})
    return [
        OperationRepresentationConfig(
            content_type=str(media),
            schema_id=schema_ref,
            examples=_example("default", examples.get(media)) if isinstance(examples, dict) else [],
        )
        for media in produces
    ]


def _response_headers(
    response: dict[str, Any], version: str, schemas: dict[str, Any]
) -> list[OperationParameterConfig]:
    raw_headers = response.get("headers", {})
    if not isinstance(raw_headers, dict):
        return []
    result = []
    for name, header in raw_headers.items():
        if not isinstance(header, dict) or "$ref" in header:
            raise ValueError("Referenced response headers are unsupported")
        result.append(_parameter({**header, "name": name, "in": "header"}, version, schemas))
    return result


def _responses(
    operation: dict[str, Any], version: str, schemas: dict[str, Any]
) -> list[OperationResponseMetadataConfig]:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        raise ValueError("Operation responses must be an object")
    output = []
    for status, response in responses.items():
        if status != "default" and not str(status).isdigit():
            raise ValueError(f"Unsupported response status key: {status}")
        if not isinstance(response, dict):
            raise ValueError(f"Response {status} must be an object")
        if "$ref" in response:
            raise ValueError("Referenced responses are unsupported")
        output.append(
            OperationResponseMetadataConfig(
                status_code="default" if status == "default" else int(status),
                description=response.get("description"),
                headers=_response_headers(response, version, schemas),
                representations=_response_representations(response, operation, version, schemas),
            )
        )
    return output


def _swagger_server_url(document: dict[str, Any], source_url: str | None) -> str | None:
    x_servers = document.get("x-servers")
    if isinstance(x_servers, list):
        for server in x_servers:
            url = server.get("url") if isinstance(server, dict) else None
            if isinstance(url, str) and not urlparse(url).scheme and source_url:
                url = urljoin(source_url, url)
            parsed = urlparse(url) if isinstance(url, str) else None
            if parsed and parsed.scheme == "https" and parsed.netloc:
                return url.rstrip("/")
    host = document.get("host")
    if not host:
        return None
    schemes = document.get("schemes") or ["https"]
    scheme = next((item for item in schemes if item in {"https", "http"}), None)
    return f"{scheme}://{host}{document.get('basePath') or ''}".rstrip("/") if scheme else None


def _openapi_server_url(document: dict[str, Any], source_url: str | None) -> str | None:
    servers = document.get("servers")
    if not isinstance(servers, list):
        return None
    for server in servers:
        if not isinstance(server, dict):
            continue
        url = server.get("url")
        if server.get("variables") or (isinstance(url, str) and ("{" in url or "}" in url)):
            raise ValueError("Templated OpenAPI server URLs are unsupported")
        if isinstance(url, str) and not urlparse(url).scheme and source_url:
            url = urljoin(source_url, url)
        parsed = urlparse(url) if isinstance(url, str) else None
        if parsed and parsed.scheme == "https" and parsed.netloc:
            return url.rstrip("/")
    return None


def _server_url(document: dict[str, Any], version: str, source_url: str | None) -> str | None:
    if version == "2.0":
        return _swagger_server_url(document, source_url)
    return _openapi_server_url(document, source_url)


def _load_import_raw(normalized: str, content_value: str, fetcher: Callable[[str], str] | None) -> tuple[str, bool]:
    is_link = "link" in normalized
    try:
        raw = (fetcher or _default_fetcher)(content_value) if is_link else content_value
    except Exception as exc:
        raise ValueError(f"Unable to load API import document: {exc}") from exc
    if not isinstance(raw, str):
        raise ValueError("API import document content must be text")
    if not is_link and len(raw.encode("utf-8")) > INLINE_LIMIT:
        raise ValueError("Inline OpenAPI documents are limited to 4 MB")
    try:
        json.loads(raw)
        is_json = True
    except json.JSONDecodeError:
        is_json = False
    if normalized in JSON_FORMATS and not is_json:
        raise ValueError(f"{normalized} import format requires JSON content")
    return raw, is_json


def _load_import_spec(raw: str, is_json: bool) -> tuple[dict[str, Any], str, dict[str, Any]]:
    document = _load_api_document(raw)
    version, schemas = _api_version(document)
    if version == "2.0" and not is_json:
        raise ValueError("Swagger 2.0 import supports JSON documents only")
    if "x-ms-paths" in document:
        raise ValueError("The x-ms-paths extension is not supported by the simulator importer")
    _validate_schema_graph(schemas)
    return document, version, schemas


def _import_operation(
    path: str,
    path_item: dict[str, Any],
    method: str,
    payload: Any,
    version: str,
    schemas: dict[str, Any],
    translate: str,
    document: dict[str, Any],
) -> ImportedOperation:
    if not isinstance(payload, dict):
        raise ValueError(f"Operation {method.upper()} {path} must be an object")
    if version != "2.0" and ("servers" in payload or "servers" in path_item):
        raise ValueError("Path- and operation-level servers are unsupported")
    if version != "2.0" and ("callbacks" in payload or "externalDocs" in payload):
        raise ValueError("OpenAPI operation callbacks and externalDocs are unsupported")
    operation = {
        **payload,
        "_path": path,
        "_consumes": payload.get("consumes") or document.get("consumes", []),
        "_produces": payload.get("produces") or document.get("produces", []),
    }
    name = _operation_name(method, path, operation)
    template, request = _request_metadata(
        operation, path_item.get("parameters"), method.upper(), version, schemas, translate
    )
    path_names = {part.casefold() for part in re.findall(r"\{([^{}]+)\}", path)}
    required_query = [item for item in template if item.name.casefold() not in path_names]
    imported_path = path
    if required_query:
        imported_path += "?" + "&".join(f"{item.name}={{{item.name}}}" for item in required_query)
    if len(imported_path) >= 128:
        raise ValueError(f"URL template must be shorter than 128 characters: {imported_path}")
    raw_id = payload.get("operationId")
    return ImportedOperation(
        name=name,
        method=method.upper(),
        url_template=imported_path,
        display_name=str(payload.get("summary") or raw_id or name)[:300],
        description=payload.get("description"),
        template_parameters=template,
        request=request,
        responses=_responses(operation, version, schemas),
        source_operation_id=str(raw_id) if raw_id else None,
        generated_name=_operation_name(method, path, {}),
    )


def _operations_for_path(
    path: str,
    path_item: dict[str, Any],
    version: str,
    schemas: dict[str, Any],
    translate: str,
    document: dict[str, Any],
    counts: dict[str, int],
    used: set[str],
) -> list[ImportedOperation]:
    if not path.startswith("/"):
        raise ValueError(f"OpenAPI path must start with '/': {path}")
    if "$ref" in path_item:
        raise ValueError("PathItem references are unsupported")
    if len(path) >= 128:
        raise ValueError(f"URL template must be shorter than 128 characters: {path}")
    operations = []
    for method, payload in path_item.items():
        if not isinstance(method, str) or method.lower() not in HTTP_METHODS:
            continue
        if method.lower() == "trace" and version != "2.0":
            raise ValueError("OpenAPI 3 path-item trace operations are unsupported")
        imported = _import_operation(path, path_item, method, payload, version, schemas, translate, document)
        operations.append(replace(imported, name=_deduplicate_name(imported.name, counts, used)))
    return operations


def _operations_from_paths(
    paths: dict[Any, Any], version: str, schemas: dict[str, Any], translate: str, document: dict[str, Any]
) -> list[ImportedOperation]:
    counts: dict[str, int] = {}
    used: set[str] = set()
    return [
        imported
        for path, path_item in paths.items()
        if isinstance(path, str) and isinstance(path_item, dict)
        for imported in _operations_for_path(path, path_item, version, schemas, translate, document, counts, used)
    ]


def parse_api_import(
    *,
    content_format: str,
    content_value: str,
    fetcher: Callable[[str], str] | None = None,
    translate_required_query_parameters: str = "template",
) -> ApiImportResult:
    normalized = (content_format or "").strip().lower()
    if normalized not in SUPPORTED_API_IMPORT_FORMATS:
        raise ValueError(f"Unsupported API import format: {content_format}")
    if translate_required_query_parameters not in {"template", "query"}:
        raise ValueError("translate_required_query_parameters must be 'template' or 'query'")
    raw, is_json = _load_import_raw(normalized, content_value, fetcher)
    document, version, schemas = _load_import_spec(raw, is_json)
    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("API import document missing paths")
    operations = _operations_from_paths(paths, version, schemas, translate_required_query_parameters, document)
    content_type = "application/vnd.oai.openapi+json" if version != "2.0" else "application/vnd.swagger+json"
    if version == "2.0":
        imported_schemas = {name: ApiSchemaConfig(content_type=content_type, definitions=schemas) for name in schemas}
    else:
        imported_schemas = {
            name: ApiSchemaConfig(content_type=content_type, components={"schemas": schemas}) for name in schemas
        }
    diagnostics = [] if operations else ["API import document did not produce any operations."]
    if version == "3.0.4":
        diagnostics.append(
            "OpenAPI 3.0.4 is accepted as a local extension to the documented Azure 3.0.3 import subset."
        )
    return ApiImportResult(
        format=normalized,
        operations=operations,
        upstream_base_url=_server_url(document, version, content_value if "link" in normalized else None),
        schemas=imported_schemas,
        diagnostics=diagnostics,
    )
