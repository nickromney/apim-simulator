from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import fromstring

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.main import create_app
from app.policy import PolicyRequest, PolicyRuntime, PolicyTraceCollector
from app.policy_transforms import CrossDomain, FindAndReplace, parse_transform_policy


def test_overview_find_replace_runs_at_base_position_and_trace_preserves_bodies() -> None:
    # The overview's API example: cross-domain, parent policies, then xyz->abc.
    # Use a parent body transform to prove <base/> splices at the right position.
    global_xml = '<policies><inbound><find-and-replace from="initial" to="xyz" /></inbound><backend><forward-request /></backend></policies>'
    api_xml = Path("tests/fixtures/policy_guides/api-management-howto-policies/example-02.xml").read_text()
    api_xml = api_xml.replace(
        "</policies>", '<outbound><base /><find-and-replace from="abc" to="done" /></outbound></policies>'
    )
    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        policies_xml=global_xml,
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="http://backend",
                policies_xml=api_xml,
                operations={"echo": OperationConfig(name="Echo", method="POST", url_template="/echo")},
            )
        },
    )
    received = []

    def backend(request):
        assert int(request.headers["Content-Length"]) == len(request.content)
        received.append(request.content)
        return httpx.Response(200, content=request.content, headers={"Content-Type": "text/plain"})

    app = create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    with TestClient(app) as client:
        plain = client.post("/sample/echo", content=b"initial initial")
        token = client.post(
            "/apim/management/gateways/managed/listDebugCredentials",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"apiId": "sample"},
        ).json()["token"]
        traced = client.post("/sample/echo", content=b"initial initial", headers={"Apim-Debug-Authorization": token})
        assert plain.content == traced.content == b"done done"
        assert received == [b"abc abc", b"abc abc"]
        assert int(plain.headers["Content-Length"]) == len(plain.content)
        assert int(traced.headers["Content-Length"]) == len(traced.content)
        trace = client.post(
            "/apim/management/gateways/managed/listTrace",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"traceId": traced.headers["Apim-Trace-Id"]},
        ).json()
        assert trace["status"] == 200


@pytest.mark.parametrize("section", ["inbound", "backend", "outbound", "on-error"])
def test_find_replace_supports_expressions_empty_replacement_and_correct_message(section) -> None:
    req = PolicyRequest(
        method="POST",
        path="/",
        query={},
        headers={},
        variables={"source": "xyz"},
        body=b"xyz-xyz",
        response_body=b"xyz-xyz",
        section=section,
    )
    runtime = PolicyRuntime(trace=PolicyTraceCollector())
    node = parse_transform_policy(fromstring('<find-and-replace from=\'@(context.Variables["source"])\' to="" />'))
    assert isinstance(node, FindAndReplace)
    assert node.apply(req, runtime) is None
    assert (req.response_body if section in {"outbound", "on-error"} else req.body) == b"-"
    assert runtime.trace.steps[-1]["step"] == "find-and-replace"


def test_crossdomain_serves_configured_document_and_does_not_change_normal_requests() -> None:
    node = parse_transform_policy(
        fromstring(
            '<cross-domain><cross-domain-policy><allow-http-request-headers-from domain="example.invalid" headers="x-demo" /></cross-domain-policy></cross-domain>'
        )
    )
    assert isinstance(node, CrossDomain)
    normal = PolicyRequest(method="GET", path="/api/echo", query={}, headers={}, variables={}, body=b"same")
    assert node.apply(normal) is None
    assert normal.body == b"same"
    normal.path = "/crossdomain.xml"
    response = node.apply(normal)
    assert response.status_code == 200
    document = fromstring(response.body)
    assert document.tag == "cross-domain-policy"
    assert document[0].attrib == {"domain": "example.invalid", "headers": "x-demo"}
    normal.method = "POST"
    assert node.apply(normal) is None


@pytest.mark.parametrize(
    "xml",
    [
        '<find-and-replace from="x" />',
        '<find-and-replace from="x" to="y" unknown="true" />',
        '<find-and-replace from="x" to="y"><value>z</value></find-and-replace>',
        "<cross-domain><unrelated /></cross-domain>",
    ],
)
def test_transform_parser_rejects_invalid_shapes(xml) -> None:
    with pytest.raises(HTTPException):
        parse_transform_policy(fromstring(xml))


