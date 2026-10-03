"""Regression tests for the public trace and reload bypasses found in the audit."""

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig, RouteConfig, TenantAccessConfig
from app.main import create_app


def _config():
    return GatewayConfig(
        allow_anonymous=True,
        trace_enabled=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="private-management-key"),
        routes=[RouteConfig(name="echo", path_prefix="/api", upstream_base_url="http://backend")],
    )


def test_unauthenticated_trace_header_does_not_capture_or_disclose_payload():
    app = create_app(
        config=_config(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text="private-backend-payload"))
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api", headers={"X-Apim-Trace": "true"})
        assert response.status_code == 200
        assert not any("trace" in key.lower() for key in response.headers)
        assert app.state.trace_store == {}
        assert client.get("/apim/trace/unknown").status_code == 403


def test_reload_requires_tenant_authorization_even_without_admin_token():
    app = create_app(
        config=_config(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
    )
    with TestClient(app) as client:
        called = []
        app.state.config_reload_fn = lambda: called.append(True) or _config()
        assert client.post("/apim/reload").status_code == 403
        assert called == []
        authorized = client.post("/apim/reload", headers={"X-Apim-Tenant-Key": "private-management-key"})
        assert authorized.status_code == 200
        assert called == [True]


def test_gateway_body_limit_stops_reading_before_consuming_an_unbounded_stream():
    import asyncio
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from app.request_pipeline import _read_body_within_limit

    class StreamingRequest:
        state = SimpleNamespace()
        consumed = 0

        async def stream(self):
            for chunk in [b"abcd", b"efgh", b"must-not-consume"]:
                self.consumed += 1
                yield chunk

    request = StreamingRequest()
    with pytest.raises(HTTPException) as error:
        asyncio.run(_read_body_within_limit(request, GatewayConfig(max_request_body_bytes=5)))
    assert error.value.status_code == 413
    assert request.consumed == 2
