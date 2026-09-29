"""Effective policy for a route and product.

Owns global → product → API → operation stacking and XML merge.
The policy engine in ``app.policy`` keeps parse and apply.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from xml.etree import ElementTree as XmlTree

from defusedxml import ElementTree

from app.config import GatewayConfig, ProductConfig, RouteConfig

EMPTY_POLICY_XML = "<policies><inbound /><backend /><outbound /><on-error /></policies>"
POLICY_SECTION_NAMES = ("inbound", "backend", "outbound", "on-error")


def _parsed_policy_root(xml: str) -> Any:
    """Parse one policy document, retaining errors for the authoring boundary."""
    try:
        parsed = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid policies XML") from exc
    if parsed.tag != "policies":
        raise ValueError("Policies XML must have <policies> root")
    return parsed


def validate_policy_xml_syntax(xml: str) -> None:
    """Reject malformed or wrongly rooted policy XML during config loading."""
    _parsed_policy_root(xml)


def _validate_direct_base(section: Any) -> None:
    """Reject a base nested below a policy section's immediate children."""
    for child in list(section):
        if child.tag == "base":
            continue
        if any(node.tag == "base" for node in child.iter()):
            raise ValueError("The base element is only allowed directly inside a policy section")


def _merge_section_children(parent: list[Any], source: Any | None) -> list[Any]:
    """Replace each direct base marker with the already-effective parent."""
    if source is None:
        return []

    _validate_direct_base(source)
    merged: list[Any] = []
    for child in list(source):
        if child.tag == "base":
            merged.extend(deepcopy(parent))
        else:
            merged.append(deepcopy(child))
    return merged


def _scope_section_source(roots: list[Any], section_name: str) -> Any | None:
    """Combine partial documents authored at one scope into one section."""
    source = XmlTree.Element(section_name)
    present = False
    for root in roots:
        section = root.find(section_name)
        if section is None:
            continue
        present = True
        for child in list(section):
            source.append(deepcopy(child))
    return source if present else None


def _merge_policy_scopes(scope_groups: list[list[str]]) -> str:
    root = XmlTree.Element("policies")
    sections = {name: XmlTree.SubElement(root, name) for name in POLICY_SECTION_NAMES}
    effective_sections = {name: [] for name in POLICY_SECTION_NAMES}

    for group in scope_groups:
        roots = [_parsed_policy_root(xml) for xml in group]
        for section_name in POLICY_SECTION_NAMES:
            effective_sections[section_name] = _merge_section_children(
                effective_sections[section_name], _scope_section_source(roots, section_name)
            )

    for section_name, children in effective_sections.items():
        for child in children:
            sections[section_name].append(child)

    return XmlTree.tostring(root, encoding="unicode")


def merge_policy_xml_documents(xml_documents: list[str]) -> str:
    """Merge documents as a broad-to-narrow policy scope stack."""
    documents = [item for item in xml_documents if item]
    if not documents:
        return EMPTY_POLICY_XML
    return _merge_policy_scopes([[xml] for xml in documents])


def effective_policy_xml(*groups: list[str] | None) -> str:
    scope_groups: list[list[str]] = []
    for group in groups:
        if not group:
            continue
        documents = [item for item in group if item]
        if documents:
            scope_groups.append(documents)
    if not scope_groups:
        return EMPTY_POLICY_XML
    return _merge_policy_scopes(scope_groups)


def policy_xml_documents_for_target(target: Any) -> list[str]:
    docs = list(getattr(target, "policies_xml_documents", []) or [])
    xml = getattr(target, "policies_xml", None)
    if xml:
        docs.append(xml)
    return docs


def stacked_policy_xml_documents(
    cfg: GatewayConfig,
    route: RouteConfig,
    effective_product: ProductConfig | None,
) -> list[str]:
    documents: list[str] = []
    documents.extend(cfg.policies_xml_documents)
    if cfg.policies_xml:
        documents.append(cfg.policies_xml)
    if effective_product is not None and effective_product.policies_xml:
        documents.append(effective_product.policies_xml)
    documents.extend(route.policies_xml_documents)
    if route.policies_xml:
        documents.append(route.policies_xml)
    return [item for item in documents if item]
