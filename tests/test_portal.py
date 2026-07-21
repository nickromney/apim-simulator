from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import (
    ApiConfig,
    GatewayConfig,
    GroupConfig,
    OperationConfig,
    PortalConfig,
    ProductConfig,
    ProductState,
    RouteConfig,
    SubscriptionConfig,
    TenantAccessConfig,
    UserConfig,
)
from app.main import create_app
from app.urls import http_url


def _portal_config(**overrides) -> GatewayConfig:
    defaults: dict = {
        "allow_anonymous": True,
        "portal": PortalConfig(enabled=True),
        "tenant_access": TenantAccessConfig(enabled=True, primary_key="t1", secondary_key="t2"),
        "users": {
            "dev-1": UserConfig(id="dev-1", name="Dev One", state="active"),
            "dev-2": UserConfig(id="dev-2", name="Dev Two"),
        },
        "groups": {"partners": GroupConfig(id="partners", name="Partners", users=["dev-2"])},
        "products": {
            "starter": ProductConfig(name="Starter", description="Open to everyone"),
            "partner": ProductConfig(name="Partner", groups=["partners"]),
            "internal": ProductConfig(name="Internal", state=ProductState.NotPublished),
            "gated": ProductConfig(name="Gated", approval_required=True),
            "open": ProductConfig(name="Open", require_subscription=False),
        },
        "apis": {
            "hello": ApiConfig(
                name="hello",
                path="hello",
                upstream_base_url=http_url("upstream"),
                products=["starter", "gated"],
                operations={"greet": OperationConfig(name="greet", method="GET", url_template="/greet")},
            )
        },
        "subscription": SubscriptionConfig(required=False, subscriptions={}),
        "routes": [
            RouteConfig(
                name="hello",
                path_prefix="/hello",
                upstream_base_url=http_url("upstream"),
                upstream_path_prefix="",
                products=["gated"],
            )
        ],
    }
    defaults.update(overrides)
    return GatewayConfig(**defaults)


def _client(config: GatewayConfig) -> TestClient:
    app = create_app(
        config=config,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))),
    )
    return TestClient(app)


def test_portal_endpoints_are_404_when_disabled() -> None:
    config = _portal_config(portal=PortalConfig(enabled=False))
    with _client(config) as client:
        assert client.get("/apim/portal").status_code == 404
        assert client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "dev-1"}).status_code == 404


def test_portal_requires_known_user() -> None:
    config = _portal_config()
    with _client(config) as client:
        missing = client.get("/apim/portal/catalog")
        assert missing.status_code == 401

        unknown = client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "ghost"})
        assert unknown.status_code == 401


def test_portal_page_and_users_are_served_when_enabled() -> None:
    config = _portal_config()
    with _client(config) as client:
        page = client.get("/apim/portal")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert "Developer Portal" in page.text

        users = client.get("/apim/portal/users")
        assert users.status_code == 200
        assert {user["id"] for user in users.json()["users"]} == {"dev-1", "dev-2"}


@pytest.mark.contract("PORTAL-CATALOG-VISIBILITY")
def test_catalog_filters_unpublished_and_group_restricted_products() -> None:
    config = _portal_config()
    with _client(config) as client:
        everyone = client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "dev-1"})
        assert everyone.status_code == 200
        visible = {product["id"] for product in everyone.json()["products"]}
        assert visible == {"starter", "gated", "open"}

        partner = client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "dev-2"})
        partner_visible = {product["id"] for product in partner.json()["products"]}
        assert partner_visible == {"starter", "partner", "gated", "open"}

        starter = next(p for p in everyone.json()["products"] if p["id"] == "starter")
        assert [api["id"] for api in starter["apis"]] == ["hello"]
        assert starter["apis"][0]["operations"][0]["url_template"] == "/greet"
        # Consumer projection must not leak the upstream target.
        assert "upstream_base_url" not in starter["apis"][0]


@pytest.mark.contract("PORTAL-SUBSCRIPTION-SIGNUP")
def test_subscription_signup_approval_loop_end_to_end() -> None:
    config = _portal_config()
    with _client(config) as client:
        created = client.post(
            "/apim/portal/subscriptions",
            json={"product_id": "gated"},
            headers={"X-Apim-Portal-User": "dev-1"},
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["state"] == "submitted"
        key = payload["keys"]["primary"]

        pending = client.get("/hello/greet", headers={"Ocp-Apim-Subscription-Key": key})
        assert pending.status_code == 403

        approved = client.patch(
            f"/apim/management/subscriptions/{payload['id']}",
            json={"state": "active"},
            headers={"X-Apim-Tenant-Key": "t1"},
        )
        assert approved.status_code == 200

        after = client.get("/hello/greet", headers={"Ocp-Apim-Subscription-Key": key})
        assert after.status_code == 200

        mine = client.get("/apim/portal/subscriptions", headers={"X-Apim-Portal-User": "dev-1"})
        assert [sub["state"] for sub in mine.json()["subscriptions"]] == ["active"]

        other = client.get("/apim/portal/subscriptions", headers={"X-Apim-Portal-User": "dev-2"})
        assert other.json()["subscriptions"] == []


@pytest.mark.contract("PORTAL-SUBSCRIPTION-SIGNUP")
def test_subscription_signup_is_active_immediately_without_approval() -> None:
    config = _portal_config()
    with _client(config) as client:
        created = client.post(
            "/apim/portal/subscriptions",
            json={"product_id": "starter"},
            headers={"X-Apim-Portal-User": "dev-1"},
        )
        assert created.status_code == 201
        assert created.json()["state"] == "active"


def test_subscription_signup_error_paths() -> None:
    config = _portal_config()
    with _client(config) as client:
        headers = {"X-Apim-Portal-User": "dev-1"}

        duplicate_target = client.post("/apim/portal/subscriptions", json={"product_id": "starter"}, headers=headers)
        assert duplicate_target.status_code == 201
        duplicate = client.post("/apim/portal/subscriptions", json={"product_id": "starter"}, headers=headers)
        assert duplicate.status_code == 409

        invisible = client.post("/apim/portal/subscriptions", json={"product_id": "partner"}, headers=headers)
        assert invisible.status_code == 404

        unpublished = client.post("/apim/portal/subscriptions", json={"product_id": "internal"}, headers=headers)
        assert unpublished.status_code == 404

        open_product = client.post("/apim/portal/subscriptions", json={"product_id": "open"}, headers=headers)
        assert open_product.status_code == 400


def test_subscription_signup_respects_product_subscriptions_limit() -> None:
    config = _portal_config()
    config.products["capped"] = ProductConfig(name="Capped", subscriptions_limit=0)
    config.apis["hello"].products.append("capped")
    with _client(config) as client:
        blocked = client.post(
            "/apim/portal/subscriptions",
            json={"product_id": "capped"},
            headers={"X-Apim-Portal-User": "dev-1"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "Subscription limit reached for this product"

        allowed = client.post(
            "/apim/portal/subscriptions",
            json={"product_id": "starter"},
            headers={"X-Apim-Portal-User": "dev-1"},
        )
        assert allowed.status_code == 201
