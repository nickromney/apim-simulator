from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from app.policy import PolicyRequest, apply_inbound, parse_policies_xml

CHECK_HEADER_DOC = "https://learn.microsoft.com/en-us/azure/api-management/check-header-policy"
IP_FILTER_DOC = "https://learn.microsoft.com/en-us/azure/api-management/ip-filter-policy"
ERRORS_DOC = "https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies"


def _doc(inbound: str):
    return parse_policies_xml(f"<policies><inbound>{inbound}</inbound><backend /><outbound /><on-error /></policies>")


def _req(headers: dict[str, str] | None = None, variables: dict[str, Any] | None = None) -> PolicyRequest:
    return PolicyRequest(method="GET", path="/", query={}, headers=headers or {}, variables=variables or {})


def _check(headers: dict[str, str], *values: str, ignore_case: str = "false", extra: str = ""):
    children = "".join(f"<value>{v}</value>" for v in values)
    doc = _doc(
        f'<check-header name="X-Key" failed-check-httpcode="401" failed-check-error-message="Not authorized" '
        f'ignore-case="{ignore_case}"{extra}>{children}</check-header>'
    )
    return apply_inbound([doc], _req(headers))


def test_check_header_any_value_child_matches() -> None:
    """Multiple <value> elements: success if any one matches.

    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy
    """
    assert _check({"x-key": "b"}, "a", "b") is None
    denied = _check({"x-key": "c"}, "a", "b")
    assert denied is not None and denied.status_code == 401


def test_check_header_without_value_children_is_presence_only() -> None:
    """The value element is optional; without it only presence is checked.

    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy
    """
    assert _check({"x-key": "anything"}) is None
    assert _check({}) is not None


def test_check_header_ignore_case() -> None:
    """ignore-case controls whether the value comparison is case-insensitive.

    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy
    """
    assert _check({"x-key": "ABC"}, "abc", ignore_case="false") is not None
    assert _check({"x-key": "ABC"}, "abc", ignore_case="true") is None


