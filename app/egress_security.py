"""An outbound firewall shared by gateway, callout, discovery and resolver requests."""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
from collections.abc import Callable
from typing import Any

import httpx
from jwt import InvalidTokenError, PyJWKClient

from app.network_security import peer_in_cidrs


def _destination(request, config):
    from app.security_governance import enforce_backend_transport

    enforce_backend_transport(config, str(request.url))
    settings = config.network_security
    host = request.url.host.casefold().rstrip(".")
    allowed = settings.allowed_backend_hosts
    if request.url.scheme not in {"http", "https"} or (
        allowed is not None and host not in {name.casefold().rstrip(".") for name in allowed}
    ):
        raise httpx.ConnectError("Outbound destination is not allowed", request=request)
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return host, port, settings


def _pin_request(request, settings, host, results):
    addresses = [result[4][0] for result in results]
    address = next((value for value in addresses if peer_in_cidrs(value, settings.allowed_backend_cidrs)), None)
    if address is None:
        raise httpx.ConnectError("Outbound address is outside the allowed network", request=request)
    # Pin the verified address. Host and TLS SNI retain the original name,
    # preventing a second DNS lookup from escaping the CIDR firewall.
    return httpx.Request(
        request.method,
        request.url.copy_with(host=address),
        headers=request.headers,
        stream=request.stream,
        extensions={**request.extensions, "sni_hostname": host},
    )


class EgressTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport, config_provider: Callable[[], Any]):
        self.transport = transport
        self.config_provider = config_provider

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host, port, settings = _destination(request, self.config_provider())
        forwarded = request
        if settings.allowed_backend_cidrs is not None:
            try:
                results = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
            except OSError as exc:
                raise httpx.ConnectError("Outbound destination cannot be resolved", request=request) from exc
            forwarded = _pin_request(request, settings, host, results)
        return await self.transport.handle_async_request(forwarded)

    async def aclose(self) -> None:
        await self.transport.aclose()


class EgressSyncTransport(httpx.BaseTransport):
    def __init__(self, transport: httpx.BaseTransport, config_provider: Callable[[], Any]):
        self.transport = transport
        self.config_provider = config_provider

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host, port, settings = _destination(request, self.config_provider())
        forwarded = request
        if settings.allowed_backend_cidrs is not None:
            try:
                results = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            except OSError as exc:
                raise httpx.ConnectError("Outbound destination cannot be resolved", request=request) from exc
            forwarded = _pin_request(request, settings, host, results)
        return self.transport.handle_request(forwarded)

    def close(self) -> None:
        self.transport.close()


class GuardedJWKClient(PyJWKClient):
    def __init__(self, uri: str, config: Any, ca_file: str | None = None):
        super().__init__(uri)
        self.config = config
        self.ca_file = ca_file

    def fetch_data(self):
        context = ssl.create_default_context(cafile=self.ca_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        transport = EgressSyncTransport(httpx.HTTPTransport(verify=context), lambda: self.config)
        try:
            with httpx.Client(transport=transport, trust_env=False, timeout=self.timeout) as client:
                response = client.get(self.uri)
                response.raise_for_status()
                jwk_set = response.json()
            if not isinstance(jwk_set, dict):
                raise ValueError("JWKS must be an object")
        except (httpx.HTTPError, ValueError) as exc:
            raise InvalidTokenError("Unable to acquire trusted signing keys") from exc
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(jwk_set)
        self._last_successful_fetch = time.monotonic()
        return jwk_set
