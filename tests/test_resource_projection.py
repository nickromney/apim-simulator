from __future__ import annotations

import json

from app.config import (
    ApiConfig,
    GatewayConfig,
    NamedValueConfig,
    OperationConfig,
    ProductConfig,
    Subscription,
    SubscriptionConfig,
    SubscriptionKeyPair,
    load_config,
)
from app.resource_projection import project_summary


def test_project_summary_uses_service_scoped_ids_and_masks_secrets() -> None:
    cfg = GatewayConfig(
        service={"name": "lab-sim", "display_name": "Lab Simulator"},
        allow_anonymous=True,
        products={"starter": ProductConfig(name="Starter", require_subscription=True)},
        subscription=SubscriptionConfig(
            required=True,
            subscriptions={
                "starter-dev": Subscription(
                    id="starter-dev",
                    name="starter-dev",
                    keys=SubscriptionKeyPair(primary="primary", secondary="secondary"),
                    products=["starter"],
                )
            },
        ),
        named_values={"backend-secret": NamedValueConfig(value="super-secret-token", secret=True)},
        apis={
            "hello": ApiConfig(
                name="hello",
                path="hello",
                upstream_base_url="http://upstream",
                products=["starter"],
                operations={"getHello": OperationConfig(name="getHello", method="GET", url_template="/hello")},
            )
        },
    )
    cfg.routes = cfg.materialize_routes()

    payload = project_summary(cfg, trace_store={"trace-1": {"id": "trace-1"}})

    assert payload["service"]["id"] == "service/lab-sim"
    assert payload["service"]["counts"]["apis"] == 1
    assert payload["service"]["counts"]["operations"] == 1
    assert payload["service"]["counts"]["recent_traces"] == 1
    assert payload["apis"][0]["resource_id"] == "service/lab-sim/apis/hello"
    assert payload["apis"][0]["operations"][0]["resource_id"] == "service/lab-sim/apis/hello/operations/getHello"
    assert payload["subscriptions"][0]["resource_id"] == "service/lab-sim/subscriptions/starter-dev"
    assert payload["named_values"][0]["value"] == "***"
    assert payload["named_values"][0]["resolved"]["value"] == "***"


def test_load_config_accepts_api_and_route_authored_files(tmp_path, monkeypatch) -> None:
    api_authored = tmp_path / "api-authored.json"
    api_authored.write_text(
        json.dumps(
            {
                "allow_anonymous": True,
                "apis": {
                    "sample": {
                        "name": "sample",
                        "path": "sample",
                        "upstream_base_url": "http://upstream",
                        "operations": {"health": {"name": "health", "method": "GET", "url_template": "/health"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APIM_CONFIG_PATH", str(api_authored))

    loaded_api = load_config()

    assert loaded_api.service.name == "apim-simulator"
    assert list(loaded_api.apis) == ["sample"]
    assert loaded_api.routes == []

    route_authored = tmp_path / "route-authored.json"
    route_authored.write_text(
        json.dumps(
            {
                "allow_anonymous": True,
                "routes": [
                    {
                        "name": "legacy",
                        "path_prefix": "/api",
                        "upstream_base_url": "http://upstream",
                        "upstream_path_prefix": "/api",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APIM_CONFIG_PATH", str(route_authored))

    loaded_route = load_config()

    assert loaded_route.service.display_name == "Local APIM Simulator"
    assert loaded_route.apis == {}
    assert loaded_route.routes[0].name == "legacy"
