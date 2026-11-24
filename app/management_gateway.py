"""Management-gateway mode: a simulator instance that routes to a serving instance.

This models the Azure topology where the managed APIM instance is what callers
and operators address, and a self-hosted gateway does the gateway work. With
``APIM_MANAGEMENT_GATEWAY=true`` this process loads no APIs or policies of its
own. It answers its own liveness probes and status, and forwards every other
request (gateway traffic, ``/apim/management``, portal, traces) unchanged to
the serving instance named by ``APIM_MANAGEMENT_GATEWAY_UPSTREAM``. The serving
instance owns the one configuration, so management writes land there.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

logger = logging.getLogger("apim-simulator.management-gateway")

ENV_FLAG = "APIM_MANAGEMENT_GATEWAY"
ENV_UPSTREAM = "APIM_MANAGEMENT_GATEWAY_UPSTREAM"
ENV_GATEWAY_NAME = "APIM_MANAGEMENT_GATEWAY_NAME"
DEFAULT_GATEWAY_NAME = "self-hosted"
STATUS_PATH = "/apim/management-gateway/status"
FORWARDED_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

# RFC 9110 section 7.6.1 connection-specific fields, plus Content-Length, which
# HTTPX recalculates from the body it actually sends (see AGENTS.md).
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ManagementGatewaySettings:
    """Where to route, and the self-hosted gateway name reported in status."""

    upstream: str
    gateway_name: str = DEFAULT_GATEWAY_NAME

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ManagementGatewaySettings | None:
        """The settings when the flag is on, None when it is off.

        Raises ValueError when the flag is on but the upstream is missing or is
        not an absolute http(s) URL, so a misconfigured router fails at start
        rather than answering every request with 502.
        """
        env = os.environ if env is None else env
        if env.get(ENV_FLAG, "").strip().lower() not in _TRUTHY:
            return None
        upstream = env.get(ENV_UPSTREAM, "").strip().rstrip("/")
        parts = urlsplit(upstream)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"{ENV_FLAG} is on, so {ENV_UPSTREAM} must be an http(s) URL; got {upstream!r}")
        if parts.path or parts.query or parts.fragment:
            raise ValueError(f"{ENV_UPSTREAM} must be a scheme and host only, without a path; got {upstream!r}")
        gateway_name = env.get(ENV_GATEWAY_NAME, "").strip() or DEFAULT_GATEWAY_NAME
        return cls(upstream=upstream, gateway_name=gateway_name)


def _connection_tokens(headers: Mapping[str, str]) -> set[str]:
    """Header names the Connection field marks as hop-by-hop for this message."""
    return {token.strip().lower() for token in headers.get("connection", "").split(",") if token.strip()}


def forwardable_request_headers(request: Request) -> list[tuple[str, str]]:
    """The caller's headers minus hop-by-hop fields, with X-Forwarded-For added.

    Host is kept, so the serving instance sees the address the caller used. The
    serving instance ignores X-Forwarded-For unless the router's address is in
    its ``network_security.trusted_proxy_cidrs``.
    """
    dropped = _HOP_BY_HOP | _connection_tokens(request.headers) | {"content-length"}
    headers = [(name, value) for name, value in request.headers.items() if name.lower() not in dropped]
    peer = request.client.host if request.client is not None else ""
    if peer:
        prior = request.headers.get("x-forwarded-for", "")
        headers = [(name, value) for name, value in headers if name.lower() != "x-forwarded-for"]
        headers.append(("x-forwarded-for", f"{prior}, {peer}" if prior else peer))
    return headers


def forwardable_response_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    """The serving instance's headers minus hop-by-hop fields.

    Content-Length and Content-Encoding stay: the body is relayed as raw bytes,
    exactly as the serving instance sent it.
    """
    dropped = _HOP_BY_HOP | _connection_tokens(headers)
    return [(name, value) for name, value in headers.multi_items() if name.lower() not in dropped]


def _upstream_url(settings: ManagementGatewaySettings, request: Request) -> str:
    raw_path = request.scope.get("raw_path") or request.url.path.encode()
    url = settings.upstream + raw_path.decode("latin1")
    query = request.scope.get("query_string", b"")
    return f"{url}?{query.decode('latin1')}" if query else url


async def _relay_body(upstream: httpx.Response) -> AsyncIterator[bytes]:
    """The serving instance's body as raw bytes, as it arrived.

    A transport that has already read the body (HTTPX's MockTransport does)
    leaves nothing to stream, so relay what it read instead.
    """
    if upstream.is_stream_consumed:
        yield upstream.content
        return
    async for chunk in upstream.aiter_raw():
        yield chunk


def _gateway_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"statusCode": status_code, "message": message})


async def forward_request(request: Request) -> Response:
    """Relay one request to the serving instance and stream its response back."""
    settings: ManagementGatewaySettings = request.app.state.management_gateway
    client: httpx.AsyncClient = request.app.state.http_client
    upstream_request = client.build_request(
        request.method,
        _upstream_url(settings, request),
        headers=forwardable_request_headers(request),
        content=await request.body(),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        logger.warning("serving gateway timed out | upstream=%s", settings.upstream)
        return _gateway_error(504, "The serving gateway did not respond in time.")
    except httpx.TransportError as exc:
        logger.warning("serving gateway unreachable | upstream=%s | error=%s", settings.upstream, exc)
        return _gateway_error(502, "The management gateway could not reach the serving gateway.")
    return StreamingResponse(
        _relay_body(upstream),
        status_code=upstream.status_code,
        headers=dict(forwardable_response_headers(upstream.headers)),
        background=BackgroundTask(upstream.aclose),
    )


async def _probe_upstream(settings: ManagementGatewaySettings, client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        response = await client.get(f"{settings.upstream}/apim/startup", timeout=5.0)
    except httpx.HTTPError as exc:
        return {"status": "unreachable", "error": type(exc).__name__}
    return {"status": "ready" if response.status_code == 200 else "starting", "http_status": response.status_code}


def _build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/apim/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "mode": "management-gateway"}

    @router.get("/apim/startup")
    async def startup() -> dict[str, str]:
        return {"status": "started", "mode": "management-gateway"}

    @router.get(STATUS_PATH)
    async def status(request: Request) -> dict[str, Any]:
        settings: ManagementGatewaySettings = request.app.state.management_gateway
        return {
            "mode": "management-gateway",
            "gateway": settings.gateway_name,
            "upstream": settings.upstream,
            "serves_apis_natively": False,
            "serving_gateway": await _probe_upstream(settings, request.app.state.http_client),
        }

    @router.api_route("/{full_path:path}", methods=FORWARDED_METHODS, include_in_schema=False)
    async def forward(full_path: str, request: Request) -> Response:  # noqa: ARG001 - path is read from the scope
        return await forward_request(request)

    return router


def create_management_gateway_app(
    settings: ManagementGatewaySettings, *, http_client: httpx.AsyncClient | None = None
) -> FastAPI:
    """A FastAPI app that serves no APIs and routes everything to ``settings.upstream``."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_client = http_client is None
        app.state.http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0), follow_redirects=False, trust_env=False
        )
        logger.info(
            "apim-sim management gateway ready | gateway=%s | upstream=%s", settings.gateway_name, settings.upstream
        )
        try:
            yield
        finally:
            if owns_client:
                await app.state.http_client.aclose()

    app = FastAPI(title="Local APIM Simulator (management gateway)", lifespan=lifespan, openapi_url=None)
    app.state.management_gateway = settings
    app.include_router(_build_router())
    return app
