"""Service-level schemas (APIM's /schemas) through the management API.

validate-content schema-id resolves these, so a schema added here changes
gateway validation without a restart.
"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.main import create_app

HEADERS = {"X-Apim-Tenant-Key": "t1"}
POLICY = (
    '<policies><inbound><base /><validate-content unspecified-content-type-action="prevent" max-size="1024" '
    'size-exceeded-action="prevent"><content type="application/json" validate-as="json" action="prevent" '
    'schema-id="calculation" /></validate-content></inbound><backend><forward-request /></backend>'
    "<outbound><base /></outbound><on-error><base /></on-error></policies>"
)


def _app():
    config = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="t1"),
        apis={
            "calc": ApiConfig(
                name="calc",
                path="calc",
                upstream_base_url="http://upstream",
                policies_xml=POLICY,
                operations={"create": OperationConfig(name="create", method="POST", url_template="/")},
            )
        },
    )
    return create_app(
        config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(201)))
    )


def test_global_schema_lifecycle_drives_validate_content() -> None:
    schema = {"type": "object", "required": ["scenario"], "properties": {"scenario": {"type": "string"}}}
    with TestClient(_app()) as client:
        assert client.get("/apim/management/schemas", headers=HEADERS).json() == []

        created = client.put(
            "/apim/management/schemas/calculation",
            json={"type": "json", "value": json.dumps(schema), "description": "Calculation"},
            headers=HEADERS,
        )
        assert created.status_code == 200
        assert created.json()["id"] == "calculation"
        assert created.json()["resource_id"].endswith("/schemas/calculation")
        assert client.get("/apim/management/schemas/calculation", headers=HEADERS).json()["description"] == (
            "Calculation"
        )

        valid = client.post("/calc/", json={"scenario": "a"})
        invalid = client.post("/calc/", json={"other": "a"})
        assert (valid.status_code, invalid.status_code) == (201, 400)

        deleted = client.delete("/apim/management/schemas/calculation", headers=HEADERS)
        assert deleted.json() == {"deleted": True, "schema_id": "calculation", "remaining": 0}
        assert client.get("/apim/management/schemas/calculation", headers=HEADERS).status_code == 404
        assert client.delete("/apim/management/schemas/calculation", headers=HEADERS).status_code == 404


def test_global_schema_writes_require_tenant_access_and_valid_json() -> None:
    with TestClient(_app()) as client:
        assert client.put("/apim/management/schemas/x", json={"value": "{}"}).status_code in {401, 403}
        rejected = client.put("/apim/management/schemas/x", json={"type": "json", "value": "{not"}, headers=HEADERS)
        assert rejected.status_code == 400
        xml = client.put("/apim/management/schemas/x", json={"type": "xml", "value": "<xs:schema/>"}, headers=HEADERS)
        assert xml.status_code == 200
