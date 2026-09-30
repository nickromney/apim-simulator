from __future__ import annotations

from xml.etree import ElementTree

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import (
    ApiConfig,
    GatewayConfig,
    NamedValueConfig,
    OperationConfig,
    ProductConfig,
    TenantAccessConfig,
)
from app.main import create_app
from app.policy_inspection import inspect_effective_policy
from app.urls import http_url


def _scope_policy(scope: str, *, inherit: bool = True) -> str:
    base = "<base />" if inherit else ""
    return (
        "<policies><inbound>"
        f'{base}<set-header name="x-scope" exists-action="append">'
        f"<value>{scope}</value></set-header>"
        "</inbound></policies>"
    )


def _config_with_scopes() -> GatewayConfig:
    return GatewayConfig(
        policies_xml=(
            '<policies><inbound><set-header name="x-scope" exists-action="append"><value>global</value>'
            "</set-header></inbound><backend><forward-request /></backend></policies>"
        ),
        products={
            "api-product": ProductConfig(name="API product", policies_xml=_scope_policy("product")),
            "operation-product": ProductConfig(name="Operation product", policies_xml=_scope_policy("op-product")),
        },
        apis={
            "weather": ApiConfig(
                name="Weather",
                path="weather",
                upstream_base_url=http_url("weather-backend"),
                products=["api-product"],
                policies_xml=_scope_policy("api"),
                operations={
                    "current": OperationConfig(
                        name="Current",
                        method="GET",
                        url_template="/current",
                        products=["operation-product"],
                        policies_xml=_scope_policy("operation"),
                    )
                },
            )
        },
    )


def _target(cfg: GatewayConfig, scope_type: str, scope_name: str):
    if scope_type == "gateway":
        return cfg
    if scope_type == "product":
        return cfg.products[scope_name]
    if scope_type == "api":
        return cfg.apis[scope_name]
    api_id, operation_id = scope_name.split(":", maxsplit=1)
    return cfg.apis[api_id].operations[operation_id]


def _inbound_values(xml: str, header: str = "x-scope") -> list[str]:
    root = ElementTree.fromstring(xml)
    inbound = root.find("inbound")
    assert inbound is not None
    return [item.findtext("value") or "" for item in inbound.findall("set-header") if item.attrib.get("name") == header]


def test_effective_policy_matches_runtime_scope_order_and_product_association() -> None:
    cfg = _config_with_scopes()
    api_target = _target(cfg, "api", "weather")
    operation_target = _target(cfg, "operation", "weather:current")

    api_xml = inspect_effective_policy(cfg, scope_type="api", scope_name="weather", target=api_target)
    operation_without_product = inspect_effective_policy(
        cfg, scope_type="operation", scope_name="weather:current", target=operation_target
    )
    operation_with_product = inspect_effective_policy(
        cfg,
        scope_type="operation",
        scope_name="weather:current",
        target=operation_target,
        product_id="operation-product",
    )
    api_with_product = inspect_effective_policy(
        cfg,
        scope_type="api",
        scope_name="weather",
        target=api_target,
        product_id="api-product",
    )

    assert _inbound_values(api_xml) == ["global", "api"]
    assert _inbound_values(api_with_product) == ["global", "product", "api"]
    assert _inbound_values(operation_without_product) == ["global", "api", "operation"]
    assert _inbound_values(operation_with_product) == ["global", "op-product", "api", "operation"]
    assert "<forward-request" in operation_with_product

    with pytest.raises(HTTPException, match="not associated") as exc_info:
        inspect_effective_policy(
            cfg,
            scope_type="operation",
            scope_name="weather:current",
            target=operation_target,
            product_id="api-product",
        )
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException, match="not associated") as api_product_exc:
        inspect_effective_policy(
            cfg,
            scope_type="api",
            scope_name="weather",
            target=api_target,
            product_id="operation-product",
        )
    assert api_product_exc.value.status_code == 400


def test_effective_product_scope_inherits_global_policy() -> None:
    cfg = _config_with_scopes()

    xml = inspect_effective_policy(
        cfg,
        scope_type="product",
        scope_name="api-product",
        target=cfg.products["api-product"],
    )

    assert _inbound_values(xml) == ["global", "product"]
    assert "<forward-request" in xml


