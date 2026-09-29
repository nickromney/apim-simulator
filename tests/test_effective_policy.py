from xml.etree import ElementTree

import pytest
from fastapi import HTTPException

from app.config import GatewayConfig, ProductConfig, RouteConfig
from app.effective_policy import (
    EMPTY_POLICY_XML,
    effective_policy_xml,
    policy_xml_documents_for_target,
    stacked_policy_xml_documents,
)
from app.policy import PolicyRequest, apply_backend, apply_inbound, apply_on_error, apply_outbound, parse_policies_xml
from app.urls import http_url

PARENT_POLICY = """\
<policies>
  <inbound>
    <set-header name="x-order" exists-action="append"><value>global</value></set-header>
  </inbound>
  <backend>
    <set-header name="x-order" exists-action="append"><value>global</value></set-header>
  </backend>
  <outbound>
    <set-header name="x-order" exists-action="append"><value>global</value></set-header>
  </outbound>
  <on-error>
    <set-header name="x-order" exists-action="append"><value>global</value></set-header>
  </on-error>
</policies>
"""

CHILD_POLICY_WITH_BASE = """\
<policies>
  <inbound>
    <set-header name="x-order" exists-action="append"><value>child</value></set-header>
    <base />
  </inbound>
  <backend>
    <set-header name="x-order" exists-action="append"><value>child</value></set-header>
    <base />
  </backend>
  <outbound>
    <set-header name="x-order" exists-action="append"><value>child</value></set-header>
    <base />
  </outbound>
  <on-error>
    <set-header name="x-order" exists-action="append"><value>child</value></set-header>
    <base />
  </on-error>
</policies>
"""


def test_effective_policy_xml_empty_is_the_empty_document() -> None:
    """APIM policy documents have four independent sections.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies
    """
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
    """Policy sections are composed independently across scopes.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
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
    """The effective policy remains a policies document.

    https://learn.microsoft.com/en-us/rest/api/apimanagement/policy/create-or-update?view=rest-apimanagement-2024-05-01
    """
    merged = effective_policy_xml(
        [
            "<policies><inbound><set-header name='a' exists-action='override'><value>1</value></set-header></inbound></policies>",
            "<policies><outbound><set-header name='b' exists-action='override'><value>2</value></set-header></outbound></policies>",
        ]
    )
    assert ElementTree.fromstring(merged).tag == "policies"


def test_a_document_that_will_not_parse_is_rejected_instead_of_skipped() -> None:
    """APIM rejects malformed policy XML when the policy is saved.

    https://learn.microsoft.com/en-us/rest/api/apimanagement/policy/create-or-update?view=rest-apimanagement-2024-05-01
    """
    with pytest.raises(ValueError, match="Invalid policies XML"):
        effective_policy_xml(
            [
                "<policies><inbound>",
                "<policies><inbound><set-header name='a' exists-action='override'><value>1</value></set-header></inbound></policies>",
            ]
        )


def test_an_empty_group_does_not_drop_the_groups_after_it() -> None:
    merged = effective_policy_xml(
        None,
        [],
        [
            "<policies><inbound><set-header name='a' exists-action='override'><value>1</value></set-header></inbound></policies>",
            "<policies><outbound><set-header name='b' exists-action='override'><value>2</value></set-header></outbound></policies>",
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


def test_base_is_replaced_at_its_position_in_all_policy_sections() -> None:
    """The child scope's base element splices the parent section in place.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    docs = [parse_policies_xml(PARENT_POLICY), parse_policies_xml(CHILD_POLICY_WITH_BASE)]

    inbound_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    assert apply_inbound(docs, inbound_req) is None
    assert inbound_req.headers["x-order"] == "child,global"

    backend_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    assert apply_backend(docs, backend_req) is None
    assert backend_req.headers["x-order"] == "child,global"

    outbound_headers: dict[str, str] = {}
    apply_outbound(docs, headers=outbound_headers)
    assert outbound_headers["x-order"] == "child,global"

    on_error_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    assert apply_on_error(docs, on_error_req) is None
    assert on_error_req.headers["x-order"] == "child,global"


