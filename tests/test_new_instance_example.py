"""In-process checks for examples/new-instance.

The config holds what a new classic-tier Azure APIM instance contains: the
built-in groups, the Starter and Unlimited products, the Echo API and the
built-in all-access subscription.
https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-add-products
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "examples" / "new-instance" / "apim.json"

STARTER_KEY = "new-instance-starter-primary"
UNLIMITED_KEY = "new-instance-unlimited-primary"
ALL_ACCESS_KEY = "new-instance-all-access-primary"
TENANT = {"X-Apim-Tenant-Key": "local-dev-tenant-key"}
PORTAL_USER = {"X-Apim-Portal-User": "demo-dev"}


class EchoBackend:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        payload = {"method": request.method, "path": request.url.path, "headers": dict(request.headers)}
        return httpx.Response(200, json=payload)


@pytest.fixture
def backend() -> EchoBackend:
    return EchoBackend()


@pytest.fixture
def client(backend: EchoBackend) -> Iterator[TestClient]:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    data["allowed_origins"] = ["http://localhost:8000"]
    app = create_app(
        config=GatewayConfig.model_validate(data),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)),
    )
    with TestClient(app) as test_client:
        yield test_client


def _call(client: TestClient, key: str | None, method: str = "GET", path: str = "/echo/resource") -> httpx.Response:
    headers = {"Ocp-Apim-Subscription-Key": key} if key else {}
    return client.request(method, path, params={"param1": "sample"}, headers=headers)


def test_config_contains_the_new_instance_defaults() -> None:
    config = GatewayConfig.model_validate(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    assert set(config.groups) == {"administrators", "developers", "guests"}
    assert set(config.products) == {"starter", "unlimited"}
    assert not config.products["starter"].approval_required
    assert config.products["unlimited"].approval_required
    for product in config.products.values():
        assert set(product.groups) == {"administrators", "developers", "guests"}
        assert product.subscriptions_limit == 1
    operations = config.apis["echo-api"].operations
    assert sorted((op.method, op.url_template) for op in operations.values()) == [
        ("DELETE", "/resource"),
        ("GET", "/resource"),
        ("GET", "/resource-cached"),
        ("HEAD", "/resource"),
        ("POST", "/resource"),
        ("PUT", "/resource"),
    ]


def test_a_call_without_a_key_is_refused(client: TestClient) -> None:
    response = _call(client, None)
    assert response.status_code == 401


def test_starter_allows_five_calls_a_minute(client: TestClient) -> None:
    statuses = [_call(client, STARTER_KEY).status_code for _ in range(6)]
    assert statuses == [200] * 5 + [429]


def test_unlimited_has_no_rate_limit(client: TestClient) -> None:
    assert [_call(client, UNLIMITED_KEY).status_code for _ in range(10)] == [200] * 10


def test_the_all_access_subscription_skips_product_policies(client: TestClient) -> None:
    assert [_call(client, ALL_ACCESS_KEY).status_code for _ in range(8)] == [200] * 8


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD"])
def test_each_resource_operation_reaches_the_backend(client: TestClient, backend: EchoBackend, method: str) -> None:
    assert _call(client, ALL_ACCESS_KEY, method).status_code == 200
    assert [(request.method, request.url.path) for request in backend.calls] == [(method, "/api/resource")]


def test_the_cached_operation_calls_the_backend_once(client: TestClient, backend: EchoBackend) -> None:
    first = _call(client, ALL_ACCESS_KEY, path="/echo/resource-cached")
    second = _call(client, ALL_ACCESS_KEY, path="/echo/resource-cached")
    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json() == first.json()
    assert len(backend.calls) == 1


def test_portal_starter_is_instant_and_limited_to_one_subscription(client: TestClient) -> None:
    created = client.post("/apim/portal/subscriptions", headers=PORTAL_USER, json={"product_id": "starter"})
    assert created.status_code == 201
    assert created.json()["state"] == "active"
    assert _call(client, created.json()["keys"]["primary"]).status_code == 200

    again = client.post("/apim/portal/subscriptions", headers=PORTAL_USER, json={"product_id": "starter"})
    assert again.status_code == 409


def test_portal_unlimited_waits_for_administrator_approval(client: TestClient) -> None:
    created = client.post("/apim/portal/subscriptions", headers=PORTAL_USER, json={"product_id": "unlimited"})
    assert created.json()["state"] == "submitted"
    subscription_id = created.json()["id"]
    key = created.json()["keys"]["primary"]
    assert _call(client, key).status_code == 401

    approved = client.patch(
        f"/apim/management/subscriptions/{subscription_id}", headers=TENANT, json={"state": "active"}
    )
    assert approved.status_code == 200
    assert _call(client, key).status_code == 200

    messages = client.get("/apim/management/outbox", headers=TENANT).json()["messages"]
    assert any(
        message["notification"] == "PurchaseDeveloperNotificationMessage" and message["to"] == ["demo-dev@example.test"]
        for message in messages
    )
