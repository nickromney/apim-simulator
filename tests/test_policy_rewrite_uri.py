from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig
from app.main import create_app
from app.policy import parse_policies_xml
from app.urls import http_url


def _app_for_rewrite(
    operation_template: str,
    policy: str,
    *,
    upstream: str = "upstream",
    http_client: httpx.AsyncClient | None = None,
):
    config = GatewayConfig(
        allow_anonymous=True,
        apis={
            "store": ApiConfig(
                name="Store",
                path="api",
                upstream_base_url=http_url(upstream),
                operations={
                    "operation": OperationConfig(
                        name="Operation",
                        method="GET",
                        url_template=operation_template,
                        policies_xml=policy,
                    )
                },
            )
        },
    )
    return create_app(
        config=config,
        http_client=http_client
        or httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
    )


def _policy(template: str, *, copy_unmatched_params: str | None = None) -> str:
    copy_attribute = f' copy-unmatched-params="{copy_unmatched_params}"' if copy_unmatched_params is not None else ""
    escaped_template = template.replace("&", "&amp;")
    return f"""\
<policies>
  <inbound>
    <rewrite-uri template='{escaped_template}'{copy_attribute} />
  </inbound>
  <backend><forward-request /></backend>
  <outbound />
  <on-error />
</policies>
"""


def test_rewrite_uri_documented_basic_url_rewrite() -> None:
    """Example 1 rewrites path parameters and sets the policy query parameters.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    """
    policy = _policy("/v2/US/hardware/{storenumber}/{ordernumber}?City=city&State=state")
    seen: list[str] = []
    app = _app_for_rewrite(
        "/{storenumber}/{ordernumber}?City={city}&State={state}",
        policy,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200))
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/storenumber/ordernumber?City&State")

    assert response.status_code == 200
    assert seen == [f"{http_url('upstream')}/v2/US/hardware/storenumber/ordernumber?City=city&State=state"]


def test_rewrite_uri_documented_copy_unmatched_parameters() -> None:
    """Example 2 copies c=d but excludes a=b bound by the operation template.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    """
    policy = _policy("/put")
    seen: list[str] = []
    app = _app_for_rewrite(
        "/get?a={b}",
        policy,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200))
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/get?a=b&c=d")

    assert response.status_code == 200
    assert seen == [f"{http_url('upstream')}/put?c=d"]


def test_rewrite_uri_documented_drops_unmatched_parameters() -> None:
    """Example 3 drops c=d as well as the operation-template parameter a=b.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    """
    policy = _policy("/put", copy_unmatched_params="false")
    seen: list[str] = []
    app = _app_for_rewrite(
        "/get?a={b}",
        policy,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200))
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/get?a=b&c=d")

    assert response.status_code == 200
    assert seen == [f"{http_url('upstream')}/put"]


def test_rewrite_uri_documented_expression_template() -> None:
    """Example 4 allows a whole-attribute policy expression as the template.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    """
    policy = """\
<policies>
  <inbound>
    <set-variable name="apiVersion" value="/v3" />
    <rewrite-uri template='@("/api" + context.Variables["apiVersion"] + context.Request.Url.Path)' />
  </inbound>
  <backend><forward-request /></backend>
  <outbound />
  <on-error />
</policies>
"""
    seen: list[str] = []
    app = _app_for_rewrite(
        "/original",
        policy,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200))
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/original")

    assert response.status_code == 200
    assert seen == [f"{http_url('upstream')}/api/v3/api/original"]


def test_rewrite_uri_substitutes_path_and_query_template_parameters() -> None:
    """Rewrite placeholders use path and query parameters matched by the operation.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    policy = _policy("/backend/{id}?selected={filter}")
    seen: list[str] = []
    app = _app_for_rewrite(
        "/items/{id}?filter={filter}",
        policy,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200))
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/items/42?filter=active&extra=value")

    assert response.status_code == 200
    assert seen == [f"{http_url('upstream')}/backend/42?selected=active&extra=value"]


def test_rewrite_uri_template_is_relative_to_backend_service_url() -> None:
    """The rewritten path is joined to the backend service URL, not the public route prefix.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    """
    policy = _policy("/put")
    seen: list[str] = []
    app = _app_for_rewrite(
        "/get",
        policy,
        upstream="upstream/service",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200))
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/get")

    assert response.status_code == 200
    assert seen == [f"{http_url('upstream/service')}/put"]


def test_rewrite_uri_copy_expression_is_evaluated() -> None:
    """copy-unmatched-params accepts a policy expression and evaluates it at runtime.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    policy = _policy("/put", copy_unmatched_params="@(false)")
    seen: list[str] = []
    app = _app_for_rewrite(
        "/get?a={b}",
        policy,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: seen.append(str(request.url)) or httpx.Response(200))
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/get?a=b&c=d")

    assert response.status_code == 200
    assert seen == [f"{http_url('upstream')}/put"]


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        (
            '<rewrite-uri template="/put" copy-unmatched-params="sometimes" />',
            "copy-unmatched-params must be true or false",
        ),
        (
            '<rewrite-uri template="/put/@(context.Request.Url.Path)" />',
            "rewrite-uri template must be a complete policy expression",
        ),
    ],
)
def test_rewrite_uri_validates_documented_attribute_rules(xml: str, message: str) -> None:
    """Invalid rewrite-uri attribute forms are rejected instead of being silently accepted.

    https://learn.microsoft.com/en-us/azure/api-management/rewrite-uri-policy
    """
    with pytest.raises(HTTPException, match=message):
        parse_policies_xml(f"<policies><inbound>{xml}</inbound></policies>")
