from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import (
    ApiConfig,
    GatewayConfig,
    ProductConfig,
    ProductState,
    Subscription,
    SubscriptionConfig,
    SubscriptionKeyPair,
    TenantAccessConfig,
)
from app.main import create_app

HEADERS = {"X-Apim-Tenant-Key": "product-authoring-test"}


def _config(**kwargs) -> GatewayConfig:
    return GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key=HEADERS["X-Apim-Tenant-Key"]), **kwargs
    )


def test_new_management_product_defaults_unpublished_and_projects_subscription_settings(monkeypatch) -> None:
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    with TestClient(create_app(config=_config())) as client:
        response = client.put(
            "/apim/management/products/starter",
            headers=HEADERS,
            json={"name": "Starter", "subscriptions_limit": 2, "terms": "Accept the starter terms."},
        )
        assert response.status_code == 200
        assert response.json()["state"] == "not_published"
        assert response.json()["require_subscription"] is True
        assert response.json()["subscriptions_limit"] == 2
        assert response.json()["terms"] == "Accept the starter terms."
        assert client.get("/apim/management/products/starter", headers=HEADERS).json() == response.json()
        published = client.put("/apim/management/products/starter", headers=HEADERS, json={"state": "published"})
        assert published.status_code == 200
        assert published.json()["name"] == "Starter"
        assert published.json()["state"] == "published"
        assert published.json()["terms"] == "Accept the starter terms."
        assert published.json()["subscriptions_limit"] == 2
        missing_name = client.put("/apim/management/products/unnamed", headers=HEADERS, json={"state": "published"})
        assert missing_name.status_code == 400
        assert client.get("/apim/management/products/unnamed", headers=HEADERS).status_code == 404


def test_product_metadata_update_preserves_policy_access_settings_and_subscriptions(monkeypatch) -> None:
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    policy = '<policies><inbound><set-header name="X-Product" exists-action="override"><value>Starter</value></set-header></inbound></policies>'
    config = _config(
        products={
            "starter": ProductConfig(
                name="Starter",
                description="Original description",
                state=ProductState.Published,
                approval_required=True,
                subscriptions_limit=3,
                terms="Product terms",
                groups=["developers"],
                tags=["featured"],
                policies_xml=policy,
            )
        },
        subscription=SubscriptionConfig(
            subscriptions={
                "starter-sub": Subscription(
                    id="starter-sub",
                    name="Existing subscription",
                    keys=SubscriptionKeyPair(primary="primary-test", secondary="secondary-test"),
                    products=["starter"],
                )
            }
        ),
    )
    with TestClient(create_app(config=config)) as client:
        response = client.put("/apim/management/products/starter", headers=HEADERS, json={"name": "Renamed"})
        assert response.status_code == 200
        product = client.app.state.gateway_config.products["starter"]
        assert product == config.products["starter"].model_copy(update={"name": "Renamed"})
        assert product.policies_xml == policy
        assert response.json()["subscription_count"] == 1
        assert client.app.state.gateway_config.subscription.subscriptions == config.subscription.subscriptions


def test_product_update_can_clear_terms_and_limit_explicitly(monkeypatch) -> None:
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    config = _config(products={"starter": ProductConfig(name="Starter", subscriptions_limit=2, terms="Terms")})
    with TestClient(create_app(config=config)) as client:
        response = client.put(
            "/apim/management/products/starter",
            headers=HEADERS,
            json={"name": "Starter", "state": "not_published", "subscriptions_limit": None, "terms": None},
        )
        assert response.status_code == 200
        assert response.json()["state"] == "not_published"
        assert response.json()["subscriptions_limit"] is None
        assert response.json()["terms"] is None


def test_invalid_product_update_keeps_existing_product(monkeypatch) -> None:
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    config = _config(products={"starter": ProductConfig(name="Starter", approval_required=True)})
    with TestClient(create_app(config=config)) as client:
        response = client.put(
            "/apim/management/products/starter",
            headers=HEADERS,
            json={"name": "Starter", "require_subscription": False},
        )
        assert response.status_code == 400
        assert client.app.state.gateway_config.products["starter"].require_subscription is True


@pytest.mark.parametrize("limit", [0, -1])
def test_product_subscription_limit_has_minimum_one(limit, monkeypatch) -> None:
    with pytest.raises(ValidationError):
        ProductConfig(name="Starter", subscriptions_limit=limit)
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    with TestClient(create_app(config=_config())) as client:
        response = client.put(
            "/apim/management/products/starter", headers=HEADERS, json={"name": "Starter", "subscriptions_limit": limit}
        )
        assert response.status_code == 422
        assert "starter" not in client.app.state.gateway_config.products


@pytest.mark.parametrize("write_target", ["api", "product"])
def test_conflicting_open_product_write_preserves_runtime_and_disk(write_target, tmp_path, monkeypatch) -> None:
    config = _config(
        products={
            "open-first": ProductConfig(name="Open first", require_subscription=False),
            "second": ProductConfig(name="Second", require_subscription=write_target == "product"),
        },
        apis={
            "hello": ApiConfig(
                name="Hello",
                path="hello",
                upstream_base_url="http://backend",
                products=["open-first", "second"] if write_target == "product" else ["open-first"],
            )
        },
    )
    path = tmp_path / "products.json"
    original = json.dumps(config.model_dump(mode="json"))
    path.write_text(original)
    monkeypatch.setenv("APIM_CONFIG_PATH", str(path))
    with TestClient(create_app(config=config)) as client:
        before = client.app.state.gateway_config.model_dump(mode="json")
        if write_target == "api":
            response = client.put(
                "/apim/management/apis/hello",
                headers=HEADERS,
                json={
                    "name": "Hello",
                    "path": "hello",
                    "upstream_base_url": "http://backend",
                    "products": ["open-first", "second"],
                },
            )
        else:
            response = client.put(
                "/apim/management/products/second",
                headers=HEADERS,
                json={"name": "Second", "require_subscription": False},
            )
        assert response.status_code == 400
        assert "at most one open product" in response.json()["detail"]
        assert client.app.state.gateway_config.model_dump(mode="json") == before
        assert path.read_text() == original
