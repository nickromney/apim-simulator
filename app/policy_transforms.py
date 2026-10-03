"""Body substitution and Adobe cross-domain responses from the policy overview.

References:
https://learn.microsoft.com/en-us/azure/api-management/find-and-replace-policy
https://learn.microsoft.com/en-us/azure/api-management/cross-domain-policy
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.etree.ElementTree import Element, tostring

from fastapi import APIRouter, HTTPException, Request, Response

from app.policy import PolicyNode, PolicyRequest, PolicyRuntime, ResponseSpec, _record_step, render_policy_value


@dataclass(frozen=True)
class FindAndReplace(PolicyNode):
    source: str
    replacement: str

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        source = render_policy_value(self.source, req, runtime).encode("utf-8")
        replacement = render_policy_value(self.replacement, req, runtime).encode("utf-8")
        if not source:
            raise HTTPException(status_code=500, detail="find-and-replace from must not be empty")
        response = req.section in {"outbound", "on-error"}
        body = req.response_body if response else req.body
        result = body.replace(source, replacement)
        if response:
            req.response_body = result
        else:
            req.body = result
        _record_step(runtime, "find-and-replace", {"length": len(result), "replacements": body.count(source)})


@dataclass(frozen=True)
class CrossDomain(PolicyNode):
    document: bytes

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> ResponseSpec | None:
        # The Adobe client fetches this conventional policy-file path. Other
        # requests continue through the policy pipeline unchanged.
        if req.path != "/crossdomain.xml" or req.method != "GET":
            return None
        _record_step(runtime, "cross-domain", {"length": len(self.document)})
        return ResponseSpec(200, {"content-type": "application/xml"}, self.document, "application/xml")


def _validate_shape(element: Element, allowed: set[str]) -> None:
    unknown = set(element.attrib) - allowed - {"id"}
    if unknown:
        raise HTTPException(
            status_code=500, detail=f"{element.tag} unsupported attributes: {', '.join(sorted(unknown))}"
        )


def _parse_find_replace(element: Element) -> PolicyNode:
    _validate_shape(element, {"from", "to"})
    if "from" not in element.attrib or "to" not in element.attrib:
        raise HTTPException(status_code=500, detail="find-and-replace requires from and to")
    if list(element):
        raise HTTPException(status_code=500, detail="find-and-replace does not allow child elements")
    return FindAndReplace(element.attrib["from"], element.attrib["to"])


def _parse_cross_domain(element: Element) -> PolicyNode:
    _validate_shape(element, set())
    children = list(element)
    if len(children) > 1 or (children and children[0].tag != "cross-domain-policy"):
        raise HTTPException(status_code=500, detail="cross-domain requires a single cross-domain-policy document")
    document = tostring(children[0], encoding="utf-8") if children else b"<cross-domain-policy />"
    return CrossDomain(document)


def _parse_set_status(element: Element) -> PolicyNode:
    _validate_shape(element, {"code", "reason"})
    if not element.attrib.get("code") or "reason" not in element.attrib:
        raise HTTPException(status_code=500, detail="set-status requires code and reason")
    if list(element):
        raise HTTPException(status_code=500, detail="set-status does not allow child elements")
    return SetStatus(element.attrib["code"], element.attrib["reason"])


def parse_transform_policy(element: Element) -> PolicyNode | None:
    """Return a transform node, or None for another policy family.

    Imported lazily by the policy parser to keep its core node interfaces in
    one place without introducing a circular import during module loading.
    """
    parser = {
        "find-and-replace": _parse_find_replace,
        "cross-domain": _parse_cross_domain,
        "set-status": _parse_set_status,
    }.get(element.tag)
    return parser(element) if parser else None


def _cross_domain_node(nodes: list[PolicyNode], req: PolicyRequest) -> CrossDomain | None:
    from app.policy import Choose

    for node in nodes:
        if isinstance(node, CrossDomain):
            return node
        if isinstance(node, Choose):
            selected = next((steps for condition, steps in node.branches if condition(req)), node.otherwise)
            matched = _cross_domain_node(selected, req)
            if matched is not None:
                return matched
    return None


def build_cross_domain_router() -> APIRouter:
    """Serve the Adobe policy file declared at global scope.

    This conventional endpoint has no API route or backend. It exposes only
    global cross-domain declarations, including expanded fragments and the
    selected branch of a global choose; API declarations do not publish a
    service-wide Adobe policy file.
    """
    from app.policy import _effective_section_steps, parse_policies_xml

    router = APIRouter()

    @router.get("/crossdomain.xml")
    async def cross_domain_document(request: Request) -> Response:
        cfg = request.app.state.gateway_config
        xml_documents = list(cfg.policies_xml_documents)
        if cfg.policies_xml:
            xml_documents.append(cfg.policies_xml)
        docs = [
            parse_policies_xml(xml, policy_fragments=cfg.policy_fragments, gateway_config=cfg) for xml in xml_documents
        ]
        req = PolicyRequest("GET", "/crossdomain.xml", dict(request.query_params), dict(request.headers), {})
        nodes = [node for _scope, node in _effective_section_steps(docs, "inbound")]
        configured = _cross_domain_node(nodes, req)
        if configured is None:
            raise HTTPException(status_code=404, detail="No global cross-domain policy configured")
        return Response(configured.document, media_type="application/xml")

    return router


@dataclass(frozen=True)
class SetStatus(PolicyNode):
    code: str
    reason: str

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None) -> None:
        try:
            status = int(render_policy_value(self.code, req, runtime))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=500, detail="set-status code must be an integer") from exc
        if not 100 <= status <= 599:
            raise HTTPException(status_code=500, detail="set-status code must be a valid HTTP status")
        req.response_status_code = status
        reason = render_policy_value(self.reason, req, runtime)
        # ASGI status lines use the server's reason phrase. Keep the authored
        # phrase in policy state and traces, as for return-response metadata.
        req.variables["_response_reason_phrase"] = reason
        _record_step(runtime, "set-status", {"status_code": status, "reason": reason})
