from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import (
    ApiConfig,
    ApiVersioningScheme,
    ApiVersionSetConfig,
    GatewayConfig,
    OperationConfig,
    RouteConfig,
)
from app.main import create_app
from app.urls import http_url


def test_operation_templates_match_complete_paths_and_prefer_literals() -> None:
    """APIM matches complete operation templates and prefers literals.

    https://learn.microsoft.com/en-us/azure/api-management/add-api-manually
    """
    config = GatewayConfig(
        allow_anonymous=True,
        apis={
            "users": ApiConfig(
                name="Users",
                path="users",
                upstream_base_url=http_url("unused"),
                operations={
                    "parameter": OperationConfig(
                        name="By id",
                        method="GET",
                        url_template="/{id}",
                        upstream_base_url=http_url("parameter"),
                    ),
                    "literal": OperationConfig(
                        name="Me",
                        method="GET",
                        url_template="/me",
                        upstream_base_url=http_url("literal"),
                    ),
                },
            )
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"host": request.url.host, "path": request.url.path})

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        literal = client.get("/users/me")
        parameter = client.get("/users/42")
        missing_operation = client.get("/users")
        missing_operation_slash = client.get("/users/")
        too_deep = client.get("/users/42/orders/7")

    assert literal.status_code == 200
    assert literal.json() == {"host": "literal", "path": "/me"}
    assert parameter.status_code == 200
    assert parameter.json() == {"host": "parameter", "path": "/42"}
    assert missing_operation.status_code == 404
    assert missing_operation_slash.status_code == 404
    assert too_deep.status_code == 404


def test_operation_paths_are_case_insensitive_and_ignore_trailing_slashes() -> None:
    """APIM path matching ignores case and does not distinguish trailing slashes.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-faq
    https://learn.microsoft.com/en-us/answers/questions/1322265/azure-api-management-is-not-validating-case-sensit
    """
    config = GatewayConfig(
        allow_anonymous=True,
        apis={
            "users": ApiConfig(
                name="Users",
                path="Users",
                upstream_base_url=http_url("upstream"),
                operations={
                    "me": OperationConfig(name="Me", method="GET", url_template="/Me"),
                },
            )
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"path": request.url.path})

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        response = client.get("/users/ME/")

    assert response.status_code == 200
    assert response.json() == {"path": "/ME/"}


def test_query_template_is_required_and_matched_parameters_reach_policies() -> None:
    """APIM can discriminate operations with required query template parameters.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-api-import-restrictions
    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    policy = """\
<policies>
  <inbound>
    <set-header name="x-matched-term" exists-action="override">
      <value>@(context.Request.MatchedParameters.GetValueOrDefault("term", ""))</value>
    </set-header>
  </inbound>
  <backend><forward-request /></backend>
  <outbound />
  <on-error />
