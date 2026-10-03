"""Socket-peer trust and forwarding boundaries, independent of deployment metadata."""

from __future__ import annotations

from collections.abc import Callable
from ipaddress import ip_address, ip_network
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class NetworkSecurityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    private_peer_cidrs: list[str] = Field(default_factory=list)
    allow_simulated_forwarded_headers: bool = False
    allowed_backend_hosts: list[str] | None = None
    allowed_backend_cidrs: list[str] | None = None

    @field_validator("trusted_proxy_cidrs", "private_peer_cidrs")
    @classmethod
    def validate_cidrs(cls, values: list[str]) -> list[str]:
        return [str(ip_network(value, strict=False)) for value in values]

    @field_validator("allowed_backend_cidrs")
    @classmethod
    def validate_optional_cidrs(cls, values: list[str] | None) -> list[str] | None:
        return cls.validate_cidrs(values) if values is not None else None


def peer_in_cidrs(peer: str, cidrs: list[str]) -> bool:
    try:
        address = ip_address(peer)
        return any(address in ip_network(cidr, strict=False) for cidr in cidrs)
    except ValueError:
        return False


def forwarded_client_ip(peer: str, value: str, trusted_cidrs: list[str]) -> str:
    """Walk from the socket back through trusted hops; never accept a caller's leftmost claim."""
    hops = [hop.strip() for hop in value.split(",") if hop.strip()] + [peer]
    for hop in reversed(hops):
        if not peer_in_cidrs(hop, trusted_cidrs):
            try:
                return str(ip_address(hop))
            except ValueError:
                return peer
    return peer


def _certificate_headers(config: Any) -> set[bytes]:
    settings = config.client_certificate
    return {
        getattr(settings, name).lower().encode()
        for name in ("subject_header", "issuer_header", "thumbprint_header", "cert_header")
    }


def sanitize_forwarding(scope: Scope, config: Any) -> None:
    """Attest the real peer before replacing untrusted forwarding claims."""
    settings = config.network_security
    peer = str((scope.get("client") or ("", 0))[0])
    trusted = peer_in_cidrs(peer, settings.trusted_proxy_cidrs)
    scope["apim.trusted_proxy"] = trusted
    scope["apim.socket_peer"] = peer
    accept_forwarding = trusted or settings.allow_simulated_forwarded_headers
    cert_headers = _certificate_headers(config)
    accept_certificate = trusted or config.client_certificate.allow_simulated_headers
    headers = []
    for name, value in scope.get("headers", []):
        lower = name.lower()
        if lower in cert_headers and not accept_certificate:
            continue
        if (lower.startswith(b"x-forwarded-") or lower == b"forwarded") and not accept_forwarding:
            continue
        if trusted and lower == b"x-forwarded-for":
            value = forwarded_client_ip(peer, value.decode("latin1"), settings.trusted_proxy_cidrs).encode()
        headers.append((name, value))
    scope["headers"] = headers


def private_access_allowed(scope: Scope, config: Any) -> bool:
    service = config.service
    private = (
        service.public_network_access_enabled is False or str(service.virtual_network_type).casefold() == "internal"
    )
    if not private:
        return True
    peer = str(scope.get("apim.socket_peer") or (scope.get("client") or ("", 0))[0])
    return peer_in_cidrs(peer, config.network_security.private_peer_cidrs)


class NetworkSecurityMiddleware:
    def __init__(self, app: ASGIApp, config_provider: Callable[[], Any]):
        self.app = app
        self.config_provider = config_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        config = self.config_provider()
        sanitize_forwarding(scope, config)
        if not private_access_allowed(scope, config):
            _record_private_denial(scope)
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": "Peer is outside the private gateway network"}, status_code=403)(
                    scope, receive, send
                )
            return
        await self.app(scope, receive, send)


def _record_private_denial(scope: Scope) -> None:
    from app.security_monitoring import security_actor

    app = scope.get("app")
    sink = getattr(getattr(app, "state", None), "security_audit_sink", None)
    if sink is not None:
        sink.record_event(
            "ingress_denial",
            security_actor(scope),
            scope.get("method", ""),
            scope.get("path", ""),
            403,
            reason="private-network",
        )
