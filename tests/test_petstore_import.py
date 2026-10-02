from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig, TenantAccessConfig
from app.main import create_app
from app.openapi_import import parse_api_import

pytestmark = pytest.mark.contract("MGMT-OPENAPI-IMPORT")
FIXTURES = Path(__file__).parent / "fixtures" / "openapi"
SOURCES = {
    2: "https://petstore.swagger.io/v2/swagger.json",
    3: "https://petstore3.swagger.io/api/v3/openapi.json",
}


def _document(version: int) -> str:
    return (FIXTURES / f"petstore{version}-2026-10-02.json").read_text()


def _schema(result, schema_id):
    schema = result.schemas[schema_id]
    return (schema.definitions or schema.components["schemas"])[schema_id]


@pytest.mark.parametrize("version,count", [(2, 20), (3, 19)])
def test_exact_microsoft_tutorial_petstore_specs_import_without_changes(version, count):
    """Frozen public tutorial specs are imported whole, not trimmed to supported operations."""
    raw = _document(version)
    result = parse_api_import(
        content_format="swagger-link-json" if version == 2 else "openapi+json-link",
        content_value=SOURCES[version],
        fetcher=lambda _: raw,
    )
    assert len(result.operations) == count
    assert {op.source_operation_id for op in result.operations} == {
        op["operationId"] for path in json.loads(raw)["paths"].values() for op in path.values()
    }
    assert result.upstream_base_url == (
        "https://petstore.swagger.io/v2" if version == 2 else "https://petstore3.swagger.io/api/v3"
    )
    operations = {op.source_operation_id: op for op in result.operations}
    status = operations["findPetsByStatus"].request.query_parameters[0]
    assert status.required is True
    assert operations["findPetsByStatus"].url_template == "/pet/findByStatus?status={status}"
    tags = operations["findPetsByTags"].request.query_parameters[0]
    assert tags.type == "array"
    assert _schema(result, tags.schema_id) == {"type": "array", "items": {"type": "string"}}
    pet_id = operations["getPetById"].template_parameters[0]
    assert pet_id.type == "integer"
    assert _schema(result, pet_id.schema_id)["format"] == "int64"
    representation = operations["findPetsByStatus"].responses[0].representations[0]
    response_schema = _schema(result, representation.schema_id)
    assert response_schema["type"] == "array"
    assert response_schema["items"]["$ref"].endswith("/Pet")
    if version == 2:
        order_id = operations["getOrderById"].template_parameters[0]
        assert _schema(result, order_id.schema_id)["maximum"] == 10
        forms = operations["uploadFile"].request.representations[0]
        assert forms.content_type == "multipart/form-data"
        assert [parameter.name for parameter in forms.form_parameters] == ["additionalMetadata", "file"]
        assert _schema(result, forms.form_parameters[1].schema_id) == {"type": "string", "format": "binary"}
    else:
        fallback = next(
            response for response in operations["getPetById"].responses if response.status_code == "default"
        )
        assert fallback.description == "Unexpected error"
        assert "local extension" in result.diagnostics[0]


@pytest.mark.parametrize("version", [2, 3])
def test_petstore_management_import_routes_and_preserves_repeated_array_query_values(version):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"query": request.url.params.multi_items()})

    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant")
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app) as client:
        imported = client.post(
            "/apim/management/apis/petstore/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={
                "content_format": "swagger-json" if version == 2 else "openapi+json",
                "content_value": _document(version),
                "upstream_base_url": "http://petstore-backend/api",
                "policies_xml": '<policies><inbound><validate-parameters specified-parameter-action="prevent" '
                'unspecified-parameter-action="ignore" /></inbound><backend><forward-request /></backend>'
                "<outbound /><on-error /></policies>",
            },
        )
        assert imported.status_code == 200, imported.text
        response = client.get("/petstore/pet/findByTags?tags=cat&tags=dog")
        assert response.status_code == 200, response.text
        assert response.json() == {"query": [["tags", "cat"], ["tags", "dog"]]}
        assert requests[-1].url.path == "/api/pet/findByTags"
        assert client.get("/petstore/pet/findByStatus?status=pending").status_code == 200
        assert client.get("/petstore/pet/findByStatus?status=unknown").status_code == 400
        # Without the required query selector, /pet/{petId} matches instead;
        # validation rejects the non-integer "findByStatus" path parameter.
        assert client.get("/petstore/pet/findByStatus").status_code == 400
        assert client.get("/petstore/pet/invalid").status_code == 400
        if version == 2:
            assert client.get("/petstore/store/order/11").status_code == 400
            uploaded = client.post(
                "/petstore/pet/1/uploadImage",
                files={"file": ("pet.txt", b"local-pet", "text/plain")},
                data={"additionalMetadata": "tutorial"},
            )
            assert uploaded.status_code == 200
            assert requests[-1].headers["content-type"].startswith("multipart/form-data;")
            assert b"local-pet" in requests[-1].read()


