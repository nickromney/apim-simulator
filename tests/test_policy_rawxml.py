"""Policy XML in APIM's rawxml format: expressions are not XML-escaped.

The azurerm provider sends xml_content as rawxml, so Azure accepts these
documents; the simulator must too, without changing well-formed ones.
"""

from __future__ import annotations

import pytest
from defusedxml import ElementTree

from app.policy import parse_policies_xml
from app.policy_rawxml import escape_raw_expressions, parse_policy_xml

RETRY_FROM_TERRAFORM = """<policies>
  <inbound><base /></inbound>
  <backend>
    <retry condition="@(context.Response != null && (context.Response.StatusCode == 429 || context.Response.StatusCode == 503))" count="2" interval="1" first-fast-retry="true">
      <set-variable name="attempt" value="@(context.Variables.GetValueOrDefault&lt;int&gt;(&quot;attempt&quot;, 0) + 1)" />
      <forward-request buffer-request-body="true" />
    </retry>
  </backend>
  <outbound><base /></outbound>
</policies>"""


def test_raw_ampersands_in_an_attribute_expression_parse() -> None:
    document = parse_policies_xml(RETRY_FROM_TERRAFORM)
    retry = document.backend[0]
    assert "&&" in retry.condition.expression


def test_raw_quotes_and_generics_in_attribute_expressions_are_escaped() -> None:
    xml = '<policies><inbound><set-variable name="n" value="@(context.Variables.GetValueOrDefault<int>("n", 0) > 1)" /></inbound></policies>'
    root = parse_policy_xml(xml)
    assert root.find("inbound/set-variable").attrib["value"] == (
        '@(context.Variables.GetValueOrDefault<int>("n", 0) > 1)'
    )


def test_raw_generics_in_element_text_expressions_are_escaped() -> None:
    xml = (
        "<policies><inbound><set-header name='x' exists-action='override'>"
        '<value>@(context.Request.Body.As<string>(preserveContent: true).Length < 10 ? "a" : "b")</value>'
        "</set-header></inbound></policies>"
    )
    root = parse_policy_xml(xml)
    assert root.find("inbound/set-header/value").text.startswith("@(context.Request.Body.As<string>")


def test_brackets_inside_csharp_strings_do_not_end_the_expression() -> None:
    xml = '<policies><inbound><choose><when condition="@(context.Request.Headers.GetValueOrDefault("x", ")") == ")" && true)" /></choose></inbound></policies>'
    root = parse_policy_xml(xml)
    assert root.find("inbound/choose/when").attrib["condition"].endswith('== ")" && true)')


def test_multi_statement_expression_with_verbatim_string() -> None:
    xml = '<policies><inbound><set-variable name="v" value="@{ var p = @"a""b"; return p.Length > 1 && true; }" /></inbound></policies>'
    root = parse_policy_xml(xml)
    assert root.find("inbound/set-variable").attrib["value"] == '@{ var p = @"a""b"; return p.Length > 1 && true; }'


def test_well_formed_documents_are_not_rewritten() -> None:
    xml = (
        '<policies><inbound><set-body>@(&quot;a&quot; + "&lt;b&gt;")</set-body><!-- @(x && y) --></inbound></policies>'
    )
    # Entities already present, and comments, are kept verbatim.
    assert escape_raw_expressions(xml) == xml
    assert ElementTree.tostring(parse_policy_xml(xml)) == ElementTree.tostring(ElementTree.fromstring(xml))


def test_cdata_and_plain_text_with_at_signs_are_left_alone() -> None:
    xml = "<policies><inbound><set-body><![CDATA[a && b < c]]></set-body><set-body>mail me @(at) x && y</set-body></inbound></policies>"
    assert "<![CDATA[a && b < c]]>" in escape_raw_expressions(xml)
    with pytest.raises(ElementTree.ParseError):
        parse_policy_xml(xml)


def test_unrecoverable_documents_report_the_original_parse_error() -> None:
    with pytest.raises(ElementTree.ParseError) as excinfo:
        parse_policy_xml('<policies><inbound><when condition="@(a && b)"></inbound>')
    # The strict error is reported: it points at the raw && the author wrote.
    assert "column 41" in str(excinfo.value)
