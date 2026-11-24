"""The xml-to-json and json-to-xml transformation policies.

References:
https://learn.microsoft.com/en-us/azure/api-management/xml-to-json-policy
https://learn.microsoft.com/en-us/azure/api-management/json-to-xml-policy

Microsoft documents the attributes but not the document mapping. The gateway is
a .NET service, so the mapping follows Json.NET's XmlNodeConverter conventions:
attributes become `@name` properties, text beside attributes becomes `#text`,
repeated sibling elements become an array, and every value stays a string. The
adaptations are listed in docs/XML-JSON-TRANSFORMS.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from xml.dom.minidom import Element as DomElement
from xml.dom.minidom import Node
from xml.etree.ElementTree import Element
from xml.sax.saxutils import escape, quoteattr

from defusedxml import minidom
from fastapi import HTTPException

from app.policy import PolicyNode, PolicyRequest, PolicyRuntime, _policy_bool, _record_step, render_policy_value

_MESSAGE_SECTIONS = frozenset({"outbound", "on-error"})


def _message(req: PolicyRequest) -> tuple[bytes, str]:
    """The body this section transforms, and its content type."""
    if req.section in _MESSAGE_SECTIONS:
        content_type = req.headers.get("content-type") or req.response_media_type or ""
        return req.response_body, content_type
    return req.body, req.headers.get("content-type") or ""


def _replace_message(req: PolicyRequest, body: bytes, content_type: str) -> None:
    req.headers["content-type"] = content_type
    if req.section in _MESSAGE_SECTIONS:
        req.response_body = body
        req.response_media_type = content_type
    else:
        req.body = body


def _accept_header(req: PolicyRequest) -> str:
    """The client's Accept header; outbound policy sees response headers in req.headers."""
    if req.section in _MESSAGE_SECTIONS:
        original = req.variables.get("_request_headers")
        if isinstance(original, dict):
            return str(original.get("accept") or original.get("Accept") or "")
        return ""
    return req.headers.get("accept") or ""


def _should_convert(
    req: PolicyRequest,
    runtime: PolicyRuntime | None,
    *,
    apply: str,
    consider_accept: str | None,
    source: str,
    target: str,
    content_type: str,
) -> str | None:
    """None when the message should be converted, else the reason it is left alone.

    apply="content-type-<source>" needs a source-format message; with
    consider-accept-header the client must ask for the target format.
    """
    mode = render_policy_value(apply, req, runtime).strip().lower()
    if mode == f"content-type-{source}" and source not in content_type.lower():
        return "content-type"
    if _policy_bool(consider_accept, req, runtime, default=True) and target not in _accept_header(req).lower():
        return "accept-header"
    return None


# --- xml-to-json -------------------------------------------------------------


def _element_to_json(element: DomElement, always_array: bool) -> Any:
    attributes = {f"@{name}": value for name, value in element.attributes.items()}
    children: dict[str, list[Any]] = {}
    text_parts: list[str] = []
    for child in element.childNodes:
        if child.nodeType == Node.ELEMENT_NODE:
            children.setdefault(child.tagName, []).append(_element_to_json(child, always_array))
        elif child.nodeType in {Node.TEXT_NODE, Node.CDATA_SECTION_NODE}:
            text_parts.append(child.data)
    text = "".join(text_parts).strip()
    if not attributes and not children:
        return text or None
    result: dict[str, Any] = dict(attributes)
    if text:
        result["#text"] = text
    for name, values in children.items():
        result[name] = values if always_array or len(values) > 1 else values[0]
    return result


def xml_to_json_document(xml: bytes, *, always_array_child_elements: bool = False) -> dict[str, Any]:
    document = minidom.parseString(xml)
    root = document.documentElement
    return {root.tagName: _element_to_json(root, always_array_child_elements)}