def test_imported_integer_query_array_validates_every_item():
    document = {
        "openapi": "3.0.4",
        "paths": {
            "/numbers": {
                "get": {
                    "parameters": [
                        {
                            "name": "n",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                        }
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    app = create_app(
        config=GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"))
    )
    with TestClient(app) as client:
        response = client.post(
            "/apim/management/apis/arrays/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={
                "content_format": "openapi+json",
                "content_value": json.dumps(document),
                "policies_xml": '<policies><inbound><validate-parameters specified-parameter-action="prevent" '
                'unspecified-parameter-action="ignore" /><return-response><set-status code="200" reason="OK" />'
                "</return-response></inbound><backend /><outbound /><on-error /></policies>",
            },
        )
        assert response.status_code == 200, response.text
        assert client.get("/arrays/numbers?n=1&n=2").status_code == 200
        assert client.get("/arrays/numbers?n=1").status_code == 200
        assert client.get("/arrays/numbers?n=1&n=bad").status_code == 400
        assert client.get("/arrays/numbers?n=1&n=0").status_code == 400


@pytest.mark.parametrize(
    "status,body,expected", [(200, {"ok": True}, 200), (503, {"error": "down"}, 503), (503, {"ok": True}, 502)]
)
def test_default_response_is_fallback_for_status_and_content_validation(status, body, expected):
    document = {
        "openapi": "3.0.4",
        "paths": {
            "/response": {
                "get": {
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["ok"],
                                        "properties": {"ok": {"type": "boolean"}},
                                    }
                                }
                            },
                        },
                        "default": {
                            "description": "Other responses",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["error"],
                                        "properties": {"error": {"type": "string"}},
                                    }
                                }
                            },
                        },
                    }
                }
            }
        },
    }
    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant")
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body))),
    )
    with TestClient(app) as client:
        imported = client.post(
            "/apim/management/apis/fallback/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={
                "content_format": "openapi+json",
                "content_value": json.dumps(document),
                "upstream_base_url": "http://fallback-backend",
                "policies_xml": "<policies><inbound />"
                "<backend><forward-request /></backend><outbound>"
                '<validate-status-code unspecified-status-code-action="prevent" />'
                '<validate-content unspecified-content-type-action="prevent" max-size="1000" '
                'size-exceeded-action="prevent"><content type="application/json" validate-as="json" '
                'action="prevent" /></validate-content></outbound><on-error /></policies>',
            },
        )
        assert imported.status_code == 200, imported.text
        response = client.get("/fallback/response")
        assert response.status_code == expected, response.text


@pytest.mark.parametrize("status,body", [(200, {"ok": True}), (503, {"error": "fallback"})])
def test_mock_response_prefers_exact_status_then_default_example(status, body):
    document = {
        "openapi": "3.0.4",
        "paths": {
            "/mock": {
                "get": {
                    "responses": {
                        "200": {"description": "OK", "content": {"application/json": {"example": {"ok": True}}}},
                        "default": {
                            "description": "Other",
                            "content": {"application/json": {"example": {"error": "fallback"}}},
                        },
                    }
                }
            }
        },
    }
    app = create_app(
        config=GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"))
    )
    with TestClient(app) as client:
        imported = client.post(
            "/apim/management/apis/fallback/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={
                "content_format": "openapi+json",
                "content_value": json.dumps(document),
                "policies_xml": f'<policies><inbound><mock-response status-code="{status}" '
                'content-type="application/json" /></inbound><backend /><outbound /><on-error /></policies>',
            },
        )
        assert imported.status_code == 200, imported.text
        response = client.get("/fallback/mock")
        assert response.status_code == status
        assert response.json() == body


def test_petstore_inline_array_mock_resolves_component_schema_items():
    app = create_app(
        config=GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"))
    )
    with TestClient(app) as client:
        imported = client.post(
            "/apim/management/apis/petstore/import",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={
                "content_format": "openapi+json",
                "content_value": _document(3),
                "policies_xml": '<policies><inbound><mock-response status-code="200" '
                'content-type="application/json" /></inbound><backend /><outbound /><on-error /></policies>',
            },
        )
        assert imported.status_code == 200, imported.text
        response = client.get("/petstore/pet/findByStatus?status=pending")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["name"] == "doggie"


@pytest.mark.parametrize(
    "schema,location",
    [
        ({"type": "array", "items": {"type": "object"}}, "query"),
        ({"type": "array", "items": {"type": "array", "items": {"type": "string"}}}, "query"),
        ({"type": "array", "items": {"type": "string"}}, "header"),
    ],
)
def test_import_rejects_array_shapes_without_supported_serialization(schema, location):
    document = {
        "openapi": "3.0.4",
        "paths": {"/x": {"get": {"parameters": [{"name": "q", "in": location, "schema": schema}]}}},
    }
    with pytest.raises(ValueError, match="scalar|serialization"):
        parse_api_import(content_format="openapi+json", content_value=json.dumps(document))


def test_swagger_query_array_rejects_csv_encoding():
    document = {
        "swagger": "2.0",
        "paths": {
            "/x": {
                "get": {
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "type": "array",
                            "items": {"type": "string"},
                            "collectionFormat": "csv",
                        }
                    ]
                }
            }
        },
    }
    with pytest.raises(ValueError, match="serialization"):
        parse_api_import(content_format="swagger-json", content_value=json.dumps(document))
