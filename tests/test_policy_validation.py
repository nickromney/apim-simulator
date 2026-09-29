from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config import (
    ApiConfig,
    GatewayConfig,
    NamedValueConfig,
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
        inbound='<validate-content unspecified-content-type-action="prevent" max-size="100" '
        'size-exceeded-action="prevent">'
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
    assert b"exceeds the limit of 10 bytes" in blocked.body

    wrong_type = _request(body=b"<xml />", headers={"content-type": "application/xml"})
    blocked = apply_inbound([doc], wrong_type)
    assert blocked is not None
    assert blocked.body == b"Unspecified content type application/xml is not allowed."


@pytest.mark.contract("POLICY-VALIDATE-CONTENT")
def test_validate_content_detect_records_errors_without_blocking() -> None:
    doc = _doc(
        inbound='<validate-content unspecified-content-type-action="ignore" max-size="100" '
        'size-exceeded-action="prevent" errors-variable-name="contentErrors">'
        '<content type="application/json" validate-as="json" action="detect" />'
        "</validate-content>"
    )
    req = _request(body=b"nope", headers={"content-type": "application/json"})
    assert apply_inbound([doc], req) is None
    errors = req.variables["contentErrors"]
    assert len(errors) == 1
    assert errors[0]["Name"] == "application/json"
    assert errors[0]["Type"] == "RequestBody"
    assert errors[0]["ValidationRule"] == "IncorrectMessage"
    assert errors[0]["Action"] == "detect"


def test_validate_content_records_apim_error_shape_and_generic_outbound_message() -> None:
    """Validation errors use APIM's structured variable and public response.

    https://learn.microsoft.com/en-us/azure/api-management/validation-policies
    """
    doc = _doc(
        outbound='<validate-content unspecified-content-type-action="prevent" max-size="100" '
        'size-exceeded-action="prevent" errors-variable-name="contentErrors">'
        '<content type="application/json" validate-as="json" action="prevent" />'
        "</validate-content>"
    )
    req = PolicyRequest(
        method="GET",
        path="/api/echo",
        query={},
        headers={},
        variables={},
        response_status_code=200,
        response_headers={"content-type": "application/json"},
        response_body=b"{not json",
    )
    blocked = asyncio.run(apply_outbound_async([doc], req))
    assert blocked is not None
    assert blocked.status_code == 502
    assert (
        blocked.body.decode() == "The request could not be processed due to an internal error. Contact the API owner."
    )
    assert req.variables["contentErrors"] == [
        {
            "Name": "application/json",
            "Type": "ResponseBody",
            "ValidationRule": "IncorrectMessage",
            "Details": "Body of the response is not valid JSON for content type application/json",
            "Action": "prevent",
        }
    ]


def test_validate_content_maps_missing_and_any_content_types() -> None:
    """Content type maps select validation even when the header is absent.

    https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy
    """
    missing_doc = _doc(
        inbound='<validate-content unspecified-content-type-action="prevent" max-size="100" '
        'size-exceeded-action="prevent">'
        '<content-type-map missing-content-type-value="application/json" />'
        '<content validate-as="json" action="prevent" />'
        "</validate-content>"
    )
    missing = _request(body=b"{not json")
    blocked = apply_inbound([missing_doc], missing)
    assert blocked is not None
    assert blocked.status_code == 400

    any_doc = _doc(
        inbound='<validate-content unspecified-content-type-action="ignore" max-size="100" '
        'size-exceeded-action="prevent">'
        '<content-type-map any-content-type-value="application/json" />'
        '<content type="application/json" validate-as="json" action="prevent" />'
        "</validate-content>"
    )
    any_type = _request(body=b"{not json", headers={"content-type": "text/plain"})
    blocked = apply_inbound([any_doc], any_type)
    assert blocked is not None
    assert blocked.status_code == 400

    explicit_doc = _doc(
        inbound='<validate-content unspecified-content-type-action="prevent" max-size="100" '
        'size-exceeded-action="prevent">'
        '<content-type-map any-content-type-value="text/plain">'
        '<type from="application/hal+json" to="application/json" />'
        "</content-type-map>"
        '<content type="application/json" validate-as="json" action="prevent" />'
        "</validate-content>"
    )
    explicit = _request(body=b"{not json", headers={"content-type": "application/hal+json"})
    blocked = apply_inbound([explicit_doc], explicit)
    assert blocked is not None
    assert blocked.status_code == 400


def test_validate_content_accepts_optional_type_and_rejects_unimplemented_options() -> None:
    """Type is optional; unsupported schema overrides must not be ignored.

    https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy
    """
    doc = _doc(
        inbound='<validate-content unspecified-content-type-action="ignore" max-size="100" '
        'size-exceeded-action="prevent"><content validate-as="json" action="ignore" /></validate-content>'
    )
    assert apply_inbound([doc], _request(body=b"plain", headers={"content-type": "text/plain"})) is None

    with pytest.raises(Exception, match="schema-id"):
        parse_policies_xml(
            '<policies><inbound><validate-content unspecified-content-type-action="ignore" max-size="100" '
            'size-exceeded-action="prevent"><content validate-as="json" action="ignore" '
            'schema-id="schema" /></validate-content></inbound></policies>'
        )


def test_validation_policy_required_attributes_and_unimplemented_modes_are_rejected() -> None:
    """Required policy attributes and unsupported validation modes are explicit.

    https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy
    https://learn.microsoft.com/en-us/azure/api-management/validate-parameters-policy
    https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy
    """
    with pytest.raises(Exception, match="max-size"):
        parse_policies_xml(
            '<policies><inbound><validate-content unspecified-content-type-action="ignore" '
            'size-exceeded-action="prevent" /></inbound></policies>'
        )
    with pytest.raises(Exception, match="unspecified-parameter-action"):
        parse_policies_xml(
            '<policies><inbound><validate-parameters specified-parameter-action="ignore" /></inbound></policies>'
        )
    with pytest.raises(Exception, match="unspecified-status-code-action"):
        parse_policies_xml("<policies><outbound><validate-status-code /></outbound></policies>")
    with pytest.raises(Exception, match="Unsupported validate-as: xml"):
        parse_policies_xml(
            '<policies><inbound><validate-content unspecified-content-type-action="ignore" max-size="100" '
            'size-exceeded-action="prevent"><content validate-as="xml" action="ignore" />'
            "</validate-content></inbound></policies>"
        )


def test_validate_content_detects_size_errors_without_interrupting() -> None:
    """Detect records size-limit details while allowing the request through.

    https://learn.microsoft.com/en-us/azure/api-management/validation-policies
    """
    doc = _doc(
        inbound='<validate-content unspecified-content-type-action="ignore" max-size="2" '
        'size-exceeded-action="detect" errors-variable-name="sizeErrors" />'
    )
    req = _request(body=b"long")
    assert apply_inbound([doc], req) is None
    assert req.variables["sizeErrors"] == [
        {
            "Name": "",
            "Type": "RequestBody",
            "ValidationRule": "SizeLimit",
            "Details": "Request's body is 4 bytes long and it exceeds the configured limit of 2 bytes.",
            "Action": "detect",
        }
    ]


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


def test_policy_rejects_a_missing_named_value_reference() -> None:
    """APIM policy named-value references must resolve at policy validation.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties
    """
    with pytest.raises(Exception, match="unknown named value"):
        parse_policies_xml(
            "<policies><inbound><set-body>{{missing}}</set-body></inbound></policies>",
            gateway_config=GatewayConfig(named_values={"present": NamedValueConfig(value="ok")}),
        )


def test_config_validation_rejects_a_missing_named_value_reference() -> None:
    """Config loading validates named values used by policy documents.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties
    """
    from app.config import validate_policy_config

    with pytest.raises(ValueError, match="unknown named value"):
        validate_policy_config(
            GatewayConfig(policies_xml="<policies><inbound><set-body>{{missing}}</set-body></inbound></policies>")
        )


@pytest.mark.contract("POLICY-VALIDATE-PARAMETERS")
def test_validate_parameters_requires_declared_parameters() -> None:
    doc = _doc(
        inbound='<validate-parameters specified-parameter-action="prevent" unspecified-parameter-action="ignore" />'
    )
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


def test_validate_parameters_applies_named_overrides_and_path_parameters() -> None:
    """Named overrides take precedence and path parameters are validated.

    https://learn.microsoft.com/en-us/azure/api-management/validate-parameters-policy
    """
    doc = _doc(
        inbound='<validate-parameters specified-parameter-action="prevent" unspecified-parameter-action="prevent">'
        '<headers specified-parameter-action="prevent" unspecified-parameter-action="prevent">'
        '<parameter name="User-Agent" action="ignore" />'
        "</headers>"
        '<query><parameter name="debug" action="ignore" /></query>'
        '<path specified-parameter-action="prevent" />'
        "</validate-parameters>"
    )
    runtime = PolicyRuntime(gateway_config=_operation_config())
    runtime.gateway_config.apis["demo-api"].operations["echo"].template_parameters.append(
        OperationParameterConfig(name="id", required=True, type="string")
    )
    req = _request(
        headers={"user-agent": "client", "x-required-header": "1"},
        query={"mode": "fast", "debug": "1"},
        variables={**_operation_variables(), "_matched_parameters": {"id": "42"}},
    )
    assert apply_inbound([doc], req, runtime) is None


def test_validate_parameters_does_not_exempt_unspecified_standard_headers() -> None:
    """Unspecified request headers are checked unless explicitly overridden.

    https://learn.microsoft.com/en-us/azure/api-management/validate-parameters-policy
    """
    doc = _doc(
        inbound='<validate-parameters specified-parameter-action="ignore" unspecified-parameter-action="prevent" />'
    )
    runtime = PolicyRuntime(gateway_config=_operation_config())
    req = _request(headers={"host": "example.test"}, variables=_operation_variables())
    blocked = apply_inbound([doc], req, runtime)
    assert blocked is not None
    assert b"host" in blocked.body


def test_validate_parameters_records_detected_header_query_and_path_errors() -> None:
    """Detect captures required and unspecified errors for all parameter kinds.

    https://learn.microsoft.com/en-us/azure/api-management/validation-policies
    """
    doc = _doc(
        inbound='<validate-parameters specified-parameter-action="detect" unspecified-parameter-action="detect" '
        'errors-variable-name="parameterErrors" />'
    )
    runtime = PolicyRuntime(gateway_config=_operation_config())
    runtime.gateway_config.apis["demo-api"].operations["echo"].template_parameters.append(
        OperationParameterConfig(name="id", required=True, type="string")
    )
    req = _request(
        headers={"x-extra": "1"},
        query={"extra": "1"},
        variables=_operation_variables(),
    )
    assert apply_inbound([doc], req, runtime) is None
    errors = req.variables["parameterErrors"]
    assert {item["Type"] for item in errors} == {"RequestHeader", "QueryParameter", "PathParameter"}
    assert all(item["Action"] == "detect" for item in errors)


def test_validate_headers_rejects_unspecified_response_headers() -> None:
    """validate-headers checks response headers against response metadata.

    https://learn.microsoft.com/en-us/azure/api-management/validate-headers-policy
    """
    config = _operation_config()
    config.apis["demo-api"].operations["echo"].responses = [
        OperationResponseMetadataConfig(
            status_code=200,
            headers=[OperationParameterConfig(name="x-allowed", required=False, type="string")],
        )
    ]
    doc = _doc(
        outbound='<validate-headers specified-header-action="ignore" unspecified-header-action="prevent" '
        'errors-variable-name="headerErrors" />'
    )
    req = PolicyRequest(
        method="POST",
        path="/api/echo",
        query={},
        headers={},
        variables=_operation_variables(),
        response_status_code=200,
        response_headers={"x-allowed": "yes", "x-extra": "no"},
    )
    blocked = asyncio.run(apply_outbound_async([doc], req, PolicyRuntime(gateway_config=config)))
    assert blocked is not None
    assert blocked.status_code == 502
    assert (
        blocked.body.decode() == "The request could not be processed due to an internal error. Contact the API owner."
    )
    assert req.variables["headerErrors"][0] == {
        "Name": "x-extra",
        "Type": "ResponseHeader",
        "ValidationRule": "Unspecified",
        "Details": "Unspecified header x-extra is not allowed.",
        "Action": "prevent",
    }


def test_validate_headers_honors_named_override_and_required_headers() -> None:
    """Named response-header overrides take precedence over group actions.

    https://learn.microsoft.com/en-us/azure/api-management/validate-headers-policy
    """
    config = _operation_config()
    config.apis["demo-api"].operations["echo"].responses = [
        OperationResponseMetadataConfig(
            status_code=200,
            headers=[OperationParameterConfig(name="x-required", required=True, type="string")],
        )
    ]
    doc = _doc(
        outbound='<validate-headers specified-header-action="prevent" unspecified-header-action="prevent">'
        '<header name="x-extra" action="ignore" />'
        "</validate-headers>"
    )
    req = PolicyRequest(
        method="POST",
        path="/api/echo",
        query={},
        headers={},
        variables=_operation_variables(),
        response_status_code=200,
        response_headers={"x-extra": "allowed"},
    )
    blocked = asyncio.run(apply_outbound_async([doc], req, PolicyRuntime(gateway_config=config)))
    assert blocked is not None
    assert blocked.status_code == 502
    assert "x-required" not in blocked.body.decode()


@pytest.mark.contract("POLICY-VALIDATE-STATUS-CODE")
def test_validate_status_code_prevent_short_circuits_with_502() -> None:
    doc = _doc(
        outbound='<validate-status-code unspecified-status-code-action="prevent" errors-variable-name="statusErrors" />'
    )
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
    blocked = asyncio.run(apply_outbound_async([doc], req, runtime))
    assert blocked is not None
    assert blocked.status_code == 502
    assert b"teapot" not in blocked.body
    assert variables["statusErrors"] == [
        {
            "Name": "418",
            "Type": "StatusCode",
            "ValidationRule": "Unspecified",
            "Details": "Response status code 418 is not allowed.",
            "Action": "prevent",
        }
    ]


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
