from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from fastapi import HTTPException

from app.apim_expr import CalloutResponse
from app.backend_pool import apply_backend_credentials
from app.config import BackendConfig, GatewayConfig
from app.policy import (
    PolicyRequest,
    PolicyRuntime,
    apply_inbound,
    apply_inbound_async,
    apply_outbound_async,
    parse_policies_xml,
)


def _request(
    *,
    method: str = "POST",
    body: bytes = b"request-body",
    headers: dict[str, str] | None = None,
    variables: dict[str, object] | None = None,
) -> PolicyRequest:
    return PolicyRequest(
        method=method,
        path="/api/test",
        query={},
        headers=headers or {},
        variables=variables or {},
        body=body,
    )


def _runtime(handler) -> PolicyRuntime:
    return PolicyRuntime(
        gateway_config=GatewayConfig(allow_anonymous=True),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_send_request_new_requires_url_and_method() -> None:
    """send-request requires set-url and set-method unless mode=copy.

    https://learn.microsoft.com/en-us/azure/api-management/send-request-policy
    """
    with pytest.raises(HTTPException, match="requires set-url and set-method"):
        parse_policies_xml(
            '<policies><inbound><send-request mode="new" response-variable-name="r" /></inbound></policies>'
        )


def test_send_request_rejects_invalid_mode() -> None:
    """send-request mode accepts only new or copy.

    https://learn.microsoft.com/en-us/azure/api-management/send-request-policy
    """
    with pytest.raises(HTTPException, match="mode must be new or copy"):
        parse_policies_xml(
            '<policies><inbound><send-request mode="other" response-variable-name="r">'
            "<set-url>https://example.test</set-url><set-method>GET</set-method>"
            "</send-request></inbound></policies>"
        )


def test_send_request_outbound_copy_does_not_copy_body() -> None:
    """Outbound send-request mode=copy does not initialize the request body.

    https://learn.microsoft.com/en-us/azure/api-management/send-request-policy
    """
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, text="ok")

    doc = parse_policies_xml(
        '<policies><outbound><send-request mode="copy" response-variable-name="r" /></outbound></policies>'
    )
    req = _request(body=b"original", variables={"original_request_url": "https://original.test/api/test"})
    req.section = "outbound"
    asyncio.run(
        apply_outbound_async(
            [doc],
            req,
            _runtime(handler),
        )
    )
    assert seen == [b""]


def test_send_request_evaluates_response_variable_name() -> None:
    """send-request evaluates the response-variable-name expression.

    https://learn.microsoft.com/en-us/azure/api-management/send-request-policy
    """

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    doc = parse_policies_xml(
        '<policies><inbound><set-variable name="target" value="result" />'
        '<send-request mode="new" response-variable-name='
        '\'@(context.Variables.GetValueOrDefault("target",""))\'>'
        "<set-url>https://example.test</set-url><set-method>GET</set-method>"
        "</send-request></inbound></policies>"
    )
    req = _request()
    asyncio.run(apply_inbound_async([doc], req, _runtime(handler)))
    assert isinstance(req.variables["result"], CalloutResponse)
    assert '@(context.Variables.GetValueOrDefault("target",""))' not in req.variables


def test_send_request_proxy_is_rejected_instead_of_dropped() -> None:
    """Unsupported send-request proxy configuration is rejected at parse time.

    https://learn.microsoft.com/en-us/azure/api-management/send-request-policy
    """
    with pytest.raises(HTTPException, match="proxy is unsupported"):
        parse_policies_xml(
            '<policies><inbound><send-request mode="new" response-variable-name="r">'
            "<set-url>https://example.test</set-url><set-method>GET</set-method>"
            '<proxy url="http://proxy.test" username="u" password="p" />'
            "</send-request></inbound></policies>"
        )


def test_send_request_managed_identity_sets_bearer_without_marker_headers() -> None:
    """send-request managed identity uses a local bearer token adaptation.

    https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy
    """
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(200, text="ok")

    doc = parse_policies_xml(
        '<policies><inbound><send-request mode="new" response-variable-name="r">'
        "<set-url>https://example.test</set-url><set-method>GET</set-method>"
        '<authentication-managed-identity resource="resource" client-id="client" '
        'output-token-variable-name="token" />'
        "</send-request></inbound></policies>"
    )
    req = _request()
    asyncio.run(apply_inbound_async([doc], req, _runtime(handler)))
    assert seen[0]["authorization"].startswith("Bearer local-apim-mi.")
    assert "x-apim-managed-identity" not in seen[0]
    assert "x-apim-managed-identity-resource" not in seen[0]
    assert req.variables["token"].startswith("local-apim-mi.")


