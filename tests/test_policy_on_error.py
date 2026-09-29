"""Policy errors and expression failures enter the on-error section.

Docs: https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig, RouteConfig
from app.main import create_app

ERROR_HEADERS = """
    <set-header name="err-source"><value>@(context.LastError.Source)</value></set-header>
    <set-header name="err-reason"><value>@(context.LastError.Reason)</value></set-header>
    <set-header name="err-message"><value>@(context.LastError.Message)</value></set-header>
    <set-header name="err-section"><value>@(context.LastError.Section)</value></set-header>
    <set-header name="err-scope"><value>@(context.LastError.Scope)</value></set-header>
    <set-header name="err-status"><value>@(context.Response.StatusCode.ToString())</value></set-header>
"""


def _client(policy: str, *, upstream_calls: list[httpx.Request] | None = None) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if upstream_calls is not None:
            upstream_calls.append(request)
        return httpx.Response(200, text="ok")

    config = GatewayConfig(
        allow_anonymous=True,
        proxy_streaming=False,
        routes=[
            RouteConfig(
                name="on-error-test",
                path_prefix="/api",
                upstream_base_url="http://backend.example:8080",
                policies_xml=policy,
            )
        ],
    )
    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return TestClient(app, raise_server_exceptions=False)


def test_check_header_refusal_enters_on_error_and_returns_its_response() -> None:
    """A check-header refusal is a predefined error: control jumps to on-error."""
    policy = """
    <policies>
      <inbound><check-header name="x-required" failed-check-httpcode="403" failed-check-error-message="Header x-required was not found in the request. Access denied." ignore-case="false" /></inbound>
      <on-error>
        <return-response>
          <set-status code="418" />
          <set-header name="err-source"><value>@(context.LastError.Source)</value></set-header>
          <set-header name="err-reason"><value>@(context.LastError.Reason)</value></set-header>
          <set-header name="err-section"><value>@(context.LastError.Section)</value></set-header>
          <set-header name="err-status"><value>@(context.Response.StatusCode.ToString())</value></set-header>
        </return-response>
      </on-error>
    </policies>
    """
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 418
    assert response.headers["err-source"] == "check-header"
    assert response.headers["err-reason"] == "HeaderNotFound"
    assert response.headers["err-section"] == "inbound"
    assert response.headers["err-status"] == "403"


def test_on_error_without_return_response_keeps_error_response_and_adds_headers() -> None:
    """The learn example sets headers in on-error and the caller still gets the error response."""
    policy = f"""
    <policies>
      <inbound><check-header name="x-required" failed-check-httpcode="403" failed-check-error-message="denied" ignore-case="false" /></inbound>
      <on-error>{ERROR_HEADERS}</on-error>
    </policies>
    """
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 403
    assert response.json() == {"statusCode": 403, "message": "denied"}
    assert response.headers["err-source"] == "check-header"
    assert response.headers["err-message"] == "denied"
    assert response.headers["err-section"] == "inbound"
    assert response.headers["err-status"] == "403"
    assert response.headers["err-scope"] != ""


def test_refusal_without_on_error_is_unchanged() -> None:
    policy = '<policies><inbound><check-header name="x-required" failed-check-httpcode="403" failed-check-error-message="denied" ignore-case="false" /></inbound></policies>'
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 403
    assert response.json() == {"statusCode": 403, "message": "denied"}


def test_return_response_is_not_an_error() -> None:
    policy = f"""
    <policies>
      <inbound><return-response><set-status code="202" /></return-response></inbound>
      <on-error>{ERROR_HEADERS}</on-error>
    </policies>
    """
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 202
    assert "err-source" not in response.headers


def test_rate_limit_exceeded_reports_documented_reason() -> None:
    policy = f"""
    <policies>
      <inbound><rate-limit-by-key calls="1" renewal-period="60" counter-key="k" /></inbound>
      <on-error>{ERROR_HEADERS}</on-error>
    </policies>
    """
    with _client(policy) as client:
        assert client.get("/api/x").status_code == 200
        response = client.get("/api/x")

    assert response.status_code == 429
    assert response.headers["err-source"] == "rate-limit-by-key"
    assert response.headers["err-reason"] == "RateLimitExceeded"


def test_validate_jwt_failure_reports_token_not_present() -> None:
    policy = f"""
    <policies>
      <inbound><validate-jwt header-name="Authorization" require-scheme="Bearer"><openid-config url="http://idp.example/.well-known/openid-configuration" /></validate-jwt></inbound>
      <on-error>{ERROR_HEADERS}</on-error>
    </policies>
    """
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 401
    assert response.headers["err-source"] == "validate-jwt"
    assert response.headers["err-reason"] == "TokenNotPresent"


def test_unknown_backend_id_enters_on_error() -> None:
    """A policy that raises stops processing and jumps to on-error."""
    calls: list[httpx.Request] = []
    policy = f"""
    <policies>
      <inbound><set-backend-service backend-id="missing" /></inbound>
      <on-error>{ERROR_HEADERS}</on-error>
    </policies>
    """
    with _client(policy, upstream_calls=calls) as client:
        response = client.get("/api/x")

    assert response.status_code == 500
    assert response.headers["err-source"] == "set-backend-service"
    assert response.headers["err-section"] == "inbound"
    assert "missing" in response.headers["err-message"]
    assert calls == []


def test_expression_failure_enters_on_error_with_documented_reason() -> None:
    policy = f"""
    <policies>
      <inbound><set-variable name="v" value="@(1 / 0)" /></inbound>
      <on-error>{ERROR_HEADERS}</on-error>
    </policies>
    """
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 500
    assert response.headers["err-source"] == "set-variable"
    assert response.headers["err-reason"] == "ExpressionValueEvaluationFailure"
    assert response.headers["err-section"] == "inbound"


def test_expression_failure_without_on_error_returns_apim_500_body() -> None:
    policy = '<policies><inbound><set-variable name="v" value="@(1 / 0)" /></inbound></policies>'
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 500
    payload = json.loads(response.text)
    assert payload["statusCode"] == 500
    assert payload["message"] == "Internal server error"


def test_outbound_expression_failure_enters_on_error() -> None:
    policy = f"""
    <policies>
      <outbound><set-header name="h"><value>@(1 / 0)</value></set-header></outbound>
      <on-error>{ERROR_HEADERS}</on-error>
    </policies>
    """
    with _client(policy) as client:
        response = client.get("/api/x")

    assert response.status_code == 500
    assert response.headers["err-source"] == "set-header"
    assert response.headers["err-section"] == "outbound"
    assert response.headers["err-reason"] == "ExpressionValueEvaluationFailure"


def test_last_error_reports_the_policy_reason_and_envelope_message() -> None:
    """LastError carries the predefined Reason and the refusal's message, not its JSON body.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    policy = """<policies>
      <inbound><check-header name="x-required" failed-check-httpcode="403" failed-check-error-message="nope" ignore-case="false"><value>ok</value></check-header></inbound>
      <on-error>
        <set-header name="err-reason" exists-action="override"><value>@(context.LastError.Reason)</value></set-header>
        <set-header name="err-message" exists-action="override"><value>@(context.LastError.Message)</value></set-header>
      </on-error>
    </policies>"""
    with _client(policy) as client:
        response = client.get("/api/x", headers={"x-required": "wrong"})

    assert response.status_code == 403
    assert response.headers["err-reason"] == "HeaderValueNotAllowed"
    assert response.headers["err-message"] == "nope"
