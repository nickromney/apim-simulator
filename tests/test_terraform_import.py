from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.compat_report import build_compat_report
from app.config import GatewayConfig, RouteConfig, TenantAccessConfig
from app.main import create_app
from app.terraform_import import config_from_tofu_show_json, import_from_tofu_show_json


def _tf_json(resources: list[dict]) -> dict:
    return {"values": {"root_module": {"resources": resources}}}


def test_config_from_tofu_show_json_mvp() -> None:
    tf = _tf_json(
        [
            {
                "address": "azurerm_api_management_product.app_a",
                "type": "azurerm_api_management_product",
                "name": "app_a",
                "values": {"product_id": "app-a", "display_name": "App A", "subscription_required": True},
            },
            {
                "address": "azurerm_api_management_api.app_a",
                "type": "azurerm_api_management_api",
                "name": "app_a",
                "values": {"name": "api", "path": "app-a", "service_url": "http://upstream"},
            },
            {
                "address": "azurerm_api_management_api_operation.health",
                "type": "azurerm_api_management_api_operation",
                "name": "health",
                "values": {"api_name": "api", "operation_id": "health", "method": "GET", "url_template": "/health"},
            },
            {
                "address": "azurerm_api_management_product_api.app_a",
                "type": "azurerm_api_management_product_api",
                "name": "app_a",
                "values": {"product_id": "app-a", "api_name": "api"},
            },
            {
                "address": "azurerm_api_management_subscription.sub",
                "type": "azurerm_api_management_subscription",
                "name": "sub",
                "values": {
                    "subscription_id": "sub1",
                    "display_name": "demo",
                    "primary_key": "good",
                    "secondary_key": "good2",
                    "product_id": "app-a",
                },
            },
            {
                "address": "azurerm_api_management_api_policy.api",
                "type": "azurerm_api_management_api_policy",
                "name": "api",
                "values": {
                    "api_name": "api",
                    "xml_content": "<policies><inbound><base/></inbound><backend/><outbound/><on-error/></policies>",
                },
            },
            {
                "address": "azurerm_api_management_api_operation_policy.op",
                "type": "azurerm_api_management_api_operation_policy",
                "name": "op",
                "values": {
                    "api_name": "api",
                    "operation_id": "health",
                    "xml_content": "<policies><inbound><base/></inbound><backend/><outbound/><on-error/></policies>",
                },
            },
        ]
    )

    cfg = config_from_tofu_show_json(tf)
    assert cfg.apis["api"].path == "app-a"
    assert cfg.apis["api"].upstream_base_url == "http://upstream"
    assert cfg.apis["api"].products == ["app-a"]
    assert "health" in cfg.apis["api"].operations
    assert cfg.subscription.subscriptions["sub1"].keys.primary == "good"
    assert cfg.subscription.subscriptions["sub1"].products == ["app-a"]


