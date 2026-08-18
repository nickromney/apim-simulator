from app.config import GatewayConfig, ProductConfig, RouteConfig
from app.effective_policy import (
    EMPTY_POLICY_XML,
    effective_policy_xml,
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
