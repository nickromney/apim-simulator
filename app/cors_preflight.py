"""CORS preflight handling for the gateway.

https://learn.microsoft.com/en-us/azure/api-management/cors-policy

APIM evaluates only the `cors` policy on a preflight `OPTIONS` request and
answers it without running the rest of the pipeline. A preflight carries no
subscription key, so it is resolved and answered before admission.
"""

from __future__ import annotations

from fastapi import Request, Response
from starlette.requests import Request as StarletteRequest

from app.policy import PolicyRequest, PolicyRuntime, apply_preflight
from app.proxy import resolve_route
from app.request_pipeline import _policy_document_stack


def _is_preflight(request: Request) -> bool:
    # The docs do not define "processed as a preflight request"; use the Fetch
    # standard's shape: OPTIONS with Origin and Access-Control-Request-Method.
    return (
        request.method == "OPTIONS"
        and "origin" in request.headers
        and "access-control-request-method" in request.headers
    )


def _as_method(request: Request, method: str) -> Request:
    return StarletteRequest({**request.scope, "method": method.upper()}, request.receive)


def _defines_options_operation(cfg, request: Request) -> bool:
    """True when an operation explicitly declares OPTIONS.

    The docs: such an operation runs its own pipeline, and the cors preflight
    logic is not executed.
    """
    resolved = resolve_route(cfg, request)
    if resolved is None or not resolved.route.methods:
        return False
    return "OPTIONS" in {method.upper() for method in resolved.route.methods}


async def answer_preflight(request: Request) -> Response | None:
    """The policy's answer to a preflight, or None to run the normal pipeline."""
    if not _is_preflight(request):
        return None
    cfg = request.app.state.gateway_config
    if _defines_options_operation(cfg, request):
        return None
    resolved = resolve_route(cfg, _as_method(request, request.headers["access-control-request-method"]))
    if resolved is None:
        return None
    docs = _policy_document_stack(cfg, resolved.route, "", request.app.state.policy_cache)
    policy_req = PolicyRequest(
        method="OPTIONS",
        path=request.url.path,
        query=dict(request.query_params),
        headers=dict(request.headers),
        variables={},
    )
    spec = await apply_preflight(docs, policy_req, PolicyRuntime(gateway_config=cfg))
    if spec is None:
        return None
    return Response(status_code=spec.status_code, headers=spec.headers)
