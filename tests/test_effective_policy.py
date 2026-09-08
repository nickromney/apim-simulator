from xml.etree import ElementTree

from app.config import GatewayConfig, ProductConfig, RouteConfig
from app.effective_policy import (
    EMPTY_POLICY_XML,
    effective_policy_xml,
    policy_xml_documents_for_target,
    stacked_policy_xml_documents,
)
from app.urls import http_url


def test_effective_policy_xml_empty_is_the_empty_document() -> None:
    assert effective_policy_xml() == EMPTY_POLICY_XML


def test_stacked_policy_xml_documents_follow_scope_order() -> None:
    cfg = GatewayConfig(
        policies_xml="<policies><inbound><set-header name='g' exists-action='override'><value>1</value></set-header></inbound></policies>",
        products={
            "starter": ProductConfig(
                name="starter",
                policies_xml="<policies><inbound><set-header name='p' exists-action='override'><value>1</value></set-header></inbound></policies>",
            )
        },
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url=http_url("upstream"),
                policies_xml="<policies><inbound><set-header name='r' exists-action='override'><value>1</value></set-header></inbound></policies>",
            )
        ],
    )
    docs = stacked_policy_xml_documents(cfg, cfg.routes[0], cfg.products["starter"])
    assert docs == [
        cfg.policies_xml,
        cfg.products["starter"].policies_xml,
        cfg.routes[0].policies_xml,
    ]


def test_effective_policy_xml_merges_sections() -> None:
    merged = effective_policy_xml(
        [
            "<policies><inbound><set-header name='a' exists-action='override'><value>1</value></set-header></inbound></policies>",
            "<policies><outbound><set-header name='b' exists-action='override'><value>2</value></set-header></outbound></policies>",
        ]
    )
    assert "name='a'" in merged or 'name="a"' in merged
    assert "name='b'" in merged or 'name="b"' in merged
    assert "<inbound>" in merged
    assert "<outbound>" in merged


def test_merged_document_is_rooted_at_policies() -> None:
    merged = effective_policy_xml(
        [
            "<policies><inbound><set-header name='a' exists-action='override'><value>1</value></set-header></inbound></policies>",
            "<policies><outbound><set-header name='b' exists-action='override'><value>2</value></set-header></outbound></policies>",
        ]
    )
    assert ElementTree.fromstring(merged).tag == "policies"


def test_a_document_that_will_not_parse_does_not_drop_the_ones_after_it() -> None:
    merged = effective_policy_xml(
        [
            "<policies><inbound>",
            "<policies><inbound><set-header name='a' exists-action='override'><value>1</value></set-header></inbound></policies>",
            "<policies><outbound><set-header name='b' exists-action='override'><value>2</value></set-header></outbound></policies>",
        ]
    )
    assert "name='a'" in merged or 'name="a"' in merged
    assert "name='b'" in merged or 'name="b"' in merged


def test_an_empty_group_does_not_drop_the_groups_after_it() -> None:
    merged = effective_policy_xml(
        None,
        [],
        [
            "<policies><inbound><set-header name='a' exists-action='override'><value>1</value></set-header></inbound></policies>"
        ],
        [
            "<policies><outbound><set-header name='b' exists-action='override'><value>2</value></set-header></outbound></policies>"
        ],
    )
    assert "name='a'" in merged or 'name="a"' in merged
    assert "name='b'" in merged or 'name="b"' in merged


def test_policy_documents_for_a_target_keep_the_list_then_the_single_document() -> None:
    route = RouteConfig(
        name="r1",
        path_prefix="/api",
        upstream_base_url=http_url("upstream"),
        policies_xml_documents=["<policies><inbound /></policies>"],
        policies_xml="<policies><outbound /></policies>",
    )
    assert policy_xml_documents_for_target(route) == [
        "<policies><inbound /></policies>",
        "<policies><outbound /></policies>",
    ]


def test_policy_documents_for_a_target_with_neither_attribute_is_empty() -> None:
    # Accepted equivalent: policy_xml_documents_for_target__mutmut_6 changes the
    # getattr default from [] to None and survives, because the result is passed
    # straight through `or []`. Both spellings give the same list for a target
    # that has the attribute and for one that does not.
    class Bare:
        pass

    assert policy_xml_documents_for_target(Bare()) == []
