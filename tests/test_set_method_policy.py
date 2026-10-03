from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig
from app.main import create_app
from app.policy import PolicyRequest, PolicyRuntime, PolicyTraceCollector, apply_inbound, parse_policies_xml


def request() -> PolicyRequest:
    return PolicyRequest("GET", "/echo", {}, {}, {})


@pytest.mark.contract("POLICY-SET-METHOD")
def test_method_transform_reaches_backend_and_updates_context_and_trace() -> None:
    seen = []

    def backend(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.headers["x-effective-method"], req.content, req.headers["content-length"]))
        return httpx.Response(200, content=req.content)

    config = GatewayConfig(
        allow_anonymous=True,
        cache_enabled=True,
        trace_enabled=True,
        trace_allow_unauthenticated=True,
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="http://backend",
                operations={"echo": OperationConfig(name="Echo", method="GET", url_template="/echo")},
                policies_xml="""<policies><inbound><set-method>POST</set-method>
                <set-header name="x-effective-method" exists-action="override"><value>@(context.Request.Method)</value></set-header>
                <set-body>transformed body</set-body></inbound><outbound>
                <set-header name="x-outbound-method" exists-action="override"><value>@(context.Request.Method)</value></set-header>
                </outbound></policies>""",
            )
        },
    )
    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    with TestClient(app) as client:
        for _ in range(2):
            response = client.get("/sample/echo", headers={"X-Apim-Trace": "true"})
            assert response.status_code == 200
            assert response.content == b"transformed body"
            assert response.headers["x-outbound-method"] == "POST"
        trace = next(reversed(app.state.trace_store.values()))
        step = next(step for step in trace["policy_steps"] if step["step"] == "set-method")
        assert step["method"] == "POST"
        assert step["original_method"] == "GET"
    # Changing an inbound GET into a POST must not activate the gateway GET cache.
    assert seen == [("POST", "POST", b"transformed body", "16")] * 2


@pytest.mark.parametrize("section", ["inbound", "on-error"])
def test_set_method_allowed_sections_expression_and_custom_method(section: str) -> None:
    docs = parse_policies_xml(
        f'<policies><{section}><set-method>@(context.Variables["method"])</set-method></{section}></policies>'
    )
    req = request()
    req.variables["method"] = "propfind"
    req.section = section
    runtime = PolicyRuntime(trace=PolicyTraceCollector())
    getattr(docs, section.replace("-", "_"))[0].apply(req, runtime)
    assert req.method == "PROPFIND"
    assert req.body == b""


@pytest.mark.parametrize("section", ["backend", "outbound"])
def test_set_method_rejects_undocumented_sections(section: str) -> None:
    with pytest.raises(HTTPException, match="set-method is not allowed"):
        parse_policies_xml(f"<policies><{section}><set-method>POST</set-method></{section}></policies>")


@pytest.mark.parametrize(
    "xml",
    ["<set-method />", '<set-method method="POST">POST</set-method>', "<set-method><value>POST</value></set-method>"],
)
def test_set_method_rejects_invalid_shape(xml: str) -> None:
    with pytest.raises(HTTPException):
        parse_policies_xml(f"<policies><inbound>{xml}</inbound></policies>")


@pytest.mark.parametrize("method", ["", "GET POST", "GET\r\nInjected: yes", "GÉT"])
def test_set_method_rejects_invalid_runtime_token(method: str) -> None:
    docs = parse_policies_xml(
        '<policies><inbound><set-method>@(context.Variables["method"])</set-method></inbound></policies>'
    )
    req = request()
    req.variables["method"] = method
    with pytest.raises(HTTPException, match="valid HTTP method token"):
        apply_inbound([docs], req)
    assert req.method == "GET"


def test_callout_set_method_does_not_change_parent_method() -> None:
    from app.policy import SendRequest

    req = request()
    req.method = "PATCH"
    node = SendRequest(mode="copy", response_variable_name="result", method="POST", url="http://backend/callout")
    _, method, _ = node._build_callout_request(req, None)
    assert method == "POST"
    assert req.method == "PATCH"


@pytest.mark.parametrize("connection_failure", [True, False])
def test_on_error_observes_effective_method_and_can_change_it(connection_failure: bool) -> None:
    def backend(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        if connection_failure:
            raise httpx.ConnectError("unavailable", request=req)
        return httpx.Response(500, content=b"unavailable")

    config = GatewayConfig(
        allow_anonymous=True,
        apis={
            "sample": ApiConfig(
                name="Sample",
                path="sample",
                upstream_base_url="http://backend",
                operations={"echo": OperationConfig(name="Echo", method="GET", url_template="/echo")},
                policies_xml="""<policies><inbound><set-method>POST</set-method></inbound>
                <backend><forward-request fail-on-error-status-code="true" /></backend>
                <on-error><set-variable name="before-method" value="@(context.Request.Method)" />
                <set-method>PUT</set-method>
                <return-response><set-status code="503" reason="Unavailable" />
                <set-header name="x-before-method" exists-action="override"><value>@(context.Variables["before-method"])</value></set-header>
                <set-header name="x-after-method" exists-action="override"><value>@(context.Request.Method)</value></set-header></return-response></on-error></policies>""",
            )
        },
    )
    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    with TestClient(app) as client:
        response = client.get("/sample/echo")
        assert response.status_code == 503
        assert response.headers["x-before-method"] == "POST"
        assert response.headers["x-after-method"] == "PUT"
