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
def test_product_policy_runs_before_api_scope() -> None:
    config = _subscribed_config(
        products={"p1": ProductConfig(name="p1", policies_xml=PRODUCT_HEADER_POLICY)},
        subscription_products=["p1"],
        route_products=["p1"],
        route_policy=ROUTE_HEADER_POLICY,
    )
    with _client(config) as client:
        resp = client.get("/api/health", headers={"Ocp-Apim-Subscription-Key": "good"})
    assert resp.status_code == 200
    # global -> product -> API: the API-scope override wins.
    assert resp.headers["x-scope"] == "api"


@pytest.mark.contract("POLICY-PRODUCT-SCOPE")
def test_product_policy_uses_granted_product_when_route_has_many() -> None:
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
