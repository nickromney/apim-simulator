"""Focused tests for APIM forwarding and gateway adaptation behavior."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import GatewayConfig, RouteConfig
from app.main import create_app
from app.policy import ForwardRequest, PolicyRequest, PolicyRuntime, parse_policies_xml
from app.proxy import build_upstream_headers
from app.security import AuthContext


def _config(
    *,
    policy: str | None = None,
    **overrides: object,
) -> GatewayConfig:
    values: dict[str, object] = {
        "allow_anonymous": True,
        "routes": [
            RouteConfig(
                name="forwarding-test",
                path_prefix="/api",
                upstream_base_url="http://backend.example:8080",
                policies_xml=policy,
            )
        ],
        "proxy_streaming": False,
    }
    values.update(overrides)
    return GatewayConfig(**values)


def _app_client(
    config: GatewayConfig,
    transport: httpx.AsyncBaseTransport,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    http_client = httpx.AsyncClient(transport=transport)
    return TestClient(
        create_app(config=config, http_client=http_client),
        raise_server_exceptions=raise_server_exceptions,
    )


def _request(*headers: tuple[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api",
        "raw_path": b"/api",
        "query_string": b"",
        "headers": [(name.lower().encode(), value.encode()) for name, value in headers],
        "client": ("203.0.113.9", 43120),
        "server": ("client.example", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_upstream_uses_backend_host_and_appends_x_forwarded_for() -> None:
    """APIM preserves client X-Forwarded-For and adds the client address.

    Docs: https://learn.microsoft.com/en-us/azure/architecture/best-practices/host-name-preservation
    https://learn.microsoft.com/en-us/azure/api-management/set-header-policy
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    config = _config()
    with _app_client(config, httpx.MockTransport(handler)) as client:
        response = client.get(
            "/api/resource",
            headers={"host": "client.example", "x-forwarded-for": "198.51.100.8"},
        )

    assert response.status_code == 200
    assert len(seen) == 1
    assert seen[0].url.host == "backend.example"
    assert seen[0].headers["host"] == "backend.example:8080"
    assert seen[0].headers["x-forwarded-for"].startswith("198.51.100.8, ")
    assert seen[0].headers["x-forwarded-for"].endswith("testclient")
    assert "forwarded" not in seen[0].headers


def test_identity_headers_are_an_explicit_simulator_adaptation() -> None:
    """APIM does not add the simulator's identity headers unless enabled.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/authentication-authorization-overview
    """
    request = _request(("host", "client.example"))
    auth = AuthContext(
        claims={"sub": "subject-1", "email": "user@example.com", "name": "User One"},
        subscription=None,
        subscription_products=[],
    )

    default_headers = build_upstream_headers(request, auth)
    adapted_headers = build_upstream_headers(
        request,
        auth,
        inject_simulator_identity_headers=True,
    )

    identity_names = {
        "x-apim-user-object-id",
        "x-apim-user-email",
        "x-apim-user-name",
        "x-apim-auth-method",
        "x-ms-client-principal",
        "x-ms-client-principal-name",
    }
    assert identity_names.isdisjoint(default_headers)
    assert identity_names <= adapted_headers.keys()


