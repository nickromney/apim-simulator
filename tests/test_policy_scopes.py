from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import (
    GatewayConfig,
    ProductConfig,
    RouteConfig,
    Subscription,
    SubscriptionConfig,
    SubscriptionKeyPair,
    TenantAccessConfig,
)
from app.main import create_app
from app.urls import http_url

PRODUCT_HEADER_POLICY = (
    "<policies><inbound /><backend /><outbound>"
    '<set-header name="x-scope" exists-action="override"><value>product</value></set-header>'
    "</outbound><on-error /></policies>"
)

ROUTE_HEADER_POLICY = (
    "<policies><inbound /><backend /><outbound>"
    '<set-header name="x-scope" exists-action="override"><value>api</value></set-header>'
    "</outbound><on-error /></policies>"
)

GLOBAL_INBOUND_APPEND_POLICY = (
    "<policies><inbound>"
    '<set-header name="x-scope-order" exists-action="append"><value>global</value></set-header>'
    "</inbound><backend /><outbound /><on-error /></policies>"
)

API_INBOUND_APPEND_POLICY = (
    "<policies><inbound>"
    '<set-header name="x-scope-order" exists-action="append"><value>api</value></set-header>'
    "</inbound><backend /><outbound /><on-error /></policies>"
)

API_INBOUND_APPEND_WITH_BASE_POLICY = (
    "<policies><inbound>"
    '<set-header name="x-scope-order" exists-action="append"><value>api</value></set-header><base />'
    "</inbound><backend /><outbound /><on-error /></policies>"
)


def _subscribed_config(
    *,
    products: dict[str, ProductConfig],
    subscription_products: list[str],
    route_products: list[str],
    route_policy: str | None = None,
) -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        products=products,
        subscription=SubscriptionConfig(
            required=True,
            subscriptions={
                "demo": Subscription(
                    id="sub1",
                    name="demo",
                    keys=SubscriptionKeyPair(primary="good", secondary="good2"),
                    products=subscription_products,
                )
            },
        ),
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url=http_url("upstream"),
                upstream_path_prefix="/api",
                products=route_products,
                policies_xml=route_policy,
            )
        ],
    )


def _client(config: GatewayConfig) -> TestClient:
    app = create_app(
        config=config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))),
    )
    return TestClient(app)


@pytest.mark.contract("POLICY-PRODUCT-SCOPE")
def test_product_policy_applies_for_authorizing_product() -> None:
    """APIM applies the policy of the product authorizing the request.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies
    """
    config = _subscribed_config(
        products={"p1": ProductConfig(name="p1", policies_xml=PRODUCT_HEADER_POLICY)},
        subscription_products=["p1"],
        route_products=["p1"],
    )
    with _client(config) as client:
        resp = client.get("/api/health", headers={"Ocp-Apim-Subscription-Key": "good"})
    assert resp.status_code == 200
    assert resp.headers["x-scope"] == "product"


@pytest.mark.contract("POLICY-PRODUCT-SCOPE")
def test_product_policy_runs_before_api_when_api_calls_base_first() -> None:
    """With base first, the product section runs before the API's own policies.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    product_policy = (
        "<policies><inbound>"
        '<set-header name="x-scope-order" exists-action="append"><value>product</value></set-header>'
        "</inbound><backend /><outbound /><on-error /></policies>"
    )
    api_policy = (
        "<policies><inbound><base />"
        '<set-header name="x-scope-order" exists-action="append"><value>api</value></set-header>'
        "</inbound><backend /><outbound /><on-error /></policies>"
    )
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-scope-order"))
        return httpx.Response(200, json={"ok": True})

    config = _subscribed_config(
        products={"p1": ProductConfig(name="p1", policies_xml=product_policy)},
        subscription_products=["p1"],
        route_products=["p1"],
        route_policy=api_policy,
    )
    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        resp = client.get("/api/health", headers={"Ocp-Apim-Subscription-Key": "good"})
    assert resp.status_code == 200
    assert seen == ["product,api"]


@pytest.mark.contract("POLICY-PRODUCT-SCOPE")
def test_api_scope_without_base_suppresses_product_scope() -> None:
    """A child section without base does not inherit its product section.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    config = _subscribed_config(
        products={"p1": ProductConfig(name="p1", policies_xml=PRODUCT_HEADER_POLICY)},
        subscription_products=["p1"],
        route_products=["p1"],
        route_policy=ROUTE_HEADER_POLICY,
    )
    with _client(config) as client:
        resp = client.get("/api/health", headers={"Ocp-Apim-Subscription-Key": "good"})
    assert resp.status_code == 200
    assert resp.headers["x-scope"] == "api"


