"""Accept policy XML in APIM's ``rawxml`` format.

Azure accepts policy documents whose expressions are not XML-escaped when they
arrive as ``rawxml``, which is how the azurerm Terraform provider sends
``xml_content``. A condition such as ``@(a != null && b)`` or a body such as
``@(context.Variables.GetValueOrDefault<int>("n"))`` is therefore valid in a
deployed policy even though it is not well-formed XML.

``parse_policy_xml`` parses strictly first, so well-formed documents are never
touched. Only when that fails does it escape the characters XML forbids inside
expressions that start an attribute value or an element's text, then parse again.
"""

from __future__ import annotations

import re
from typing import Any

from defusedxml import ElementTree

_ENTITY = re.compile(r"&(?:[A-Za-z]+|#[0-9]+|#x[0-9A-Fa-f]+);")
_EXPRESSION_STARTS = ("@(", "@{")


def parse_policy_xml(xml: str) -> Any:
    """ElementTree.fromstring, retrying once with raw expressions escaped."""
    try:
        return ElementTree.fromstring(xml)
    except ElementTree.ParseError as strict_error:
        escaped = escape_raw_expressions(xml)
        if escaped == xml:
            raise
        try:
            return ElementTree.fromstring(escaped)
        except ElementTree.ParseError:
            raise strict_error from None


def _skip_quoted(text: str, position: int) -> int:
    """Return the index after the C# string or char literal starting at position."""
    if text.startswith("&quot;", position):
        end = text.find("&quot;", position + 6)
        return len(text) if end < 0 else end + 6
    verbatim = text[position] == "@"
    quote = text[position + 1] if verbatim else text[position]
    index = position + (2 if verbatim else 1)
    while index < len(text):
        char = text[index]
        if verbatim and char == quote and text.startswith(quote * 2, index):
            index += 2
            continue
        if not verbatim and char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return len(text)


def _expression_end(text: str, opening: int) -> int:
    """Index after the bracket that closes the expression opened at text[opening]."""
    open_char = text[opening]
    close_char = ")" if open_char == "(" else "}"
    depth = 0
    index = opening
    while index < len(text):
        char = text[index]
        if char == '"' or text.startswith("&quot;", index) or text.startswith(('@"', "'"), index):
            index = _skip_quoted(text, index)
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def _escape(expression: str, attribute_quote: str | None) -> str:
    out: list[str] = []
    for index, char in enumerate(expression):
        if char == "&" and not _ENTITY.match(expression, index):
            out.append("&amp;")
        elif char == "<":
            out.append("&lt;")
        elif char == ">":
            out.append("&gt;")
        elif char == attribute_quote:
            out.append("&quot;" if char == '"' else "&apos;")
        else:
            out.append(char)
    return "".join(out)


def _copy_until(text: str, position: int, terminator: str) -> int:
    end = text.find(terminator, position)
    return len(text) if end < 0 else end + len(terminator)


def _markup_section_end(xml: str, index: int) -> int | None:
    """End of a comment or CDATA section starting here; their content is never escaped."""
    for opening, closing in (("<!--", "-->"), ("<![CDATA[", "]]>")):
        if xml.startswith(opening, index):
            return _copy_until(xml, index, closing)
    return None


def _attribute_value(xml: str, index: int) -> tuple[str, int]:
    """Copy one quoted attribute value, escaping an expression at its start."""
    quote = xml[index]
    index += 1
    escaped = ""
    if xml.startswith(_EXPRESSION_STARTS, index):
        end = _expression_end(xml, index + 1)
        escaped = _escape(xml[index:end], quote)
        index = end
    end = _copy_until(xml, index, quote)
    return quote + escaped + xml[index:end], end


def escape_raw_expressions(xml: str) -> str:
    """Escape expressions that start an attribute value or an element's text."""
    out: list[str] = []
    index = 0
    in_tag = False
    at_text_start = False
    while index < len(xml):
        char = xml[index]
        if in_tag:
            if char in "\"'":
                piece, index = _attribute_value(xml, index)
                out.append(piece)
                continue
            in_tag = char != ">"
            at_text_start = char == ">"
        elif (section_end := _markup_section_end(xml, index)) is not None:
            out.append(xml[index:section_end])
            index = section_end
            continue
        elif at_text_start and xml.startswith(_EXPRESSION_STARTS, index):
            end = _expression_end(xml, index + 1)
            out.append(_escape(xml[index:end], None))
            index = end
            at_text_start = False
            continue
        else:
            in_tag = char == "<"
            at_text_start = at_text_start and char.isspace()
        out.append(char)
        index += 1
    return "".join(out)