def test_base_at_global_scope_has_no_parent_to_add() -> None:
    """A global base marker is a no-op because global has no parent scope.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    global_doc = parse_policies_xml(
        "<policies><inbound><base /><set-header name='x-order' exists-action='append'><value>global</value></set-header></inbound></policies>"
    )
    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})

    assert apply_inbound([global_doc], req) is None
    assert req.headers["x-order"] == "global"


def test_omitting_base_drops_the_parent_section() -> None:
    """Removing base prevents inheritance from the broader scope.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    child = parse_policies_xml(
        "<policies><inbound><set-header name='x-order' exists-action='append'><value>child</value></set-header></inbound></policies>"
    )
    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})

    assert apply_inbound([parse_policies_xml(PARENT_POLICY), child], req) is None
    assert req.headers["x-order"] == "child"


def test_empty_section_drops_parent_but_omitted_section_inherits() -> None:
    """A present section without base drops the parent; an omitted section inherits it.

    Learn says base "is included by default in each policy section", and that
    APIM configures base in the backend section at every non-global scope. It
    does not state what an omitted section means. Treating it as the default
    (base) keeps minimal policies that only define inbound forwarding to the
    backend, which is how they behave in practice.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    parent = parse_policies_xml(PARENT_POLICY)
    empty_section = parse_policies_xml("<policies><inbound /></policies>")
    omitted_section = parse_policies_xml("<policies><outbound /></policies>")

    empty_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    assert apply_inbound([parent, empty_section], empty_req) is None
    assert "x-order" not in empty_req.headers

    omitted_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    assert apply_inbound([parent, omitted_section], omitted_req) is None
    assert omitted_req.headers["x-order"] == "global"

    missing_doc_req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    assert apply_inbound([parent], missing_doc_req) is None
    assert missing_doc_req.headers["x-order"] == "global"


def test_effective_xml_inherits_an_omitted_section() -> None:
    """The management effective-policy view treats an omitted section as base.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    merged = effective_policy_xml([PARENT_POLICY], ["<policies><outbound /></policies>"])

    assert "global" in merged.split("<inbound>", 1)[1].split("</inbound>", 1)[0]


def test_policy_fragments_are_expanded_before_base_is_replaced() -> None:
    """Include-fragment inserts its policies at the selected position.

    https://learn.microsoft.com/en-us/azure/api-management/include-fragment-policy
    """
    child = parse_policies_xml(
        """\
<policies><inbound><include-fragment fragment-id="child" /><base /></inbound></policies>
""",
        policy_fragments={
            "child": '<set-header name="x-order" exists-action="append"><value>child</value></set-header>'
        },
    )
    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})

    assert apply_inbound([parse_policies_xml(PARENT_POLICY), child], req) is None
    assert req.headers["x-order"] == "child,global"


def test_base_inside_choose_is_rejected() -> None:
    """The base element belongs directly in a policy section, not choose branches.

    https://learn.microsoft.com/en-us/azure/api-management/choose-policy
    """
    with pytest.raises(HTTPException, match="base element is only allowed directly"):
        parse_policies_xml(
            """\
<policies><inbound><choose><when condition="method == 'GET'"><base /></when></choose></inbound></policies>
"""
        )


def test_effective_xml_replaces_base_per_section() -> None:
    """Calculate-effective-policy output reflects per-section base placement.

    https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies
    """
    merged = effective_policy_xml([PARENT_POLICY], [CHILD_POLICY_WITH_BASE])
    root = ElementTree.fromstring(merged)
    inbound = root.find("inbound")
    assert inbound is not None
    values = [item.findtext("value") for item in inbound]
    assert values == ["child", "global"]