def test_management_import_applies_routes() -> None:
    tf = _tf_json(
        [
            {
                "address": "azurerm_api_management_product.app_a",
                "type": "azurerm_api_management_product",
                "name": "app_a",
                "values": {"product_id": "app-a", "display_name": "App A", "subscription_required": True},
            },
            {
                "address": "azurerm_api_management_api.app_a",
                "type": "azurerm_api_management_api",
                "name": "app_a",
                "values": {"name": "api", "path": "app-a", "service_url": "http://upstream"},
            },
            {
                "address": "azurerm_api_management_api_operation.health",
                "type": "azurerm_api_management_api_operation",
                "name": "health",
                "values": {"api_name": "api", "operation_id": "health", "method": "GET", "url_template": "/health"},
            },
            {
                "address": "azurerm_api_management_product_api.app_a",
                "type": "azurerm_api_management_product_api",
                "name": "app_a",
                "values": {"product_id": "app-a", "api_name": "api"},
            },
            {
                "address": "azurerm_api_management_subscription.sub",
                "type": "azurerm_api_management_subscription",
                "name": "sub",
                "values": {
                    "subscription_id": "sub1",
                    "display_name": "demo",
                    "primary_key": "good",
                    "secondary_key": "good2",
                    "product_id": "app-a",
                },
            },
        ]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url == httpx.URL("http://upstream/health/")
        return httpx.Response(200, json={"ok": True})

    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            tenant_access=TenantAccessConfig(enabled=True, primary_key="t1"),
            routes=[RouteConfig(name="bootstrap", path_prefix="/", upstream_base_url="http://bootstrap")],
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with TestClient(app) as client:
        imported = client.post(
            "/apim/management/import/tofu-show",
            headers={"X-Apim-Tenant-Key": "t1"},
            json=tf,
        )
        assert imported.status_code == 200
        assert imported.json()["routes"] >= 1

        ok = client.get("/app-a/health", headers={"Ocp-Apim-Subscription-Key": "good"})
        assert ok.status_code == 200


def test_import_from_tofu_show_json_supports_openapi_version_sets_and_backend_credentials() -> None:
    fixture = Path(__file__).parent / "fixtures" / "tofu_show" / "sample.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    result = import_from_tofu_show_json(payload)
    cfg = result.config

    assert cfg.api_version_sets["sample-version-set"].version_header_name == "x-api-version"
    assert cfg.apis["sample-api"].api_version_set == "sample-version-set"
    assert cfg.apis["sample-api"].subscription_header_names == ["X-Sample-Key"]
    assert cfg.apis["sample-api"].subscription_query_param_names == ["sample-key"]
    assert sorted(cfg.apis["sample-api"].operations) == ["createWidget", "health"]
    assert cfg.backends["sample-backend"].authorization_scheme == "Bearer"
    assert cfg.backends["sample-backend"].authorization_parameter == "{{backend-secret}}"
    assert cfg.named_values["backend-secret"].value_from_key_vault is not None
    assert any(item.status == "adapted" and item.feature == "value_from_key_vault" for item in result.diagnostics)


def test_compat_report_is_green_for_supported_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "tofu_show" / "sample.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    report = build_compat_report(payload)

    assert report["unsupported"] == []
    assert report["supported"]
    assert report["adapted"]


def test_imported_subscription_key_parameter_names_are_honored_by_gateway() -> None:
    fixture = Path(__file__).parent / "fixtures" / "tofu_show" / "sample.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    cfg = config_from_tofu_show_json(payload)
    cfg.apis["sample-api"].policies_xml = None
    cfg.apis["sample-api"].operations["health"].policies_xml = None
    cfg.routes = cfg.materialize_routes()

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url == httpx.URL("https://backend.example.test/api/health/")
        return httpx.Response(200, json={"ok": True})

    app = create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with TestClient(app) as client:
        resp = client.get("/sample/health", headers={"X-Sample-Key": "sample-primary", "x-api-version": "v1"})

    assert resp.status_code == 200


def test_compat_report_classifies_policy_parity_v2_cache_modes() -> None:
    tf = _tf_json(
        [
            {
                "address": "azurerm_api_management_api_policy.cachey",
                "type": "azurerm_api_management_api_policy",
                "name": "cachey",
                "values": {
                    "api_name": "sample",
                    "xml_content": """\
<policies>
  <inbound>
    <cache-lookup vary-by-developer="false" vary-by-developer-groups="false" caching-type="prefer-external">
      <vary-by-query-parameter>version</vary-by-query-parameter>
    </cache-lookup>
    <cache-lookup-value key="demo" variable-name="value" caching-type="external" />
    <rate-limit-by-key calls="10" renewal-period="60" counter-key="demo" />
    <quota-by-key calls="100" bandwidth="4000" renewal-period="300" counter-key="demo" />
  </inbound>
  <backend />
  <outbound>
    <cache-store duration="60" />
    <cache-store-value key="demo" value="x" duration="60" />
  </outbound>
  <on-error>
    <cache-remove-value key="demo" />
  </on-error>
</policies>
""",
                },
            }
        ]
    )

    report = build_compat_report(tf)
    adapted_features = {item["feature"] for item in report["adapted"]}
    unsupported_features = {item["feature"] for item in report["unsupported"]}
    supported_features = {item["feature"] for item in report["supported"]}

    assert "cache-lookup.caching-type" in adapted_features
    assert "cache-lookup-value.caching-type" in unsupported_features
    assert "quota-by-key.bandwidth" in unsupported_features
    assert {"rate-limit-by-key", "cache-store", "cache-store-value", "cache-remove-value"}.issubset(supported_features)
