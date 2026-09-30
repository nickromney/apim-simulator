from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig, TenantAccessConfig
from app.main import create_app


@pytest.mark.contract("MGMT-OPENAPI-IMPORT")
def test_imported_response_example_drives_gateway_mock_response() -> None:
    """Import metadata must reach the mock policy, not just management JSON."""
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Inventory", "version": "1"},
        "paths": {
            "/items": {
                "get": {
                    "operationId": "ListItems",
                    "responses": {
                        "200": {
                            "description": "Inventory",
                            "content": {"application/json": {"example": {"items": ["book"]}}},
                        }
                    },
                }
            }
        },
    }
    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            tenant_access=TenantAccessConfig(enabled=True, primary_key="test-tenant"),
        )
    )
    with TestClient(app) as client:
        imported = client.post(
            "/apim/management/apis/inventory/import",
            headers={"X-Apim-Tenant-Key": "test-tenant"},
            json={
                "path": "inventory",
                "content_format": "openapi+json",
                "content_value": json.dumps(spec),
                "policies_xml": '<policies><inbound><mock-response status-code="200" '
                'content-type="application/json" /></inbound></policies>',
            },
        )
        assert imported.status_code == 200
        mocked = client.get("/inventory/items")

    assert mocked.status_code == 200
    assert mocked.json() == {"items": ["book"]}


@pytest.mark.contract("MGMT-OPENAPI-IMPORT")
def test_imported_integer_enum_is_validated_as_an_integer() -> None:
    spec = {
        "openapi": "3.0.3",
        "paths": {
            "/numbers": {
                "get": {
                    "parameters": [{"in": "query", "name": "n", "schema": {"type": "integer", "enum": [1, 2]}}],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            tenant_access=TenantAccessConfig(enabled=True, primary_key="test-tenant"),
        )
    )
    with TestClient(app) as client:
        imported = client.post(
            "/apim/management/apis/numbers/import",
            headers={"X-Apim-Tenant-Key": "test-tenant"},
            json={
                "content_format": "openapi+json",
                "content_value": json.dumps(spec),
                "policies_xml": '<policies><inbound><validate-parameters specified-parameter-action="prevent" '
                'unspecified-parameter-action="ignore" /><return-response><set-status code="200" reason="OK" />'
                "</return-response></inbound></policies>",
            },
        )
        assert imported.status_code == 200
        valid = client.get("/numbers/numbers?n=2")
        invalid = client.get("/numbers/numbers?n=3")

    assert valid.status_code == 200
    assert invalid.status_code == 400