def test_authentication_basic_replaces_existing_authorization() -> None:
    """authentication-basic sets the Authorization header from its credentials.

    https://learn.microsoft.com/en-us/azure/api-management/authentication-basic-policy
    """
    doc = parse_policies_xml(
        '<policies><inbound><authentication-basic username="u" password="p" /></inbound></policies>'
    )
    req = _request(headers={"authorization": "Bearer caller"})
    assert apply_inbound([doc], req) is None
    encoded = base64.b64encode(b"u:p").decode()
    assert req.headers["authorization"] == f"Basic {encoded}"


def test_authentication_managed_identity_sets_bearer_and_output_token() -> None:
    """authentication-managed-identity exposes and forwards its local token.

    https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy
    """
    doc = parse_policies_xml(
        '<policies><inbound><authentication-managed-identity resource="resource" '
        'client-id="client" output-token-variable-name="token" /></inbound></policies>'
    )
    req = _request(headers={"authorization": "Bearer caller"})
    assert apply_inbound([doc], req) is None
    assert req.headers["authorization"].startswith("Bearer local-apim-mi.")
    assert req.variables["token"].startswith("local-apim-mi.")


def test_authentication_certificate_is_parsed_as_local_adaptation() -> None:
    """authentication-certificate identifies a local certificate placeholder.

    https://learn.microsoft.com/en-us/azure/api-management/authentication-certificate-policy
    """
    doc = parse_policies_xml('<policies><inbound><authentication-certificate thumbprint="ABC" /></inbound></policies>')
    req = _request()
    assert apply_inbound([doc], req) is None
    assert req.headers["x-apim-authentication-certificate-thumbprint"] == "ABC"


def test_backend_basic_auth_replaces_client_authorization() -> None:
    """Backend basic auth replaces a caller Authorization header.

    https://learn.microsoft.com/en-us/azure/api-management/authentication-basic-policy
    """
    req = _request(headers={"authorization": "Bearer caller"})
    auth = apply_backend_credentials(
        BackendConfig(url="https://example.test", auth_type="basic", basic_username="u", basic_password="p"),
        req,
        GatewayConfig(allow_anonymous=True),
    )
    assert auth == ("u", "p")
    assert req.headers["authorization"] == "Basic dTpw"


def test_backend_managed_identity_sets_local_bearer() -> None:
    """Backend managed identity forwards Authorization as a bearer token.

    https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy
    """
    req = _request(headers={"authorization": "Bearer caller"})
    apply_backend_credentials(
        BackendConfig(
            url="https://example.test",
            auth_type="managed_identity",
            managed_identity_resource="resource",
        ),
        req,
        GatewayConfig(allow_anonymous=True),
    )
    assert req.headers["authorization"].startswith("Bearer local-apim-mi.")
    assert "x-apim-managed-identity" not in req.headers
    assert "x-apim-managed-identity-resource" not in req.headers


def test_set_backend_service_rejects_service_fabric_attributes() -> None:
    """The simulator rejects unsupported Service Fabric routing attributes.

    https://learn.microsoft.com/en-us/azure/api-management/set-backend-service-policy
    """
    with pytest.raises(HTTPException, match="Service Fabric attributes are unsupported"):
        parse_policies_xml(
            '<policies><inbound><set-backend-service backend-id="b" sf-partition-key="k" /></inbound></policies>'
        )


def test_empty_return_response_defaults_to_200_without_body() -> None:
    """return-response without children returns the default 200 empty response.

    https://learn.microsoft.com/en-us/azure/api-management/return-response-policy
    """
    doc = parse_policies_xml("<policies><inbound><return-response /></inbound></policies>")
    req = _request()
    req.response_status_code = 418
    result = apply_inbound([doc], req)
    assert result is not None
    assert result.status_code == 200
    assert result.body == b""


def test_return_response_starts_from_response_variable_and_preserves_order() -> None:
    """return-response modifies the response variable in document order.

    https://learn.microsoft.com/en-us/azure/api-management/return-response-policy
    https://learn.microsoft.com/en-us/azure/api-management/set-status-policy
    """
    doc = parse_policies_xml(
        '<policies><inbound><return-response response-variable-name="existing">'
        '<set-header name="x-before"><value>@(context.Response.StatusCode.ToString())</value></set-header>'
        '<set-status code="202" reason="Accepted" />'
        "<set-body>@(context.Response.StatusCode.ToString())</set-body>"
        "</return-response></inbound></policies>"
    )
    req = _request(
        variables={"existing": CalloutResponse(status_code=201, headers={"x-existing": "yes"}, content=b"original")}
    )
    result = apply_inbound([doc], req)
    assert result is not None
    assert result.status_code == 202
    assert result.reason == "Accepted"
    assert result.headers["x-existing"] == "yes"
    assert result.headers["x-before"] == "201"
    assert result.body == b"202"
