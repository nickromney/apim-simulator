from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import ApiConfig, ApiVersionSetConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.openapi_export import build_openapi, build_openapi_export_router
from app.openapi_import import parse_api_import


def _imported_api(document: dict, content_format: str = "openapi+json") -> tuple[ApiConfig, object]:
    imported = parse_api_import(content_format=content_format, content_value=json.dumps(document))
    api = ApiConfig(
        name="Widgets",
        path="widgets",
        upstream_base_url="http://private-backend.test",
        policies_xml='<policies><inbound><set-header name="X-Secret"><value>backend-secret</value></set-header></inbound></policies>',
        schemas=imported.schemas,
        operations={
            operation.name: OperationConfig(
                name=operation.display_name,
                method=operation.method,
                url_template=operation.url_template,
                description=operation.description,
                template_parameters=operation.template_parameters,
                request=operation.request,
                responses=operation.responses,
            )
            for operation in imported.operations
        },
    )
    return api, imported


def test_export_import_roundtrip_preserves_request_response_and_array_metadata() -> None:
    document = {
        "openapi": "3.0.3",
        "info": {"title": "Widgets", "version": "1.0"},
        "components": {"schemas": {"Widget": {"type": "object", "properties": {"name": {"type": "string"}}}}},
        "paths": {
            "/{widgetId}": {
                "post": {
                    "operationId": "update-widget",
                    "summary": "Update Widget",
                    "description": "Update one widget",
                    "parameters": [
                        {"name": "widgetId", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {
                            "name": "region",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string", "enum": ["eu", "us"], "default": "eu"},
                        },
                        {"name": "ids", "in": "query", "schema": {"type": "array", "items": {"type": "integer"}}},
                        {
                            "name": "X-Preview",
                            "in": "header",
                            "description": "Preview mode",
                            "schema": {"type": "boolean", "default": False},
                            "example": True,
                        },
                    ],
                    "requestBody": {
                        "description": "Widget to save",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Widget"},
                                "examples": {"widget": {"value": {"name": "example"}}},
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Saved",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}},
                        },
                        "default": {"description": "Failure", "headers": {"X-Retry": {"schema": {"type": "integer"}}}},
                    },
                }
            }
        },
    }
    api, imported = _imported_api(document)
    exported = build_openapi(api, gateway_url="http://localhost:8000")
    encoded = json.dumps(exported)
    assert exported["servers"] == [{"url": "http://localhost:8000/widgets"}]
    assert "private-backend" not in encoded
    assert "backend-secret" not in encoded
    assert "policies_xml" not in encoded
    roundtrip = parse_api_import(content_format="openapi+json", content_value=encoded)
    original = imported.operations[0]
    restored = roundtrip.operations[0]
    assert restored.name == original.name
    assert restored.description == original.description
    assert restored.url_template == original.url_template
    assert restored.template_parameters == original.template_parameters
    assert restored.request == original.request
    assert restored.responses == original.responses
    assert roundtrip.schemas["Widget"].components["schemas"]["Widget"] == document["components"]["schemas"]["Widget"]


def test_swagger_schema_references_are_converted_to_openapi_components() -> None:
    document = {
        "swagger": "2.0",
        "info": {"title": "Widgets", "version": "1.0"},
        "produces": ["application/json"],
        "definitions": {
            "Widget": {"type": "object", "properties": {"name": {"type": "string"}}},
            "Widgets": {"type": "array", "items": {"$ref": "#/definitions/Widget"}},
        },
        "paths": {
            "/list": {
                "get": {
                    "operationId": "list-widgets",
                    "responses": {"200": {"description": "Widgets", "schema": {"$ref": "#/definitions/Widgets"}}},
                }
            }
        },
    }
    api, _ = _imported_api(document, "swagger-json")
    exported = build_openapi(api)
    assert exported["components"]["schemas"]["Widgets"]["items"] == {"$ref": "#/components/schemas/Widget"}
    imported = parse_api_import(content_format="openapi+json", content_value=json.dumps(exported))
    assert imported.operations[0].responses[0].representations[0].schema_id == "Widgets"


def test_export_endpoint_checks_management_auth_and_uses_gateway_server() -> None:
    cfg = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="export-test"),
        apis={
            "hello": ApiConfig(
                name="Hello",
                path="hello",
                upstream_base_url="http://private-backend.test",
                operations={"get": OperationConfig(name="Get", method="GET", url_template="/{name}")},
            )
        },
    )
    app = FastAPI()
    app.state.gateway_config = cfg
    app.include_router(build_openapi_export_router())
    with TestClient(app) as client:
        path = "/apim/management/apis/hello/export"
        assert client.get(path).status_code == 403
        headers = {"X-Apim-Tenant-Key": "export-test"}
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        document = response.json()
        assert document["servers"] == [{"url": "http://testserver/hello"}]
        assert document["paths"]["/{name}"]["get"]["parameters"] == [
            {"name": "name", "in": "path", "required": True, "schema": {"type": "string"}}
        ]
        assert client.get("/apim/management/apis/missing/export", headers=headers).status_code == 404


def test_export_rejects_unrepresentable_duplicate_operations() -> None:
    api = ApiConfig(
        name="Hello",
        path="hello",
        upstream_base_url="http://backend",
        operations={
            "a": OperationConfig(name="A", method="GET", url_template="/hello?region={region}"),
            "b": OperationConfig(name="B", method="GET", url_template="/hello?country={country}"),
        },
    )
    with pytest.raises(ValueError, match="multiple GET operations"):
        build_openapi(api)


@pytest.mark.parametrize(
    ("scheme", "settings", "selector_location", "selector_name"),
    [
        ("Segment", {}, None, None),
        ("Header", {"version_header_name": "X-Version"}, "header", "X-Version"),
        ("Query", {"version_query_name": "version"}, "query", "version"),
    ],
)
def test_version_export_includes_gateway_selectors_and_keeps_original_unversioned(
    scheme, settings, selector_location, selector_name
) -> None:
    version_set = ApiVersionSetConfig(display_name="Versions", versioning_scheme=scheme, **settings)
    api = ApiConfig(
        name="Hello",
        path="hello",
        upstream_base_url="http://private-backend.test",
        api_version="v2",
        operations={"get": OperationConfig(name="Get", method="GET", url_template="/greet")},
    )
    exported = build_openapi(api, "https://gateway.example.test", version_set=version_set)
    parameters = exported["paths"]["/greet"]["get"]["parameters"]
    if selector_location is None:
        assert exported["servers"] == [{"url": "https://gateway.example.test/hello/v2"}]
        assert parameters == []
    else:
        assert parameters == [
            {
                "name": selector_name,
                "in": selector_location,
                "required": True,
                "schema": {"type": "string", "enum": ["v2"], "default": "v2"},
            }
        ]
    imported = parse_api_import(content_format="openapi+json", content_value=json.dumps(exported))
    assert imported.upstream_base_url == exported["servers"][0]["url"]
    if selector_location is not None:
        request = imported.operations[0].request
        selector = (request.headers if selector_location == "header" else request.query_parameters)[0]
        assert selector.required is True
        assert selector.default_value == "v2"
        assert selector.values == ["v2"]
    original = api.model_copy(update={"api_version": None})
    original_spec = build_openapi(original, "https://gateway.example.test", version_set=version_set)
    assert original_spec["servers"] == [{"url": "https://gateway.example.test/hello"}]
    assert original_spec["paths"]["/greet"]["get"]["parameters"] == []