def test_check_header_failure_is_json_envelope_with_configured_message() -> None:
    """The caller gets failed-check-httpcode and failed-check-error-message in the JSON error envelope.

    The docs say the message is returned in the response body; they do not show the envelope,
    so it follows the {"statusCode","message"} shape the other policies use.
    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    denied = _check({}, "a")
    assert denied is not None
    assert denied.status_code == 401
    assert denied.headers["content-type"] == "application/json"
    assert json.loads(denied.body) == {"statusCode": 401, "message": "Not authorized"}


def test_check_header_attributes_accept_expressions() -> None:
    """name, failed-check-httpcode, failed-check-error-message and ignore-case allow policy expressions.

    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy
    """
    doc = _doc(
        '<check-header name="@(&quot;X-Key&quot;)" failed-check-httpcode="@(403)" '
        'failed-check-error-message="@(&quot;nope&quot;)" ignore-case="@(true)"><value>abc</value></check-header>'
    )
    assert apply_inbound([doc], _req({"x-key": "ABC"})) is None
    denied = apply_inbound([doc], _req({"x-key": "zzz"}))
    assert denied is not None and denied.status_code == 403
    assert json.loads(denied.body)["message"] == "nope"


@pytest.mark.parametrize(
    "xml",
    [
        '<check-header name="x" failed-check-httpcode="401" failed-check-error-message="m" ignore-case="false" '
        'value="a" />',
        '<check-header failed-check-httpcode="401" failed-check-error-message="m" ignore-case="false" />',
        '<check-header name="x" failed-check-error-message="m" ignore-case="false" />',
        '<check-header name="x" failed-check-httpcode="401" ignore-case="false" />',
        '<check-header name="x" failed-check-httpcode="401" failed-check-error-message="m" />',
        '<check-header name="x" failed-check-httpcode="abc" failed-check-error-message="m" ignore-case="false" />',
        '<check-header name="x" failed-check-httpcode="401" failed-check-error-message="m" ignore-case="maybe" />',
    ],
)
def test_check_header_rejects_invalid_configuration(xml: str) -> None:
    """name, failed-check-httpcode, failed-check-error-message and ignore-case are all required; no value attribute.

    https://learn.microsoft.com/en-us/azure/api-management/check-header-policy
    """
    with pytest.raises(HTTPException) as exc:
        _doc(xml)
    assert exc.value.status_code == 500


def _ip(action: str, body: str, ip: Any):
    doc = _doc(f'<ip-filter action="{action}">{body}</ip-filter>')
    variables = {} if ip is None else {"client_ip": ip}
    return apply_inbound([doc], _req(variables=variables))


@pytest.mark.parametrize(
    ("ip", "allowed"),
    [
        ("13.66.140.128", True),
        ("13.66.140.135", True),
        ("13.66.140.143", True),
        ("13.66.140.127", False),
        ("13.66.140.144", False),
        ("2001:db8::5", False),
    ],
)
def test_ip_filter_address_range_is_inclusive(ip: str, allowed: bool) -> None:
    """address-range from..to filters on every address in the range, ends included.

    https://learn.microsoft.com/en-us/azure/api-management/ip-filter-policy
    """
    result = _ip("allow", '<address-range from="13.66.140.128" to="13.66.140.143" />', ip)
    assert (result is None) is allowed


@pytest.mark.parametrize(("ip", "allowed"), [("2001:db8::1", True), ("2001:db8::ff", True), ("2001:db8::100", False)])
def test_ip_filter_ipv6_range(ip: str, allowed: bool) -> None:
    """Ranges work for IPv6 addresses too.

    https://learn.microsoft.com/en-us/azure/api-management/ip-filter-policy
    """
    assert (_ip("allow", '<address-range from="2001:db8::1" to="2001:db8::ff" />', ip) is None) is allowed


def test_ip_filter_forbid_range_and_address() -> None:
    """forbid: requests that match no address or range are allowed; matches are blocked.

    https://learn.microsoft.com/en-us/azure/api-management/ip-filter-policy
    """
    body = '<address>10.0.0.1</address><address-range from="10.1.0.1" to="10.1.0.9" />'
    assert _ip("forbid", body, "10.2.0.1") is None
    blocked = _ip("forbid", body, "10.1.0.5")
    assert blocked is not None
    assert blocked.status_code == 403
    assert blocked.headers["content-type"] == "application/json"
    assert json.loads(blocked.body) == {"statusCode": 403, "message": "Caller IP address is blocked. Access denied."}
    assert _ip("forbid", body, "10.0.0.1") is not None


def test_ip_filter_allow_miss_message_names_caller() -> None:
    """CallerIpNotAllowed: 'Caller IP address {ip-address} is not allowed. Access denied.'

    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    denied = _ip("allow", "<address>10.0.0.1</address>", "10.0.0.2")
    assert denied is not None and denied.status_code == 403
    assert json.loads(denied.body) == {
        "statusCode": 403,
        "message": "Caller IP address 10.0.0.2 is not allowed. Access denied.",
    }


@pytest.mark.parametrize("ip", [None, "", "not-an-ip"])
@pytest.mark.parametrize("action", ["allow", "forbid"])
def test_ip_filter_fails_closed_without_usable_client_ip(action: str, ip: Any) -> None:
    """FailedToParseCallerIP: 'Failed to establish IP address for the caller. Access denied.'

    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    denied = _ip(action, "<address>10.0.0.1</address>", ip)
    assert denied is not None and denied.status_code == 403
    assert json.loads(denied.body) == {
        "statusCode": 403,
        "message": "Failed to establish IP address for the caller. Access denied.",
    }


@pytest.mark.parametrize(
    "xml",
    [
        '<ip-filter action="allow"><cidr>10.0.0.0/8</cidr></ip-filter>',
        '<ip-filter action="allow"><address>10.0.0.0/8</address></ip-filter>',
        '<ip-filter action="allow"><address>nope</address></ip-filter>',
        '<ip-filter action="allow"><address-range from="10.0.0.9" to="10.0.0.1" /></ip-filter>',
        '<ip-filter action="allow"><address-range from="10.0.0.1" to="::1" /></ip-filter>',
        '<ip-filter action="allow"><address-range from="10.0.0.1" /></ip-filter>',
        '<ip-filter action="allow" />',
        "<ip-filter><address>10.0.0.1</address></ip-filter>",
        '<ip-filter action="deny"><address>10.0.0.1</address></ip-filter>',
    ],
)
def test_ip_filter_rejects_invalid_configuration(xml: str) -> None:
    """action is required (allow|forbid); at least one address/address-range; no cidr element.

    https://learn.microsoft.com/en-us/azure/api-management/ip-filter-policy
    """
    with pytest.raises(HTTPException) as exc:
        _doc(xml)
    assert exc.value.status_code == 500
