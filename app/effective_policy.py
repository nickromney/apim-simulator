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


def merge_policy_xml_documents(xml_documents: list[str]) -> str:
    if not xml_documents:
        return EMPTY_POLICY_XML
    if len(xml_documents) == 1:
        return xml_documents[0]

    root = XmlTree.Element("policies")
    sections = {name: XmlTree.SubElement(root, name) for name in POLICY_SECTION_NAMES}

    for xml in xml_documents:
        try:
            parsed = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            continue
        if parsed.tag != "policies":
            continue
        for section_name in POLICY_SECTION_NAMES:
            source = parsed.find(section_name)
            if source is None:
                continue
            for child in list(source):
                sections[section_name].append(deepcopy(child))

    return XmlTree.tostring(root, encoding="unicode")


def effective_policy_xml(*groups: list[str] | None) -> str:
    xml_documents: list[str] = []
    for group in groups:
        if not group:
            continue
        xml_documents.extend(item for item in group if item)
    return merge_policy_xml_documents(xml_documents)


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