def test_api_base_runs_child_before_parent_inbound_policy() -> None:
    """A base placed after a child policy runs the parent at that position.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-scope-order"])
        return httpx.Response(200, json={"ok": True})

    config = GatewayConfig(
        allow_anonymous=True,
        policies_xml=GLOBAL_INBOUND_APPEND_POLICY,
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url=http_url("upstream"),
                upstream_path_prefix="/api",
                policies_xml=API_INBOUND_APPEND_WITH_BASE_POLICY,
            )
        ],
    )
    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with TestClient(app) as client:
        resp = client.get("/api/health")

    assert resp.status_code == 200
    assert seen == ["api,global"]


def test_api_without_base_drops_global_inbound_policy() -> None:
    """An API policy that omits base does not run the global inbound policy.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["x-scope-order"])
        return httpx.Response(200, json={"ok": True})

    config = GatewayConfig(
        allow_anonymous=True,
        policies_xml=GLOBAL_INBOUND_APPEND_POLICY,
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url=http_url("upstream"),
                upstream_path_prefix="/api",
                policies_xml=API_INBOUND_APPEND_POLICY,
            )
        ],
    )
    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with TestClient(app) as client:
        resp = client.get("/api/health")

    assert resp.status_code == 200
    assert seen == ["api"]


@pytest.mark.contract("POLICY-PRODUCT-SCOPE")
def test_product_policy_uses_granted_product_when_route_has_many() -> None:
    """Only the product context of the subscription contributes product policy.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    other_policy = PRODUCT_HEADER_POLICY.replace("product", "other")
    config = _subscribed_config(
        products={
            "p-other": ProductConfig(name="p-other", policies_xml=other_policy),
            "p-granted": ProductConfig(name="p-granted", policies_xml=PRODUCT_HEADER_POLICY),
        },
        subscription_products=["p-granted"],
        route_products=["p-other", "p-granted"],
    )
    with _client(config) as client:
        resp = client.get("/api/health", headers={"Ocp-Apim-Subscription-Key": "good"})
    assert resp.status_code == 200
    assert resp.headers["x-scope"] == "product"


@pytest.mark.contract("POLICY-PRODUCT-SCOPE")
def test_open_product_policy_applies_without_subscription() -> None:
    """An open product supplies the product context for an anonymous request.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    config = GatewayConfig(
        allow_anonymous=True,
        products={"open": ProductConfig(name="open", require_subscription=False, policies_xml=PRODUCT_HEADER_POLICY)},
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url=http_url("upstream"),
                upstream_path_prefix="/api",
                products=["open"],
            )
        ],
    )
    with _client(config) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.headers["x-scope"] == "product"


@pytest.mark.contract("POLICY-PRODUCT-SCOPE")
def test_management_product_policy_scope_roundtrip() -> None:
    """Product policies can be saved through the simulator management surface.

    https://learn.microsoft.com/en-us/rest/api/apimanagement/product-policy/create-or-update?view=rest-apimanagement-2024-05-01
    """
    config = _subscribed_config(
        products={"p1": ProductConfig(name="p1")},
        subscription_products=["p1"],
        route_products=["p1"],
    )
    config.tenant_access = TenantAccessConfig(enabled=True, primary_key="t1")
    with _client(config) as client:
        updated = client.put(
            "/apim/management/policies/product/p1",
            headers={"X-Apim-Tenant-Key": "t1"},
            json={"xml": PRODUCT_HEADER_POLICY},
        )
        assert updated.status_code == 200

        current = client.get("/apim/management/policies/product/p1", headers={"X-Apim-Tenant-Key": "t1"})
        assert current.status_code == 200
        assert current.json()["xml"] == PRODUCT_HEADER_POLICY

        resp = client.get("/api/health", headers={"Ocp-Apim-Subscription-Key": "good"})
        assert resp.status_code == 200
        assert resp.headers["x-scope"] == "product"

        missing = client.get("/apim/management/policies/product/nope", headers={"X-Apim-Tenant-Key": "t1"})
        assert missing.status_code == 404


def test_management_policy_save_rejects_malformed_xml() -> None:
    """Saving malformed XML returns a clear client error instead of dropping it.

    https://learn.microsoft.com/en-us/rest/api/apimanagement/policy/create-or-update?view=rest-apimanagement-2024-05-01
    """
    config = _subscribed_config(
        products={"p1": ProductConfig(name="p1")},
        subscription_products=["p1"],
        route_products=["p1"],
    )
    config.tenant_access = TenantAccessConfig(enabled=True, primary_key="t1")

    with _client(config) as client:
        response = client.put(
            "/apim/management/policies/product/p1",
            headers={"X-Apim-Tenant-Key": "t1"},
            json={"xml": "<policies><inbound>"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid policies XML"


def test_management_policy_save_rejects_unknown_named_value() -> None:
    """Saving a policy with an unknown named value returns a client error.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties
    """
    config = _subscribed_config(
        products={"p1": ProductConfig(name="p1")},
        subscription_products=["p1"],
        route_products=["p1"],
    )
    config.tenant_access = TenantAccessConfig(enabled=True, primary_key="t1")

    with _client(config) as client:
        response = client.put(
            "/apim/management/policies/product/p1",
            headers={"X-Apim-Tenant-Key": "t1"},
            json={"xml": "<policies><inbound><set-body>{{missing}}</set-body></inbound></policies>"},
        )

    assert response.status_code == 400
    assert "unknown named value" in response.json()["detail"]