def test_empty_search_does_not_insert_between_every_body_byte() -> None:
    req = PolicyRequest(method="POST", path="/", query={}, headers={}, variables={}, body=b"unchanged")
    with pytest.raises(HTTPException, match="from must not be empty"):
        FindAndReplace("", "x").apply(req)
    assert req.body == b"unchanged"


def test_global_crossdomain_endpoint_serves_adobe_elements_without_backend() -> None:
    policy = '<policies><inbound><cross-domain><cross-domain-policy><allow-http-request-headers-from domain="example.invalid" headers="x-example" /></cross-domain-policy></cross-domain></inbound></policies>'
    with TestClient(create_app(config=GatewayConfig(policies_xml=policy))) as client:
        response = client.get("/crossdomain.xml")
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("application/xml")
        document = fromstring(response.content)
        assert document.tag == "cross-domain-policy"
        assert document[0].tag == "allow-http-request-headers-from"
        assert document[0].attrib == {"domain": "example.invalid", "headers": "x-example"}


def test_unconfigured_global_crossdomain_endpoint_does_not_publish_api_policy() -> None:
    cfg = GatewayConfig(
        allow_anonymous=True,
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="",
                policies_xml="<policies><inbound><cross-domain /></inbound></policies>",
            )
        },
    )
    with TestClient(create_app(config=cfg)) as client:
        assert client.get("/crossdomain.xml").status_code == 404


def test_global_crossdomain_endpoint_honors_choose_instead_of_publishing_inactive_branch() -> None:
    policy = '<policies><inbound><choose><when condition=\'@(context.Request.Headers.GetValueOrDefault("x-public", "") == "yes")\'><cross-domain><cross-domain-policy><allow-access-from domain="example.invalid" /></cross-domain-policy></cross-domain></when></choose></inbound></policies>'
    with TestClient(create_app(config=GatewayConfig(policies_xml=policy))) as client:
        assert client.get("/crossdomain.xml").status_code == 404
        assert client.get("/crossdomain.xml", headers={"x-public": "yes"}).status_code == 200


def test_standalone_status_and_body_customize_on_error_without_return_response() -> None:
    policy = '<policies><inbound><check-header name="x-required" failed-check-httpcode="403" failed-check-error-message="missing" ignore-case="false" /></inbound><on-error><set-status code="@(400 + 22)" reason="@(context.LastError.Reason)" /><set-body>@(context.LastError.Reason)</set-body><set-header name="x-status"><value>@(context.Response.StatusCode.ToString())</value></set-header></on-error></policies>'
    cfg = GatewayConfig(
        allow_anonymous=True,
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="http://backend",
                policies_xml=policy,
                operations={"echo": OperationConfig(name="Echo", method="POST", url_template="/echo")},
            )
        },
    )
    with TestClient(create_app(config=cfg)) as client:
        response = client.post("/sample/echo", content=b"request remains independent")
        assert response.status_code == 422
        assert response.text == "HeaderNotFound"
        assert response.headers["x-status"] == "422"
        assert int(response.headers["Content-Length"]) == len(response.content)


def test_outbound_standalone_status_changes_status_and_keeps_streamed_body() -> None:
    policy = '<policies><outbound><set-status code="201" reason="Created locally" /></outbound></policies>'
    cfg = GatewayConfig(
        allow_anonymous=True,
        proxy_streaming=True,
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="http://backend",
                policies_xml=policy,
                operations={"echo": OperationConfig(name="Echo", method="POST", url_template="/echo")},
            )
        },
    )
    backend = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=request.content))
    )
    with TestClient(create_app(config=cfg, http_client=backend)) as client:
        response = client.post("/sample/echo", content=b"upstream body")
        assert response.status_code == 201
        assert response.content == b"upstream body"


@pytest.mark.parametrize(
    "xml",
    [
        '<set-status code="400" />',
        '<set-status reason="Missing" />',
        '<set-status code="400" reason="Missing" ignored="true" />',
        '<set-status code="400" reason="Missing"><value>x</value></set-status>',
    ],
)
def test_standalone_status_rejects_missing_required_attributes_and_unknown_shapes(xml) -> None:
    with pytest.raises(HTTPException):
        parse_transform_policy(fromstring(xml))
