"""An API's own subscriptionRequired decides whether a call needs a key.

APIM's summary table: with the API requiring a subscription a call needs a key
valid for that API (API-, product-, all-APIs- or service-scoped) unless an open
product serves it; with the API not requiring one, any key or none is allowed
and product context applies only from a matching key or an open product.
https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from app.urls import http_url

PRODUCT_POLICY = (
    '<policies><inbound><base /><set-header name="x-product" exists-action="override">'
    '<value>@(context.Product?.Id ?? "none")</value></set-header></inbound></policies>'
)


def _key(sub_id: str, primary: str, **scope: object) -> dict:
    return {"id": sub_id, "name": sub_id, "keys": {"primary": primary, "secondary": f"{primary}-2"}, **scope}


def _config(
    *,
    subscription_required: bool | None,
    products: list[str],
    global_required: bool = True,
) -> GatewayConfig:
    api: dict = {
        "name": "weather",
        "path": "weather",
        "upstream_base_url": http_url("upstream"),
        "products": products,
        "operations": {"get": {"name": "get", "method": "GET", "url_template": "/today"}},
    }
    if subscription_required is not None:
        api["subscription_required"] = subscription_required
    cfg = GatewayConfig.model_validate(
        {
            "allow_anonymous": True,
            "products": {
                "paid": {"name": "paid", "policies_xml": PRODUCT_POLICY},
                "other": {"name": "other"},
            },
            "subscription": {
                "required": global_required,
                "subscriptions": {
                    "paid": _key("paid", "paid-key", products=["paid"]),
                    "other": _key("other", "other-key", products=["other"]),
                    "api": _key("api", "api-key", scope="api", api_id="weather"),
                    "elsewhere": _key("elsewhere", "elsewhere-key", scope="api", api_id="billing"),
                    "all": _key("all", "all-key", scope="all-apis", all_apis=True),
                },
            },
            "apis": {"weather": api},
        }
    )
    cfg.routes = cfg.materialize_routes()
    return cfg


def _call(cfg: GatewayConfig, key: str | None = None) -> httpx.Response:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["product"] = request.headers.get("x-product", "")
        return httpx.Response(200, json={"product": seen["product"]})

    app = create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    headers = {"Ocp-Apim-Subscription-Key": key} if key else {}
    with TestClient(app) as client:
        return client.get("/weather/today", headers=headers)


@pytest.mark.parametrize(
    ("key", "status"),
    [(None, 401), ("api-key", 200), ("all-key", 200), ("other-key", 401), ("elsewhere-key", 401), ("bogus", 401)],
)
def test_api_requiring_a_subscription_with_no_product_needs_a_key_for_that_api(key: str | None, status: int) -> None:
    """A product-less API that requires a subscription admits only keys scoped to it.

    The gateway-wide setting is off here, so the API's own flag is what applies.
    """
    cfg = _config(subscription_required=True, products=[], global_required=False)
    assert _call(cfg, key).status_code == status


@pytest.mark.parametrize("key", [None, "bogus", "other-key", "elsewhere-key", "api-key", "all-key"])
def test_api_not_requiring_a_subscription_serves_any_key_or_none(key: str | None) -> None:
    """With subscriptionRequired false the call succeeds whatever key it carries.

    The gateway-wide setting and the API's subscription-requiring product would
    otherwise demand a key.
    """
    cfg = _config(subscription_required=False, products=["paid"])
    response = _call(cfg, key)
    assert response.status_code == 200
    assert response.json() == {"product": ""}, "the closed product's policy must not apply"


def test_api_not_requiring_a_subscription_applies_a_matching_product_key() -> None:
    """A key for one of the API's products still brings that product's context."""
    response = _call(_config(subscription_required=False, products=["paid"]), "paid-key")
    assert response.status_code == 200
    assert response.json() == {"product": "paid"}


def test_api_not_requiring_a_subscription_is_open_with_no_product() -> None:
    assert _call(_config(subscription_required=False, products=[]), None).status_code == 200


@pytest.mark.parametrize(("key", "status"), [(None, 401), ("paid-key", 200), ("other-key", 401), ("api-key", 200)])
def test_api_requiring_a_subscription_in_a_closed_product(key: str | None, status: int) -> None:
    """Product-scoped keys for the API's product, and API-scoped keys, are accepted."""
    cfg = _config(subscription_required=True, products=["paid"], global_required=False)
    assert _call(cfg, key).status_code == status


def test_unset_subscription_required_keeps_the_gateway_wide_behaviour() -> None:
    """Configs written before the field existed are served as they were."""
    cfg = _config(subscription_required=None, products=[])
    assert cfg.apis["weather"].subscription_required is None
    assert "subscription_required" not in cfg.apis["weather"].model_dump()
    assert _call(cfg, None).status_code == 200
