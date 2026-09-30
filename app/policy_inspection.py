"""Inspect the same scope inheritance used by gateway policy execution."""

from __future__ import annotations

from fastapi import HTTPException

from app.config import DEFAULT_GLOBAL_POLICY_XML, GatewayConfig
from app.effective_policy import effective_policy_xml, policy_xml_documents_for_target
from app.policy import expand_policy_fragments_xml, parse_policies_xml


def _product_documents(cfg: GatewayConfig, product_id: str | None, allowed: list[str]) -> list[str]:
    if not product_id:
        return []
    product = cfg.products.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product policy scope not found")
    if product_id not in allowed:
        raise HTTPException(status_code=400, detail="Product is not associated with this policy target")
    return policy_xml_documents_for_target(product)


def _operation_groups(cfg: GatewayConfig, scope_name: str, product_id: str | None) -> list[list[str]]:
    api_id, _, operation_id = scope_name.partition(":")
    api = cfg.apis[api_id]
    operation = api.operations[operation_id]
    products = operation.products if operation.products is not None else api.products
    return [
        _product_documents(cfg, product_id, products),
        policy_xml_documents_for_target(api),
        policy_xml_documents_for_target(operation),
    ]


def _child_groups(
    cfg: GatewayConfig, scope_type: str, scope_name: str, target: object, product_id: str | None
) -> list[list[str]]:
    if scope_type == "gateway":
        return []
    if scope_type == "operation":
        return _operation_groups(cfg, scope_name, product_id)
    if scope_type == "product":
        if product_id and product_id != scope_name:
            raise HTTPException(status_code=400, detail="Product context must match the selected product scope")
        return [policy_xml_documents_for_target(target)]
    products = list(getattr(target, "products", []) or [])
    legacy_product = getattr(target, "product", None)
    if legacy_product and legacy_product not in products:
        products.append(legacy_product)
    return [_product_documents(cfg, product_id, products), policy_xml_documents_for_target(target)]


def inspect_effective_policy(
    cfg: GatewayConfig, *, scope_type: str, scope_name: str, target: object, product_id: str | None = None
) -> str:
    """The caller resolves the target first, preserving management's 404 rules.

    Product context is explicit: API/all-APIs/service subscriptions do not run
    product policy. Named-value references remain authored references here.
    """
    global_documents = policy_xml_documents_for_target(cfg) or [DEFAULT_GLOBAL_POLICY_XML]
    groups = _child_groups(cfg, scope_type.lower(), scope_name, target, product_id)
    try:
        xml = effective_policy_xml(global_documents, *groups)
        parse_policies_xml(xml, policy_fragments=cfg.policy_fragments, gateway_config=cfg)
        return expand_policy_fragments_xml(xml, cfg.policy_fragments, cfg)
    except (ValueError, HTTPException) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raise HTTPException(status_code=400, detail=detail) from exc
