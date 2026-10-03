"""Documented fragment authoring constraints shared by load and management."""

from __future__ import annotations

from typing import Any

from defusedxml import ElementTree

MAX_FRAGMENT_BYTES = 512 * 1024
FORBIDDEN = {"policies", "inbound", "backend", "outbound", "on-error", "base", "include-fragment"}


def validate_fragment_xml(xml: str) -> None:
    if len(xml.encode("utf-8")) > MAX_FRAGMENT_BYTES:
        raise ValueError("Policy fragment exceeds 512 KB")
    try:
        root = ElementTree.fromstring("<fragment>" + xml + "</fragment>")
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid policy fragment XML") from exc
    for element in root.iter():
        if element.tag in FORBIDDEN:
            raise ValueError(f"Policy fragment cannot contain {element.tag}")


def validate_fragment_references(xml: str, fragments: dict[str, str], cfg: Any = None) -> None:
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid policies XML") from exc
    for element in root.iter("include-fragment"):
        identifier = element.get("fragment-id", "")
        if cfg is not None:
            from app.named_values import resolve_named_values_in_text

            identifier = resolve_named_values_in_text(identifier, cfg)
        if identifier not in fragments:
            raise ValueError(f"Unknown policy fragment referenced: {identifier}")


def validate_config_fragments(cfg: Any, policy_documents: list[tuple[str, str]]) -> None:
    for identifier, xml in cfg.policy_fragments.items():
        try:
            validate_fragment_xml(xml)
        except ValueError as exc:
            raise ValueError(f"Invalid fragment {identifier}: {exc}") from exc
    for _, xml in policy_documents:
        validate_fragment_references(xml, cfg.policy_fragments, cfg)
    for api in cfg.apis.values():
        if api.graphql is not None:
            for resolver in api.graphql.resolvers.values():
                validate_fragment_references(resolver.policies_xml, cfg.policy_fragments, cfg)