</policies>
"""
    config = GatewayConfig(
        allow_anonymous=True,
        apis={
            "search": ApiConfig(
                name="Search",
                path="search",
                upstream_base_url=http_url("upstream/root"),
                operations={
                    "find": OperationConfig(
                        name="Find",
                        method="GET",
                        url_template="/find?term={term}",
                        policies_xml=policy,
                    )
                },
            )
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "x-matched-term": request.headers.get("x-matched-term", ""),
                "x-upstream-url": str(request.url),
            },
            json={"ok": True},
        )

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        missing = client.get("/search/find")
        matched = client.get("/search/find?term=alpha&extra=1")

    assert missing.status_code == 404
    assert matched.status_code == 200
    assert matched.headers["x-matched-term"] == "alpha"
    assert matched.headers["x-upstream-url"] == f"{http_url('upstream/root/find')}?term=alpha&extra=1"


def test_wildcard_operation_captures_the_remaining_path() -> None:
    """APIM wildcard operations can capture an arbitrary path remainder.

    https://learn.microsoft.com/en-us/azure/api-management/add-api-manually
    https://learn.microsoft.com/en-us/answers/questions/2279235/azure-apim-best-practices
    """
    config = GatewayConfig(
        allow_anonymous=True,
        apis={
            "proxy": ApiConfig(
                name="Proxy",
                path="proxy",
                upstream_base_url=http_url("upstream"),
                operations={
                    "wildcard": OperationConfig(name="Wildcard", method="GET", url_template="/{*rest}"),
                },
            )
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"path": request.url.path})

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        response = client.get("/proxy/a/b")

    assert response.status_code == 200
    assert response.json() == {"path": "/a/b"}


def test_api_without_operations_has_no_gateway_route() -> None:
    """APIM exposes no operation for a blank API, so calls return 404.

    https://learn.microsoft.com/en-us/azure/api-management/add-api-manually
    """
    config = GatewayConfig(
        allow_anonymous=True,
        apis={
            "empty": ApiConfig(
                name="Empty",
                path="empty",
                upstream_base_url=http_url("upstream"),
            )
        },
    )

    app = create_app(config=config)
    with TestClient(app) as client:
        response = client.get("/empty/anything")

    assert response.status_code == 404


def test_segment_versioning_matches_operations_after_the_version_segment() -> None:
    """Segment versioning selects the API version before operation matching.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-versions
    """
    config = GatewayConfig(
        allow_anonymous=True,
        api_version_sets={
            "public": ApiVersionSetConfig(
                display_name="Public",
                versioning_scheme=ApiVersioningScheme.Segment,
            )
        },
        apis={
            "v1": ApiConfig(
                name="Public v1",
                path="public",
                upstream_base_url=http_url("v1"),
                api_version_set="public",
                api_version="v1",
                operations={"health": OperationConfig(name="Health", method="GET", url_template="/health")},
            ),
            "v2": ApiConfig(
                name="Public v2",
                path="public",
                upstream_base_url=http_url("v2"),
                api_version_set="public",
                api_version="v2",
                operations={"health": OperationConfig(name="Health", method="GET", url_template="/health")},
            ),
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"host": request.url.host, "path": request.url.path})

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        response = client.get("/public/v2/health")

    assert response.status_code == 200
    assert response.json() == {"host": "v2", "path": "/health"}


def test_versioned_api_requires_a_requested_version_unless_original_exists() -> None:
    """APIM requires an identifier for versioned APIs, except for Original.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-versions
    """
    config = GatewayConfig(
        allow_anonymous=True,
        api_version_sets={
            "public": ApiVersionSetConfig(
                display_name="Public",
                versioning_scheme=ApiVersioningScheme.Header,
                version_header_name="X-Api-Version",
            )
        },
        routes=[
            RouteConfig(
                name="v1",
                path_prefix="/api",
                upstream_base_url=http_url("v1"),
                api_version_set="public",
                api_version="v1",
            )
        ],
    )

    app = create_app(config=config)
    with TestClient(app) as client:
        missing = client.get("/api/health")
        unknown = client.get("/api/health", headers={"X-Api-Version": "v9"})

    assert missing.status_code == 404
    assert missing.json() == {"statusCode": 404, "message": "Resource not found"}
    assert unknown.status_code == 404


def test_original_api_version_handles_unversioned_requests() -> None:
    """APIM's Original API answers on the default URL without an identifier.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-versions
    """
    config = GatewayConfig(
        allow_anonymous=True,
        api_version_sets={
            "public": ApiVersionSetConfig(
                display_name="Public",
                versioning_scheme=ApiVersioningScheme.Header,
                version_header_name="X-Api-Version",
            )
        },
        routes=[
            RouteConfig(
                name="original",
                path_prefix="/api",
                upstream_base_url=http_url("original"),
                api_version_set="public",
                api_version=None,
            ),
            RouteConfig(
                name="v1",
                path_prefix="/api",
                upstream_base_url=http_url("v1"),
                api_version_set="public",
                api_version="v1",
            ),
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"host": request.url.host})

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"host": "original"}


def test_api_protocols_allow_forwarded_https_and_reject_disallowed_scheme() -> None:
    """APIM API protocols restrict the schemes accepted by an API.

    https://learn.microsoft.com/en-us/rest/api/apimanagement/apis/create-or-update
    """
    config = GatewayConfig(
        network_security={"allow_simulated_forwarded_headers": True},
        allow_anonymous=True,
        apis={
            "secure": ApiConfig(
                name="Secure",
                path="secure",
                upstream_base_url=http_url("upstream"),
                protocols=["https"],
                operations={"health": OperationConfig(name="Health", url_template="/health")},
            )
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        allowed = client.get("/secure/health", headers={"X-Forwarded-Proto": "https"})
        rejected = client.get("/secure/health", headers={"X-Forwarded-Proto": "http"})

    assert allowed.status_code == 200
    assert rejected.status_code == 404
    assert rejected.json() == {"statusCode": 404, "message": "Resource not found"}
