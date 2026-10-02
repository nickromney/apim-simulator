from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig, TenantAccessConfig
from app.main import create_app
from scripts import import_openapi


@pytest.mark.parametrize("override", [None, "http://mock-backend:8080/api"])
def test_tutorial_import_script_can_override_http_backend(monkeypatch, capsys, override) -> None:
    monkeypatch.setenv("OPENAPI_SOURCE", "examples/mock-backend/openapi.json")
    monkeypatch.setenv("APIM_API_ID", "tutorial-api")
    monkeypatch.setenv("APIM_TENANT_KEY", "test-tenant")
    monkeypatch.delenv("APIM_UPSTREAM_BASE_URL", raising=False)
    if override is not None:
        monkeypatch.setenv("APIM_UPSTREAM_BASE_URL", override)
    app = create_app(config=GatewayConfig(tenant_access=TenantAccessConfig(enabled=True, primary_key="test-tenant")))
    with TestClient(app) as client:

        def post(url, *, headers, json, timeout):
            return client.post("/apim/management/apis/tutorial-api/import", headers=headers, json=json)

        monkeypatch.setattr(import_openapi.httpx, "post", post)
        assert import_openapi.main() == 0
        api = client.get("/apim/management/apis/tutorial-api", headers={"X-Apim-Tenant-Key": "test-tenant"}).json()
    assert api["upstream_base_url"] == (override or "")
    assert {operation["id"] for operation in api["operations"]} == {"echo", "health"}
