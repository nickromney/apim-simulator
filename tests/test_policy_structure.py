from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import (
    ApiConfig,
    ApiSchemaConfig,
    GatewayConfig,
    OperationConfig,
    OperationRepresentationConfig,
    OperationResponseMetadataConfig,
)
from app.policy import PolicyRequest, PolicyRuntime, apply_inbound, parse_policies_xml


def _policy(section: str, body: str) -> str:
    sections = dict.fromkeys(("inbound", "backend", "outbound", "on-error"), "")
    sections[section] = body
    return "<policies>" + "".join(f"<{name}>{value}</{name}>" for name, value in sections.items()) + "</policies>"


@pytest.mark.parametrize(
    ("section", "element"),
    (
        (
            "outbound",
            '<check-header name="x" failed-check-httpcode="403" failed-check-error-message="no" ignore-case="false" />',
        ),
        ("outbound", '<set-query-parameter name="x"><value>1</value></set-query-parameter>'),
        (
            "outbound",
            '<validate-parameters specified-parameter-action="ignore" unspecified-parameter-action="ignore" />',
        ),
        ("backend", '<mock-response status-code="200" />'),
    ),
)
def test_policy_sections_match_apim_usage(section: str, element: str) -> None:
    """Policy reference Usage sections reject policies in unsupported sections.

    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy
    https://learn.microsoft.com/en-us/azure/api-management/set-query-parameter-policy
    https://learn.microsoft.com/en-us/azure/api-management/validate-parameters-policy
    https://learn.microsoft.com/en-us/azure/api-management/mock-response-policy
    """
    with pytest.raises(HTTPException, match="not allowed in"):
        parse_policies_xml(_policy(section, element))


def test_choose_requires_when_and_condition_but_accepts_boolean_constants() -> None:
    """choose requires one or more conditional when elements.

    https://learn.microsoft.com/en-us/azure/api-management/choose-policy
    """
    with pytest.raises(HTTPException, match="requires at least one when"):
        parse_policies_xml(_policy("inbound", "<choose />"))
    with pytest.raises(HTTPException, match="requires condition"):
        parse_policies_xml(_policy("inbound", "<choose><when /></choose>"))

    true_doc = parse_policies_xml(
        _policy(
            "inbound", '<choose><when condition="true"><set-variable name="selected" value="yes" /></when></choose>'
        )
    )
    false_doc = parse_policies_xml(
        _policy(
            "inbound",
            '<choose><when condition="false"><set-variable name="selected" value="no" /></when>'
            '<otherwise><set-variable name="selected" value="otherwise" /></otherwise></choose>',
        )
    )
    true_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    false_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    assert apply_inbound([true_doc], true_req) is None
    assert apply_inbound([false_doc], false_req) is None
    assert true_req.variables["selected"] == "yes"
    assert false_req.variables["selected"] == "otherwise"


@pytest.mark.parametrize(
    "fragment",
    (
        "<policies><inbound><set-header name='x' /></inbound></policies>",
        "<base />",
        "<include-fragment fragment-id='nested' />",
    ),
)
def test_policy_fragments_reject_sections_base_and_nested_fragments(fragment: str) -> None:
    """Policy fragments cannot contain sections, base, or another include-fragment.

    https://learn.microsoft.com/en-us/azure/api-management/policy-fragments
    """
    with pytest.raises(HTTPException, match="policy fragment"):
        parse_policies_xml(
            _policy("inbound", '<include-fragment fragment-id="fragment" />'),
            policy_fragments={"fragment": fragment},
        )


def test_include_fragment_requires_only_fragment_id() -> None:
    """include-fragment requires its fragment-id attribute.

    https://learn.microsoft.com/en-us/azure/api-management/include-fragment-policy
    """
    with pytest.raises(HTTPException, match="fragment-id"):
        parse_policies_xml(
            _policy("inbound", '<include-fragment name="fragment" />'), policy_fragments={"fragment": ""}
        )


def test_mock_response_does_not_fallback_to_another_status_or_content_type() -> None:
    """mock-response returns no content when no matching example or schema exists.

    https://learn.microsoft.com/en-us/azure/api-management/mock-response-policy
    """
    config = GatewayConfig(
        apis={
            "api": ApiConfig(
                name="API",
                path="api",
                upstream_base_url="http://backend",
                operations={
                    "op": OperationConfig(
                        name="Operation",
                        url_template="/op",
                        responses=[
                            OperationResponseMetadataConfig(
                                status_code=200,
                                representations=[
                                    OperationRepresentationConfig(
                                        content_type="application/json",
                                        examples=[{"name": "example", "value": {"ok": True}}],
                                    )
                                ],
                            )
                        ],
                    )
                },
            )
        }
    )
    doc = parse_policies_xml(_policy("inbound", '<mock-response status-code="201" content-type="text/plain" />'))
    req = PolicyRequest(
        method="GET", path="/api/op", query={}, headers={}, variables={"api_id": "api", "operation_id": "op"}
    )
    response = apply_inbound([doc], req, PolicyRuntime(gateway_config=config))
    assert response is not None
    assert response.status_code == 201
    assert response.body == b""


