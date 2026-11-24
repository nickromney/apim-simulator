"""xml-to-json and json-to-xml transformation policies.

https://learn.microsoft.com/en-us/azure/api-management/xml-to-json-policy
https://learn.microsoft.com/en-us/azure/api-management/json-to-xml-policy
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import GatewayConfig, RouteConfig
from app.main import create_app
from app.policy import PolicyRequest, apply_inbound_async, apply_outbound_async, parse_policies_xml
from app.policy_xml_json import json_to_xml_document, xml_to_json_document

_CALCULATOR_XML = b'<?xml version="1.0" encoding="utf-8"?><result><value>4</value><broken>false</broken></result>'


@pytest.mark.contract("POLICY-XML-JSON")
def test_xml_to_json_direct_maps_elements_attributes_text_and_repeats() -> None:
    xml = b'<order id="7"><line sku="a">2</line><line sku="b">1</line><note>gift</note><empty /></order>'
    assert xml_to_json_document(xml) == {
        "order": {
            "@id": "7",
            "line": [{"@sku": "a", "#text": "2"}, {"@sku": "b", "#text": "1"}],
            "note": "gift",
            "empty": None,
        }
    }


def test_xml_to_json_always_array_child_elements_wraps_single_children() -> None:
    assert xml_to_json_document(b"<a><b>1</b></a>", always_array_child_elements=True) == {"a": {"b": ["1"]}}


def test_xml_to_json_keeps_namespace_prefixes_and_declarations() -> None:
    xml = b'<s:Envelope xmlns:s="urn:s"><s:Body>ok</s:Body></s:Envelope>'
    assert xml_to_json_document(xml) == {"s:Envelope": {"@xmlns:s": "urn:s", "s:Body": "ok"}}


@pytest.mark.contract("POLICY-XML-JSON")
def test_json_to_xml_matches_microsoft_soap_example() -> None:
    """The documented example, except that v1:QueryItem is kept.

    The page's output drops the v1:QueryItem element its input contains.
    """
    payload = {
        "soapenv:Envelope": {
            "xmlns:soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
            "xmlns:v1": "http://localdomain.com/core/v1",
            "soapenv:Header": {},
            "soapenv:Body": {"v1:QueryList": {"#attrs": {"queryName": "test"}, "v1:QueryItem": {"name": "dummy text"}}},
        }
    }
    assert json_to_xml_document(payload, separator=":", namespace_prefix="xmlns", attribute_block="#attrs") == (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:v1="http://localdomain.com/core/v1"><soapenv:Header /><soapenv:Body>'
        '<v1:QueryList queryName="test"><v1:QueryItem><name>dummy text</name></v1:QueryItem>'
        "</v1:QueryList></soapenv:Body></soapenv:Envelope>"
    )


def test_json_to_xml_wraps_several_properties_and_encodes_names() -> None:
    xml = json_to_xml_document({"a": 1, "b": [1, 2], "c d": None, "t": True, "@id": "x", "<": "&"})
    assert xml == '<Document id="x"><a>1</a><b>1</b><b>2</b><c_x0020_d /><t>true</t><_x003C_>&amp;</_x003C_></Document>'


def test_json_to_xml_default_separator_only_splits_declared_prefixes() -> None:
    payload = {"root": {"xmlns:ns": "urn:n", "ns_item": "1", "snake_case": "2"}}
    assert json_to_xml_document(payload, namespace_prefix="xmlns") == (
        '<root xmlns:ns="urn:n"><ns:item>1</ns:item><snake_case>2</snake_case></root>'
    )


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ('<xml-to-json apply="always" />', "requires kind"),
        ('<xml-to-json kind="direct" />', "requires apply"),
        ('<xml-to-json kind="direct" apply="sometimes" />', "apply must be"),
        ('<json-to-xml apply="content-type-xml" />', "apply must be"),
        ('<json-to-xml apply="always" parse-date="@(true)" />', "parse-date must be"),
        ('<json-to-xml apply="always" mode="x" />', "unsupported attributes"),
    ],
)
def test_transform_attributes_are_validated(policy: str, message: str) -> None:
    with pytest.raises(HTTPException, match=message):
        parse_policies_xml(f"<policies><inbound>{policy}</inbound></policies>")


def test_transforms_are_not_allowed_in_backend() -> None:
    with pytest.raises(HTTPException, match="not allowed in backend"):
        parse_policies_xml('<policies><backend><xml-to-json kind="direct" apply="always" /></backend></policies>')


def _outbound(policy: str, body: bytes, content_type: str, accept: str | None = None) -> PolicyRequest:
    doc = parse_policies_xml(f"<policies><outbound>{policy}</outbound></policies>")
    headers = {"content-type": content_type}
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers=headers,
        variables={"_request_headers": {"accept": accept} if accept else {}},
        response_status_code=200,
        response_headers=headers,
        response_body=body,
    )
    asyncio.run(apply_outbound_async([doc], req))
    return req


def test_consider_accept_header_converts_only_when_json_is_requested() -> None:
    policy = '<xml-to-json kind="direct" apply="always" />'
    assert _outbound(policy, _CALCULATOR_XML, "text/xml").response_body == _CALCULATOR_XML
    converted = _outbound(policy, _CALCULATOR_XML, "text/xml", accept="application/json")
    assert json.loads(converted.response_body) == {"result": {"value": "4", "broken": "false"}}
    assert converted.headers["content-type"].startswith("application/json")


def test_content_type_xml_skips_non_xml_messages() -> None:
    policy = '<xml-to-json kind="direct" apply="content-type-xml" consider-accept-header="false" />'
    assert _outbound(policy, b"plain", "text/plain").response_body == b"plain"


def test_invalid_xml_is_left_unchanged() -> None:
    policy = '<xml-to-json kind="direct" apply="always" consider-accept-header="false" />'
    req = _outbound(policy, b"<not-closed>", "text/xml")
    assert req.response_body == b"<not-closed>"
    assert req.headers["content-type"] == "text/xml"


def test_json_to_xml_in_inbound_rewrites_the_request_body() -> None:
    doc = parse_policies_xml(
        '<policies><inbound><json-to-xml apply="content-type-json" consider-accept-header="false" /></inbound></policies>'
    )
    req = PolicyRequest(
        method="POST",
        path="/",
        query={},
        headers={"content-type": "application/json"},
        variables={},
        body=b'{"order": {"id": 7}}',
    )
    asyncio.run(apply_inbound_async([doc], req))
    assert req.body == b"<order><id>7</id></order>"
    assert req.headers["content-type"].startswith("application/xml")


@pytest.mark.contract("POLICY-XML-JSON")
def test_gateway_converts_xml_backend_response_to_json() -> None:
    """The hands-on lab's calculator step: outbound xml-to-json, ignoring Accept."""
    policy = """\
<policies>
  <inbound><base /></inbound>
  <backend><forward-request /></backend>
  <outbound><base /><xml-to-json kind="direct" apply="always" consider-accept-header="false" /></outbound>
</policies>
"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_CALCULATOR_XML, headers={"content-type": "application/xml"})

    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            routes=[
                RouteConfig(name="calc", path_prefix="/calc", upstream_base_url="http://calc", policies_xml=policy)
            ],
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with TestClient(app) as client:
        resp = client.get("/calc/add?a=2&b=2", headers={"accept": "application/xml"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"result": {"value": "4", "broken": "false"}}
