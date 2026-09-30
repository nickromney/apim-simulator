"""Outbound policies act on the response, as in Azure API Management.

Gateway-level: create_app + TestClient + httpx.MockTransport.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import (
    ApiConfig,
    GatewayConfig,
    OperationConfig,
    OperationResponseMetadataConfig,
    RouteConfig,
)
from app.main import create_app


def _policy(outbound: str) -> str:
    return f"<policies><inbound /><backend><forward-request /></backend><outbound>{outbound}</outbound><on-error /></policies>"


def _run(
    outbound: str,
    *,
    upstream: httpx.Response | None = None,
    cache: bool = False,
    declared: tuple[int, ...] = (200,),
    path: str = "/api/echo",
    method: str = "GET",
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    hits: list[int] | None = None,
    times: int = 1,
) -> httpx.Response:
    response = upstream or httpx.Response(200, json={"from": "upstream"})
    ops = {
        "echo": OperationConfig(
            name="Echo",
            method=method,
            url_template="/echo",
            responses=[OperationResponseMetadataConfig(status_code=code) for code in declared],
        )
    }
    config = GatewayConfig(
        allow_anonymous=True,
        cache_enabled=cache,
        proxy_streaming=not cache,
        apis={
            "demo": ApiConfig(
                name="Demo",
                path="api",
                upstream_base_url="http://upstream",
                policies_xml=_policy(outbound),
                operations=ops,
            )
        },
    )

    def handler(req: httpx.Request) -> httpx.Response:
        if hits is not None:
            hits.append(1)
        return response

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        for _ in range(times):
            resp = client.request(method, path, content=content, headers=headers)
        return resp


def test_outbound_set_body_replaces_response_body() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/set-body-policy

    In the outbound section set-body sets the response body.
    """
    resp = _run("<set-body>replaced</set-body>")
    assert resp.status_code == 200
    assert resp.text == "replaced"
    assert resp.headers["content-length"] == str(len("replaced"))


def test_outbound_set_body_does_not_touch_the_request_body() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/set-body-policy"""
    seen: list[bytes] = []
    outbound = "<set-body>replaced</set-body>"

    config = GatewayConfig(
        allow_anonymous=True,
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url="http://upstream",
                policies_xml=_policy(outbound),
            )
        ],
    )

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.content)
        return httpx.Response(200, text="upstream-body-that-is-longer")

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        resp = client.post("/api/x", content=b"orig")
    assert seen == [b"orig"]
    assert resp.text == "replaced"
    assert resp.headers["content-length"] == "8"


def test_outbound_return_response_replaces_response() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/return-response-policy

    return-response is allowed in outbound and replaces the response.
    """
    resp = _run(
        '<return-response><set-status code="418" reason="teapot" />'
        '<set-header name="x-out" exists-action="override"><value>1</value></set-header>'
        "<set-body>short</set-body></return-response>"
    )
    assert resp.status_code == 418
    assert resp.text == "short"
    assert resp.headers["x-out"] == "1"
    assert resp.headers["content-length"] == "5"


def test_outbound_return_response_stops_outbound_processing() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/return-response-policy"""
    resp = _run(
        '<return-response><set-status code="200" /><set-body>first</set-body></return-response>'
        "<set-body>second</set-body>"
    )
    assert resp.text == "first"


def test_outbound_mock_response_replaces_response() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/mock-response-policy

    mock-response is allowed in outbound.
    """
    resp = _run('<mock-response status-code="200" content-type="application/json" />')
    assert resp.status_code == 200
    assert resp.text != '{"from":"upstream"}'
    assert "upstream" not in resp.text


def test_outbound_validate_status_code_prevent_returns_502_to_client() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy

    prevent in outbound returns 502 to the client.
    """
    resp = _run(
        '<validate-status-code unspecified-status-code-action="prevent" />',
        upstream=httpx.Response(418, text="teapot"),
    )
    assert resp.status_code == 502
    assert "teapot" not in resp.text


def test_outbound_validate_status_code_prevent_is_what_gets_cached() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy"""
    hits: list[int] = []
    resp = _run(
        '<validate-status-code unspecified-status-code-action="prevent" />',
        upstream=httpx.Response(418, text="teapot"),
        cache=True,
        hits=hits,
        times=2,
    )
    assert hits == [1]
    assert resp.status_code == 502


def test_declared_status_code_ignores_explicit_override() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy

    "If the status code is specified in the API schema, this override doesn't
    take effect." A declared status is valid whatever the override says.
    """
    resp = _run(
        '<validate-status-code unspecified-status-code-action="prevent">'
        '<status-code code="200" action="prevent" /></validate-status-code>',
        upstream=httpx.Response(200, text="fine"),
    )
    assert resp.status_code == 200
    assert resp.text == "fine"


def test_undeclared_status_code_uses_explicit_override() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/validate-status-code-policy"""
    resp = _run(
        '<validate-status-code unspecified-status-code-action="prevent">'
        '<status-code code="418" action="ignore" /></validate-status-code>',
        upstream=httpx.Response(418, text="teapot"),
    )
    assert resp.status_code == 418


def test_outbound_validate_content_checks_the_response_body() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy

    In outbound the policy validates the response, and prevent returns 502.
    """
    resp = _run(
        '<validate-content unspecified-content-type-action="ignore" max-size="100" size-exceeded-action="prevent">'
        '<content type="application/json" validate-as="json" action="prevent" /></validate-content>',
        upstream=httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"}),
    )
    assert resp.status_code == 502
    assert "not json" not in resp.text


def test_outbound_validate_content_ignores_request_body() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy

    A bad request body is not the outbound policy's business.
    """
    resp = _run(
        '<validate-content unspecified-content-type-action="ignore" max-size="100" size-exceeded-action="prevent">'
        '<content type="application/json" validate-as="json" action="prevent" /></validate-content>',
        upstream=httpx.Response(200, json={"ok": True}),
        method="POST",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200


def test_outbound_validate_content_max_size_applies_to_response() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/validate-content-policy"""
    resp = _run(
        '<validate-content max-size="4" size-exceeded-action="prevent" unspecified-content-type-action="ignore" />',
        upstream=httpx.Response(200, text="way too long"),
    )
    assert resp.status_code == 502


def test_outbound_set_body_response_is_what_gets_cached() -> None:
    """The response cache stores the final, post-outbound response."""
    hits: list[int] = []
    config_outbound = "<set-body>final</set-body>"
    second = _run(config_outbound, cache=True, hits=hits, times=2)
    assert hits == [1]
    assert second.text == "final"
    assert second.headers["content-length"] == "5"