@dataclass(frozen=True)
class XmlToJson(PolicyNode):
    kind: str
    apply_mode: str
    consider_accept_header: str | None = None
    always_array_child_elements: str | None = None

    def apply_policy(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> None:
        body, content_type = _message(req)
        kind = render_policy_value(self.kind, req, runtime).strip().lower()
        if kind not in {"javascript-friendly", "direct"}:
            raise HTTPException(
                status_code=500, detail=f"xml-to-json kind must be javascript-friendly or direct: {kind}"
            )
        skipped = _should_convert(
            req,
            runtime,
            apply=self.apply_mode,
            consider_accept=self.consider_accept_header,
            source="xml",
            target="json",
            content_type=content_type,
        )
        if skipped is None and body.strip():
            always_array = _policy_bool(self.always_array_child_elements, req, runtime, default=False)
            try:
                converted = xml_to_json_document(body, always_array_child_elements=always_array)
            except Exception:  # noqa: BLE001 - any parse failure leaves the message as it was
                skipped = "not-xml"
            else:
                payload = json.dumps(converted, ensure_ascii=False).encode("utf-8")
                _replace_message(req, payload, "application/json; charset=utf-8")
        _record_step(runtime, "xml-to-json", {"kind": kind, "converted": skipped is None, "skipped": skipped})

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        self.apply_policy(req, runtime)


# --- json-to-xml -------------------------------------------------------------

_NAME_START = re.compile(r"[A-Za-z_:]")
_NAME_CHAR = re.compile(r"[A-Za-z0-9_.:\-]")


def _xml_name(name: str) -> str:
    """Encode a JSON property as an XML name, as .NET XmlConvert.EncodeName does."""
    if not name:
        return "_x0000_"
    out = []
    for index, char in enumerate(name):
        valid = _NAME_START.fullmatch(char) if index == 0 else _NAME_CHAR.fullmatch(char)
        out.append(char if valid else f"_x{ord(char):04X}_")
    return "".join(out)


@dataclass
class _JsonToXmlOptions:
    separator: str
    namespace_prefix: str
    attribute_block: str


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _qualified(name: str, options: _JsonToXmlOptions, prefixes: set[str]) -> str:
    if options.separator and options.separator != ":" and options.separator in name:
        prefix, local = name.split(options.separator, 1)
        if prefix in prefixes:
            return _xml_name(f"{prefix}:{local}")
    return _xml_name(name)


def _split_properties(
    value: dict[str, Any], options: _JsonToXmlOptions, prefixes: set[str]
) -> tuple[list[tuple[str, str]], str | None, list[tuple[str, Any]]]:
    attributes: list[tuple[str, str]] = []
    text: str | None = None
    children: list[tuple[str, Any]] = []
    for key, item in value.items():
        if options.namespace_prefix and (
            key == options.namespace_prefix or key.startswith(f"{options.namespace_prefix}:")
        ):
            attributes.append((key, _scalar_text(item)))
            if ":" in key:
                prefixes.add(key.split(":", 1)[1])
        elif options.attribute_block and key == options.attribute_block and isinstance(item, dict):
            attributes.extend((_qualified(name, options, prefixes), _scalar_text(v)) for name, v in item.items())
        elif key.startswith("@"):
            attributes.append((_qualified(key[1:], options, prefixes), _scalar_text(item)))
        elif key == "#text":
            text = _scalar_text(item)
        else:
            children.append((key, item))
    return attributes, text, children


def _json_to_xml(name: str, value: Any, options: _JsonToXmlOptions, prefixes: set[str]) -> str:
    if isinstance(value, list):
        return "".join(_json_to_xml(name, item, options, prefixes) for item in value)
    if not isinstance(value, dict):
        text = _scalar_text(value)
        tag = _qualified(name, options, prefixes)
        return f"<{tag}>{escape(text)}</{tag}>" if text else f"<{tag} />"
    scoped = set(prefixes)
    attributes, text, children = _split_properties(value, options, scoped)
    tag = _qualified(name, options, scoped)
    attrs = "".join(f" {attr}={quoteattr(attr_value)}" for attr, attr_value in attributes)
    inner = escape(text or "") + "".join(_json_to_xml(key, item, options, scoped) for key, item in children)
    return f"<{tag}{attrs}>{inner}</{tag}>" if inner else f"<{tag}{attrs} />"


def json_to_xml_document(
    payload: Any, *, separator: str = "_", namespace_prefix: str = "", attribute_block: str = ""
) -> str:
    """One root element: the single top-level property, else a `Document` wrapper."""
    options = _JsonToXmlOptions(separator, namespace_prefix, attribute_block)
    if isinstance(payload, dict) and len(payload) == 1:
        ((name, value),) = payload.items()
        if not isinstance(value, list):
            return _json_to_xml(name, value, options, set())
    if isinstance(payload, list):
        payload = {"Item": payload}
    return _json_to_xml("Document", payload, options, set())


@dataclass(frozen=True)
class JsonToXml(PolicyNode):
    apply_mode: str
    consider_accept_header: str | None = None
    parse_date: str | None = None
    namespace_separator: str | None = None
    namespace_prefix: str | None = None
    attribute_block_name: str | None = None

    def apply_policy(self, req: PolicyRequest, runtime: PolicyRuntime | None) -> None:
        body, content_type = _message(req)
        skipped = _should_convert(
            req,
            runtime,
            apply=self.apply_mode,
            consider_accept=self.consider_accept_header,
            source="json",
            target="xml",
            content_type=content_type,
        )
        if skipped is None and body.strip():
            try:
                payload = json.loads(body)
            except ValueError:
                skipped = "not-json"
            else:
                xml = json_to_xml_document(
                    payload,
                    separator=_optional(self.namespace_separator, req, runtime, default="_"),
                    namespace_prefix=_optional(self.namespace_prefix, req, runtime),
                    attribute_block=_optional(self.attribute_block_name, req, runtime),
                )
                _replace_message(req, xml.encode("utf-8"), "application/xml; charset=utf-8")
        _record_step(runtime, "json-to-xml", {"converted": skipped is None, "skipped": skipped})

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        self.apply_policy(req, runtime)


def _optional(value: str | None, req: PolicyRequest, runtime: PolicyRuntime | None, *, default: str = "") -> str:
    return render_policy_value(value, req, runtime) if value is not None else default


# --- parsing -----------------------------------------------------------------


def _check_attributes(element: Element, allowed: set[str], required: set[str]) -> None:
    unknown = set(element.attrib) - allowed - {"id"}
    if unknown:
        raise HTTPException(
            status_code=500, detail=f"{element.tag} unsupported attributes: {', '.join(sorted(unknown))}"
        )
    missing = sorted(name for name in required if not (element.attrib.get(name) or "").strip())
    if missing:
        raise HTTPException(status_code=500, detail=f"{element.tag} requires {', '.join(missing)}")
    if list(element):
        raise HTTPException(status_code=500, detail=f"{element.tag} does not allow child elements")


def _parse_xml_to_json(element: Element) -> PolicyNode:
    _check_attributes(
        element, {"kind", "apply", "consider-accept-header", "always-array-child-elements"}, {"kind", "apply"}
    )
    apply = element.attrib["apply"]
    if not apply.startswith("@") and apply.lower() not in {"always", "content-type-xml"}:
        raise HTTPException(status_code=500, detail="xml-to-json apply must be always or content-type-xml")
    return XmlToJson(
        kind=element.attrib["kind"],
        apply_mode=apply,
        consider_accept_header=element.attrib.get("consider-accept-header"),
        always_array_child_elements=element.attrib.get("always-array-child-elements"),
    )


def _parse_json_to_xml(element: Element) -> PolicyNode:
    _check_attributes(
        element,
        {
            "apply",
            "consider-accept-header",
            "parse-date",
            "namespace-separator",
            "namespace-prefix",
            "attribute-block-name",
        },
        {"apply"},
    )
    apply = element.attrib["apply"]
    if not apply.startswith("@") and apply.lower() not in {"always", "content-type-json"}:
        raise HTTPException(status_code=500, detail="json-to-xml apply must be always or content-type-json")
    parse_date = element.attrib.get("parse-date")
    if parse_date is not None and parse_date.lower() not in {"true", "false"}:
        # Policy expressions are not allowed for parse-date.
        raise HTTPException(status_code=500, detail="json-to-xml parse-date must be true or false")
    return JsonToXml(
        apply_mode=apply,
        consider_accept_header=element.attrib.get("consider-accept-header"),
        parse_date=parse_date,
        namespace_separator=element.attrib.get("namespace-separator"),
        namespace_prefix=element.attrib.get("namespace-prefix"),
        attribute_block_name=element.attrib.get("attribute-block-name"),
    )


def parse_xml_json_policy(element: Element) -> PolicyNode | None:
    parser = {"xml-to-json": _parse_xml_to_json, "json-to-xml": _parse_json_to_xml}.get(element.tag)
    return parser(element) if parser else None
