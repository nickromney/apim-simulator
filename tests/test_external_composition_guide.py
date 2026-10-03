from __future__ import annotations

import importlib.util
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig, TenantAccessConfig
from app.main import create_app


def test_source_composition_calls_all_four_services_sequentially_and_null_introspection_fails_closed(monkeypatch):
    monkeypatch.setenv("APIM_TENANT_KEY", "operator")
    spec = importlib.util.spec_from_file_location(
        "external_composition", "examples/apim-policies/external_composition.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests = []

    def backend(request):
        requests.append(request)
        if request.url.port == 1:
            raise httpx.ConnectError("No listener", request=request)
        assert request.method == "GET" and request.url.path.startswith("/api/metrics/")
        query = parse_qs(request.url.query.decode())
        return httpx.Response(
            200,
            json={
                "metric": request.url.path.rsplit("/", 1)[-1],
                "from": query["from"][-1],
                "to": query["to"][-1],
                "value": 42,
            },
        )

    config = GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="operator"))
    with TestClient(
        create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    ) as client:
        result = module.run(client)
        assert result["null_introspection_denied"] == 401
        assert result["trace_preserves_callout_bodies"] is True
        assert client.get("/apim/management/apis/policy-dashboard", headers=module.TENANT).status_code == 404
    expected = ["salesdata", "materiallevels", "throughput", "accidentdata"]
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests[:-1]] == expected * 3