def test_mock_response_generates_a_simple_json_schema_sample() -> None:
    """mock-response generates a sample from a schema when no example exists.

    https://learn.microsoft.com/en-us/azure/api-management/mock-response-policy
    """
    config = GatewayConfig(
        apis={
            "api": ApiConfig(
                name="API",
                path="api",
                upstream_base_url="http://backend",
                schemas={
                    "Pet": ApiSchemaConfig(
                        content_type="application/json",
                        definitions={
                            "Pet": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                                "required": ["name"],
                            }
                        },
                    )
                },
                operations={
                    "op": OperationConfig(
                        name="Operation",
                        url_template="/op",
                        responses=[
                            OperationResponseMetadataConfig(
                                status_code=200,
                                representations=[
                                    OperationRepresentationConfig(
                                        content_type="application/json",
                                        schema_id="Pet",
                                    )
                                ],
                            )
                        ],
                    )
                },
            )
        }
    )
    doc = parse_policies_xml(_policy("inbound", '<mock-response content-type="application/json" />'))
    req = PolicyRequest(
        method="GET", path="/api/op", query={}, headers={}, variables={"api_id": "api", "operation_id": "op"}
    )
    response = apply_inbound([doc], req, PolicyRuntime(gateway_config=config))
    assert response is not None
    assert response.body == b'{"name": "", "age": 0}'


@pytest.mark.parametrize(
    "element",
    (
        '<set-body template="liquid">ignored</set-body>',
        '<set-body xsi-nil="null">ignored</set-body>',
        '<set-body parse-date="false">ignored</set-body>',
    ),
)
def test_set_body_rejects_unimplemented_transform_options(element: str) -> None:
    """Unsupported set-body transformation modes fail during policy parsing.

    https://learn.microsoft.com/en-us/azure/api-management/set-body-policy
    """
    with pytest.raises(HTTPException, match="unsupported"):
        parse_policies_xml(_policy("inbound", element))


def test_set_variable_requires_value_attribute() -> None:
    """set-variable requires a value attribute.

    https://learn.microsoft.com/en-us/azure/api-management/set-variable-policy
    """
    with pytest.raises(HTTPException, match="requires value"):
        parse_policies_xml(_policy("inbound", '<set-variable name="missing" />'))


def test_policy_document_rejects_duplicate_singleton_policies() -> None:
    """APIM limits rate-limit, quota, and cors to one policy per definition.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    https://learn.microsoft.com/en-us/azure/api-management/quota-policy
    https://learn.microsoft.com/en-us/azure/api-management/cors-policy
    """
    cases = (
        '<rate-limit calls="1" renewal-period="60" /><rate-limit calls="1" renewal-period="60" />',
        '<quota calls="1" renewal-period="60" /><quota calls="1" renewal-period="60" />',
        "<cors><allowed-origins><origin>*</origin></allowed-origins></cors>"
        "<cors><allowed-origins><origin>*</origin></allowed-origins></cors>",
    )
    for elements in cases:
        with pytest.raises(HTTPException, match="only once"):
            parse_policies_xml(_policy("inbound", elements))


def test_backend_section_accepts_only_one_policy_element() -> None:
    """The APIM backend policy section can contain only one policy element.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    with pytest.raises(HTTPException, match="only one policy element"):
        parse_policies_xml(_policy("backend", "<forward-request /><set-header name='x' />"))


@pytest.mark.parametrize(
    ("section", "policy"),
    [
        ("on-error", '<validate-status-code unspecified-status-code-action="detect" />'),
        ("on-error", '<emit-metric name="m"><dimension name="API ID" /></emit-metric>'),
        ("on-error", '<cache-lookup-value key="k" variable-name="v" />'),
        ("on-error", '<cache-store-value key="k" value="v" duration="10" />'),
    ],
)
def test_policies_documented_for_on_error_are_accepted_there(section: str, policy: str) -> None:
    """The Usage sections of these policies list on-error.

    https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy
    https://learn.microsoft.com/en-us/azure/api-management/emit-metric-policy
    https://learn.microsoft.com/en-us/azure/api-management/cache-lookup-value-policy
    https://learn.microsoft.com/en-us/azure/api-management/cache-store-value-policy
    """
    parse_policies_xml(f"<policies><{section}>{policy}</{section}></policies>")


def test_llm_emit_token_metric_is_inbound_only() -> None:
    """llm-emit-token-metric lists only the inbound section.

    https://learn.microsoft.com/en-us/azure/api-management/llm-emit-token-metric-policy
    """
    policy = '<llm-emit-token-metric><dimension name="API ID" /></llm-emit-token-metric>'
    with pytest.raises(HTTPException):
        parse_policies_xml(f"<policies><outbound>{policy}</outbound></policies>")
