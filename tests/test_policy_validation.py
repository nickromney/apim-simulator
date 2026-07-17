from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config import (
    ApiConfig,
    GatewayConfig,
    OperationConfig,
    OperationParameterConfig,
    OperationRequestMetadataConfig,
    OperationResponseMetadataConfig,
)
from app.policy import (
    PolicyRequest,
    PolicyRuntime,
    apply_inbound,
    apply_outbound_async,
    parse_policies_xml,
)


def _doc(inbound: str = "", outbound: str = ""):
    return parse_policies_xml(
        f"<policies><inbound>{inbound}</inbound><backend /><outbound>{outbound}</outbound><on-error /></policies>"
    )


def _request(
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    variables: dict[str, Any] | None = None,
) -> PolicyRequest:
    return PolicyRequest(
        method="POST",
        path="/api/echo",
        query=query or {},
        headers=headers or {},
        variables=variables or {},
        body=body,
    )


@pytest.mark.contract("POLICY-VALIDATE-CONTENT")
def test_validate_content_prevents_invalid_json() -> None:
    doc = _doc(
        inbound='<validate-content unspecified-content-type-action="prevent">'
        '<content type="application/json" validate-as="json" action="prevent" />'
        "</validate-content>"
    )
    req = _request(body=b"{not json", headers={"content-type": "application/json"})
    blocked = apply_inbound([doc], req)
    assert blocked is not None
    assert blocked.status_code == 400
    assert b"not valid JSON" in blocked.body

    ok_req = _request(body=b'{"ok": true}', headers={"content-type": "application/json"})
    assert apply_inbound([doc], ok_req) is None


@pytest.mark.contract("POLICY-VALIDATE-CONTENT")
def test_validate_content_max_size_and_unspecified_type() -> None:
    doc = _doc(
        inbound='<validate-content unspecified-content-type-action="prevent" '
        'max-size="10" size-exceeded-action="prevent">'
        '<content type="application/json" validate-as="json" action="prevent" />'
        "</validate-content>"
    )
    oversized = _request(body=b'{"a": "0123456789"}', headers={"content-type": "application/json"})
    blocked = apply_inbound([doc], oversized)
    assert blocked is not None
    assert blocked.status_code == 400
    assert b"max-size" in blocked.body

    wrong_type = _request(body=b"<xml />", headers={"content-type": "application/xml"})
    blocked = apply_inbound([doc], wrong_type)
    assert blocked is not None
    assert b"not specified for validation" in blocked.body


@pytest.mark.contract("POLICY-VALIDATE-CONTENT")
def test_validate_content_detect_records_errors_without_blocking() -> None:
    doc = _doc(
        inbound='<validate-content unspecified-content-type-action="ignore" errors-variable-name="contentErrors">'
        '<content type="application/json" validate-as="json" action="detect" />'
        "</validate-content>"
    )
    req = _request(body=b"nope", headers={"content-type": "application/json"})
    assert apply_inbound([doc], req) is None
    errors = req.variables["contentErrors"]
    assert len(errors) == 1
    assert errors[0]["source"] == "validate-content"


def _operation_variables() -> dict[str, Any]:
    return {"api_id": "demo-api", "operation_id": "echo"}


def _operation_config() -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        apis={
            "demo-api": ApiConfig(
                name="Demo",
                path="api",
                upstream_base_url="http://upstream",
                operations={
                    "echo": OperationConfig(
                        name="Echo",
                        method="POST",
                        url_template="/echo",
                        request=OperationRequestMetadataConfig(
                            headers=[OperationParameterConfig(name="x-required-header", required=True, type="string")],
                            query_parameters=[OperationParameterConfig(name="mode", required=True, type="string")],
                        ),
                        responses=[OperationResponseMetadataConfig(status_code=200)],
                    )
                },
            )
        },
    )


@pytest.mark.contract("POLICY-VALIDATE-PARAMETERS")
def test_validate_parameters_requires_declared_parameters() -> None:
    doc = _doc(inbound='<validate-parameters specified-parameter-action="prevent" />')
    runtime = PolicyRuntime(gateway_config=_operation_config())

    missing = _request(variables=_operation_variables())
    blocked = apply_inbound([doc], missing, runtime)
    assert blocked is not None
    assert blocked.status_code == 400
    assert b"x-required-header" in blocked.body

    complete = _request(
        headers={"x-required-header": "1"},
        query={"mode": "fast"},
        variables=_operation_variables(),
    )
    assert apply_inbound([doc], complete, runtime) is None


@pytest.mark.contract("POLICY-VALIDATE-PARAMETERS")
def test_validate_parameters_flags_unspecified_query() -> None:
    doc = _doc(
        inbound='<validate-parameters specified-parameter-action="ignore" unspecified-parameter-action="prevent" />'
    )
    runtime = PolicyRuntime(gateway_config=_operation_config())
    req = _request(
        headers={"x-required-header": "1"},
        query={"mode": "fast", "debug": "1"},
        variables=_operation_variables(),
    )
    blocked = apply_inbound([doc], req, runtime)
    assert blocked is not None
    assert b"debug" in blocked.body


@pytest.mark.contract("POLICY-VALIDATE-STATUS-CODE")
def test_validate_status_code_prevent_mutates_response() -> None:
    doc = _doc(outbound='<validate-status-code unspecified-status-code-action="prevent" />')
    runtime = PolicyRuntime(gateway_config=_operation_config())

    headers: dict[str, str] = {}
    variables = _operation_variables()
    req = PolicyRequest(
        method="POST",
        path="/api/echo",
        query={},
        headers=headers,
        variables=variables,
        response_status_code=418,
        response_headers=headers,
        response_body=b"teapot",
        response_media_type="text/plain",
    )
    asyncio.run(apply_outbound_async([doc], req, runtime))
    assert req.response_status_code == 502
    assert req.response_body == b"Response status code validation failed"


@pytest.mark.contract("POLICY-VALIDATE-STATUS-CODE")
def test_validate_status_code_allows_declared_and_explicit_codes() -> None:
    doc = _doc(
        outbound='<validate-status-code unspecified-status-code-action="prevent">'
        '<status-code code="429" action="ignore" />'
        "</validate-status-code>"
    )
    runtime = PolicyRuntime(gateway_config=_operation_config())

    for status in (200, 429):
        headers: dict[str, str] = {}
        req = PolicyRequest(
            method="POST",
            path="/api/echo",
            query={},
            headers=headers,
            variables=_operation_variables(),
            response_status_code=status,
            response_headers=headers,
            response_body=b"ok",
            response_media_type="text/plain",
        )
        asyncio.run(apply_outbound_async([doc], req, runtime))
        assert req.response_status_code == status
        assert req.response_body == b"ok"
