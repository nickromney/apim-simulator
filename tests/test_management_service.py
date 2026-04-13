from __future__ import annotations

import json
from types import SimpleNamespace

from app.config import (
    ApiConfig,
    GatewayConfig,
    OperationConfig,
    ProductConfig,
    RouteConfig,
    Subscription,
    SubscriptionConfig,
    SubscriptionKeyPair,
    SubscriptionState,
)
from app.management_service import ManagementService


class _Counter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict[str, str]]] = []

    def add(self, value: int, attrs: dict[str, str]) -> None:
        self.calls.append((value, attrs))


def _make_service(cfg: GatewayConfig) -> tuple[ManagementService, SimpleNamespace, _Counter]:
    counter = _Counter()
    app = SimpleNamespace(
        state=SimpleNamespace(
            gateway_config=cfg,
            oidc_verifiers={},
            policy_cache={"cached": True},
            policy_response_cache={"cached": True},
            policy_value_cache={"cached": True},
            gateway_metrics=SimpleNamespace(config_reloads=counter),
        )
    )
    service = ManagementService(
        app=app,
        serialize_gateway_config=lambda current: json.dumps(current.model_dump(mode="json"), indent=2) + "\n",
        build_oidc_verifiers=lambda current: {"route_count": str(len(current.routes))},
    )
    return service, app, counter


def test_apply_runtime_config_materializes_routes_and_clears_policy_caches() -> None:
    cfg = GatewayConfig(
        apis={
            "weather": ApiConfig(
                name="weather",
                path="weather",
                upstream_base_url="http://upstream",
                operations={
                    "current": OperationConfig(name="current", method="GET", url_template="/current"),
                },
            )
        }
    )
    service, app, _ = _make_service(cfg)

    updated = service.apply_runtime_config(cfg)

    assert updated.routes[0].name == "weather:current"
    assert app.state.gateway_config is updated
    assert app.state.oidc_verifiers == {"route_count": "1"}
    assert app.state.policy_cache == {}
    assert app.state.policy_response_cache == {}
    assert app.state.policy_value_cache == {}


def test_persist_or_apply_config_writes_and_reloads_from_disk(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "apim.json"
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))

    cfg = GatewayConfig(products={"starter": ProductConfig(name="Starter")})
    service, app, counter = _make_service(cfg)

    updated = service.persist_or_apply_config(cfg)

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["products"]["starter"]["name"] == "Starter"
    assert updated.products["starter"].name == "Starter"
    assert app.state.gateway_config.products["starter"].name == "Starter"
    assert counter.calls == [(1, {"result": "success"})]


def test_delete_product_unlinks_legacy_routes_and_subscriptions() -> None:
    cfg = GatewayConfig(
        products={"starter": ProductConfig(name="Starter")},
        subscription=SubscriptionConfig(
            subscriptions={
                "demo": Subscription(
                    id="demo",
                    name="Demo",
                    keys=SubscriptionKeyPair(primary="good", secondary="good2"),
                    products=["starter"],
                )
            }
        ),
        routes=[
            RouteConfig(
                name="legacy",
                path_prefix="/legacy",
                upstream_base_url="http://upstream",
                product="starter",
                products=["starter"],
            )
        ],
    )
    service, _, _ = _make_service(cfg)

    updated = service.delete_product(cfg, "starter")

    assert "starter" not in updated.products
    assert updated.subscription.subscriptions["demo"].products == []
    assert updated.routes[0].product is None
    assert updated.routes[0].products == []


def test_subscription_lifecycle_round_trips_through_persistence() -> None:
    cfg = GatewayConfig()
    service, _, _ = _make_service(cfg)

    created = service.create_subscription(
        cfg,
        SimpleNamespace(
            id="demo",
            name="Demo",
            state=SubscriptionState.Active,
            products=["starter"],
            primary_key=None,
            secondary_key=None,
        ),
    )
    assert created.subscription.subscriptions["demo"].created_by == "management"
    assert created.subscription.subscriptions["demo"].keys.primary == "sub-demo-primary"

    updated = service.update_subscription(
        created,
        "demo",
        SimpleNamespace(name="Demo Plus", state=SubscriptionState.Suspended, products=["starter", "pro"]),
    )
    assert updated.subscription.subscriptions["demo"].name == "Demo Plus"
    assert updated.subscription.subscriptions["demo"].state == SubscriptionState.Suspended
    assert updated.subscription.subscriptions["demo"].products == ["starter", "pro"]

    rotated, new_key = service.rotate_subscription_key(updated, "demo", "secondary")
    assert rotated.subscription.subscriptions["demo"].keys.secondary == new_key

    deleted = service.delete_subscription(rotated, "demo")
    assert "demo" not in deleted.subscription.subscriptions