def test_effective_policy_honors_section_suppression_and_omitted_section_inheritance() -> None:
    cfg = GatewayConfig(
        policies_xml=(
            '<policies><inbound><set-header name="x-scope"><value>global</value></set-header>'
            "</inbound><backend><forward-request /></backend></policies>"
        ),
        apis={
            "weather": ApiConfig(
                name="Weather",
                path="weather",
                upstream_base_url=http_url("weather-backend"),
                policies_xml=_scope_policy("api", inherit=False),
            )
        },
    )

    xml = inspect_effective_policy(cfg, scope_type="api", scope_name="weather", target=cfg.apis["weather"])

    assert _inbound_values(xml) == ["api"]
    assert "<forward-request" in xml


def test_effective_policy_uses_default_forward_request_and_only_expands_fragments() -> None:
    cfg = GatewayConfig(
        named_values={
            "fragment-name": NamedValueConfig(value="shared", secret=True),
            "private-token": NamedValueConfig(value="do-not-show-this", secret=True),
        },
        policy_fragments={
            "shared": (
                '<set-header name="x-token" exists-action="override"><value>{{private-token}}</value></set-header>'
            )
        },
        policies_xml=('<policies><inbound><include-fragment fragment-id="{{fragment-name}}" /></inbound></policies>'),
    )

    xml = inspect_effective_policy(cfg, scope_type="gateway", scope_name="gateway", target=cfg)
    default_cfg = GatewayConfig()
    default_xml = inspect_effective_policy(default_cfg, scope_type="gateway", scope_name="gateway", target=default_cfg)

    assert '<set-header name="x-token"' in xml
    assert "{{private-token}}" in xml
    assert "do-not-show-this" not in xml
    assert "{{fragment-name}}" not in xml
    assert "<forward-request" in default_xml


def test_effective_policy_reports_missing_product_and_invalid_fragment_as_management_errors() -> None:
    cfg = _config_with_scopes()
    target = _target(cfg, "api", "weather")

    with pytest.raises(HTTPException, match="Product policy scope not found") as missing_product:
        inspect_effective_policy(
            cfg,
            scope_type="api",
            scope_name="weather",
            target=target,
            product_id="missing",
        )
    assert missing_product.value.status_code == 404

    cfg.apis[
        "weather"
    ].policies_xml = '<policies><inbound><include-fragment fragment-id="missing-fragment" /></inbound></policies>'
    with pytest.raises(HTTPException, match="Unknown policy fragment") as bad_fragment:
        inspect_effective_policy(cfg, scope_type="api", scope_name="weather", target=target)
    assert bad_fragment.value.status_code == 400
    target.policies_xml = (
        "<policies><inbound><choose><when condition='true'><base/></when></choose></inbound></policies>"
    )
    with pytest.raises(HTTPException, match="only allowed directly") as bad_base:
        inspect_effective_policy(cfg, scope_type="api", scope_name="weather", target=target)
    assert bad_base.value.status_code == 400


def test_authored_policy_get_remains_unchanged_and_effective_get_is_opt_in() -> None:
    authored = "<policies><inbound><set-header name='x-authored'><value>exact</value></set-header></inbound></policies>"
    cfg = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant-test"),
        apis={
            "weather": ApiConfig(
                name="Weather",
                path="weather",
                upstream_base_url=http_url("weather-backend"),
                policies_xml=authored,
            )
        },
    )

    with TestClient(create_app(config=cfg)) as client:
        headers = {"X-Apim-Tenant-Key": "tenant-test"}
        original = client.get("/apim/management/policies/api/weather", headers=headers)
        effective = client.get("/apim/management/policies/api/weather?effective=true", headers=headers)

    assert original.status_code == 200
    assert original.json() == {"scope_type": "api", "scope_name": "weather", "xml": authored}
    assert effective.status_code == 200
    assert effective.json()["effective"] is True
    assert "<forward-request" in effective.json()["xml"]
    assert "x-authored" in effective.json()["xml"]