def test_simulator_response_headers_are_opt_in() -> None:
    """APIM does not emit x-apim-simulator or x-correlation-id by default.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))

    with _app_client(_config(), transport) as client:
        default_response = client.get("/api/resource")

    with _app_client(_config(emit_simulator_response_headers=True), transport) as client:
        adapted_response = client.get("/api/resource")

    assert "x-apim-simulator" not in default_response.headers
    assert "x-correlation-id" not in default_response.headers
    assert adapted_response.headers["x-apim-simulator"] == "apim-simulator"
    assert adapted_response.headers["x-correlation-id"].startswith("corr-")
    uuid.UUID(adapted_response.headers["x-correlation-id"][5:])


def test_forward_request_defaults_and_attributes() -> None:
    """forward-request applies the documented defaults and attribute values.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/forward-request-policy
    """
    docs = parse_policies_xml(
        """
        <policies>
          <backend>
            <forward-request timeout-ms="2500" follow-redirects="true"
              buffer-request-body="true" buffer-response="false"
              fail-on-error-status-code="true" http-version="2or1" />
          </backend>
        </policies>
        """
    )
    node = docs.backend[0]
    request = PolicyRequest(method="GET", path="/api", query={}, headers={}, variables={})

    assert isinstance(node, ForwardRequest)
    node.apply(request, PolicyRuntime())

    assert request.variables["_forward_request_timeout_seconds"] == 2.5
    assert request.variables["_forward_request_follow_redirects"] is True
    assert request.variables["_forward_request_buffer_request_body"] is True
    assert request.variables["_forward_request_buffer_response"] is False
    assert request.variables["_forward_request_fail_on_error_status_code"] is True
    assert request.variables["_forward_request_http_version"] == "2or1"

    default_docs = parse_policies_xml("<policies><backend><forward-request /></backend></policies>")
    default_request = PolicyRequest(method="GET", path="/api", query={}, headers={}, variables={})
    default_docs.backend[0].apply(default_request, PolicyRuntime())
    assert default_request.variables["_forward_request_timeout_seconds"] == 300.0
    assert default_request.variables["_forward_request_follow_redirects"] is False
    assert default_request.variables["_forward_request_buffer_request_body"] is False
    assert default_request.variables["_forward_request_buffer_response"] is True
    assert default_request.variables["_forward_request_fail_on_error_status_code"] is False
    assert default_request.variables["_forward_request_http_version"] == "1"


def test_forward_request_is_backend_only() -> None:
    """forward-request is rejected outside the backend section.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/forward-request-policy
    """
    with pytest.raises(HTTPException, match="backend section"):
        parse_policies_xml("<policies><inbound><forward-request /></inbound></policies>")


def test_forward_request_controls_redirects_and_timeout() -> None:
    """forward-request controls the upstream timeout and redirect behavior.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/forward-request-policy
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, json={"ok": True})

    policy = """
    <policies>
      <backend>
        <forward-request timeout-ms="2500" follow-redirects="true"
          buffer-response="false" http-version="2or1" />
      </backend>
    </policies>
    """
    with _app_client(_config(policy=policy), httpx.MockTransport(handler)) as client:
        response = client.get("/api/resource")

    assert response.status_code == 200
    assert len(seen) == 2
    assert seen[0].extensions["timeout"]["read"] == 2.5


def test_fail_on_error_status_code_enters_on_error() -> None:
    """fail-on-error-status-code sends backend 4xx/5xx responses to on-error.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/forward-request-policy
    """
    policy = """
    <policies>
      <backend>
        <forward-request fail-on-error-status-code="true" />
      </backend>
      <outbound>
        <set-header name="x-outbound-ran"><value>true</value></set-header>
      </outbound>
      <on-error>
        <return-response>
          <set-status code="599" reason="Backend failure" />
          <set-body template="raw">handled</set-body>
        </return-response>
      </on-error>
    </policies>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(418, text="teapot"))
    with _app_client(_config(policy=policy), transport) as client:
        response = client.get("/api/resource")

    assert response.status_code == 599
    assert response.text == "handled"
    assert "x-outbound-ran" not in response.headers


def test_backend_connection_failure_returns_apim_error_shape() -> None:
    """An unhandled backend connection failure returns APIM's generic 500 body.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    Observed response shape: https://learn.microsoft.com/en-us/answers/questions/2265395/vanilla-implementation-of-azure-apim-giving-500-er
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend unavailable", request=request)

    policy = '<policies><backend><forward-request timeout="1" /></backend></policies>'
    config = _config(policy=policy)
    with _app_client(
        config,
        httpx.MockTransport(handler),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/api/resource")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    payload = json.loads(response.text)
    assert payload["statusCode"] == 500
    assert payload["message"] == "Internal server error"
    uuid.UUID(payload["activityId"])


def test_backend_connection_failure_exposes_last_error_reason_to_on_error() -> None:
    """Backend failures expose BackendConnectionFailure through context.LastError.

    Docs: https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("backend unavailable", request=request)

    policy = """
    <policies>
      <backend><forward-request /></backend>
      <on-error>
        <return-response>
          <set-status code="599" reason="Backend failure" />
          <set-header name="error-reason"><value>@(context.LastError.Reason)</value></set-header>
          <set-body template="raw">handled</set-body>
        </return-response>
      </on-error>
    </policies>
    """
    config = _config(policy=policy)
    with _app_client(
        config,
        httpx.MockTransport(handler),
        raise_server_exceptions=False,
    ) as client:
        response = client.get("/api/resource")

    assert response.status_code == 599
    assert response.headers["error-reason"] == "BackendConnectionFailure"
