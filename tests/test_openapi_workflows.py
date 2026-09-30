from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.main import create_app
from app.terraform_import import import_from_tofu_show_json
from app.urls import http_url


def _spec(operation_id: str = "createWidget") -> dict:
    return {
        "openapi": "3.0.2",
        "servers": [{"url": "https://upstream.example/api"}],
        "components": {
            "schemas": {
                "Widget": {
                    "type": "object",
                    "required": ["owner"],
                    "properties": {"owner": {"$ref": "#/components/schemas/Owner"}},
                },
                "Owner": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                },
            }
        },
        "paths": {
            "/widgets": {
                "post": {
                    "operationId": operation_id,
                    "summary": "Create a widget",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}},
                    },
                    "responses": {
                        "200": {
                            "description": "Created",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}},
                        }
                    },
                }
            }
        },
    }


def test_management_import_routes_query_templates_and_validates_nested_referenced_schema() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant")
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = TestClient(app)
    spec = {
        "openapi": "3.0.3",
        "servers": [{"url": "https://unused.example"}],
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id", "owner"],
                    "properties": {"id": {"type": "integer"}, "owner": {"$ref": "#/components/schemas/Owner"}},
                },
                "Owner": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
            }
        },
        "paths": {
            "/items/{itemId}": {
                "parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "post": {
                    "operationId": "Save Item",
                    "parameters": [{"name": "region", "in": "query", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Item"}}}},
                    "responses": {"200": {"description": "ok"}},
                },
            }
        },
    }
    policy = (
        '<policies><inbound><validate-content unspecified-content-type-action="prevent" max-size="1000" '
        'size-exceeded-action="prevent"><content type="application/json" validate-as="json" action="prevent" />'
        "</validate-content></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>"
    )

    with client:
        imported = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={
                "content_format": "openapi+json",
                "content_value": json.dumps(spec),
                "upstream_base_url": http_url("import-upstream"),
                "policies_xml": policy,
            },
        )
        valid = client.post("/catalog/items/8?region=east", json={"id": 8, "owner": {"name": "Ada"}})
        invalid = client.post("/catalog/items/8?region=east", json={"id": 8, "owner": {}})
        missing_query = client.post("/catalog/items/8", json={"id": 8, "owner": {"name": "Ada"}})

    assert imported.status_code == 200
    assert imported.json()["api"]["operations"][0]["id"] == "save-item"
    operation = imported.json()["api"]["operations"][0]
    assert operation["name"] == "Save Item"
    assert operation["url_template"] == "/items/{itemId}?region={region}"
    assert operation["request"]["representations"][0]["schema_id"] == "Item"
    assert valid.status_code == 200
    assert invalid.status_code == 400
    assert missing_query.status_code == 404
    assert len(calls) == 1


def test_reimport_refreshes_metadata_deletes_unmatched_operations_and_copies_path_policy() -> None:
    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            apis={
                "catalog": ApiConfig(
                    name="Catalog",
                    path="catalog",
                    upstream_base_url=http_url("existing"),
                    operations={
                        "stale": OperationConfig(name="old", method="GET", url_template="/old"),
                    },
                )
            },
            tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        )
    )
    policy = "<policies><inbound /><backend /><outbound /><on-error /></policies>"
    with TestClient(app) as client:
        first = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": json.dumps(_spec("createWidget"))},
        )
        current = app.state.gateway_config
        first_id = next(iter(current.apis["catalog"].operations))
        current.apis["catalog"].operations[first_id].policies_xml = policy
        second_spec = _spec("renamedWidget")
        second_spec["paths"]["/widgets"]["post"]["summary"] = "Updated summary"
        second = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": json.dumps(second_spec)},
        )

    api = app.state.gateway_config.apis["catalog"]
    assert first.status_code == second.status_code == 200
    assert set(api.operations) == {"post-widgets"}
    assert api.operations["post-widgets"].name == "Updated summary"
    assert api.operations["post-widgets"].policies_xml == policy


def test_reimport_copies_path_policy_when_required_query_metadata_changes() -> None:
    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            apis={"catalog": ApiConfig(name="Catalog", path="catalog", upstream_base_url=http_url("existing"))},
            tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        )
    )
    policy = "<policies><inbound /><backend /><outbound /><on-error /></policies>"
    required_query = _spec("firstOperation")
    required_query["paths"]["/widgets"]["post"]["parameters"] = [
        {"name": "region", "in": "query", "required": True, "schema": {"type": "string"}}
    ]
    optional_query = _spec("renamedOperation")
    optional_query["paths"]["/widgets"]["post"]["parameters"] = [
        {"name": "region", "in": "query", "required": False, "schema": {"type": "string"}}
    ]

    with TestClient(app) as client:
        first = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": json.dumps(required_query)},
        )
        first_id = next(iter(app.state.gateway_config.apis["catalog"].operations))
        app.state.gateway_config.apis["catalog"].operations[first_id].policies_xml = policy
        second = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": json.dumps(optional_query)},
        )

    operation = app.state.gateway_config.apis["catalog"].operations["post-widgets"]
    assert first.status_code == second.status_code == 200
    assert operation.url_template == "/widgets"
    assert operation.request.query_parameters[0].required is False
    assert operation.policies_xml == policy


def test_reimport_uses_literal_operation_id_matching_rule() -> None:
    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        )
    )
    policy = "<policies><inbound /><backend /><outbound /><on-error /></policies>"
    spec = _spec("find all")
    spec["paths"]["/widgets"]["get"] = spec["paths"]["/widgets"].pop("post")

    with TestClient(app) as client:
        first = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": json.dumps(spec)},
        )
        app.state.gateway_config.apis["catalog"].operations["find-all"].policies_xml = policy
        same_raw_id = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": json.dumps(spec)},
        )
        app.state.gateway_config.apis["catalog"].operations["get-widgets"].name = "Keep this resource"
        spec["paths"]["/widgets"]["get"]["operationId"] = "get-widgets"
        matching_resource_id = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": json.dumps(spec)},
        )

    operations = app.state.gateway_config.apis["catalog"].operations
    assert first.status_code == same_raw_id.status_code == matching_resource_id.status_code == 200
    assert set(operations) == {"get-widgets"}
    assert operations["get-widgets"].name == "Create a widget"
    assert operations["get-widgets"].policies_xml == policy


def test_management_and_terraform_use_the_same_operation_and_schema_projection() -> None:
    spec_text = json.dumps(_spec())
    app = create_app(
        config=GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"))
    )
    with TestClient(app) as client:
        response = client.post(
            "/apim/management/apis/catalog/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"content_format": "openapi+json", "content_value": spec_text},
        )
    management_api = app.state.gateway_config.apis["catalog"]
    tf = {
        "values": {
            "root_module": {
                "resources": [
                    {
                        "address": "azurerm_api_management_api.catalog",
                        "type": "azurerm_api_management_api",
                        "name": "catalog",
                        "values": {
                            "name": "catalog",
                            "path": "catalog",
                            "service_url": "",
                            "import": {"content_format": "openapi+json", "content_value": spec_text},
                        },
                    }
                ]
            }
        }
    }
    terraform_api = import_from_tofu_show_json(tf).config.apis["catalog"]

    assert response.status_code == 200
    assert terraform_api.operations["createwidget"].model_dump(mode="json") == management_api.operations[
        "createwidget"
    ].model_dump(mode="json")
    assert terraform_api.schemas["Widget"].model_dump(mode="json") == management_api.schemas["Widget"].model_dump(
        mode="json"
    )
