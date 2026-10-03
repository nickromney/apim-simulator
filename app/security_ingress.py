"""Opt-in local ingress filters with bounded bodies and peer-keyed admission."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from collections import deque
from urllib.parse import unquote

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.security_monitoring import security_actor


def waf_findings(path: str, query: str, body: bytes, rules: list[str]) -> list[str]:
    # Decode a bounded inspection copy; never trust the declared content type.
    text = path + "\n" + query + "\n" + body.decode("utf-8", errors="replace")
    for _ in range(2):
        text = unquote(text)
    normalized = re.sub(r"\s+", " ", text.lower())
    findings = []
    if "sql-injection" in rules and re.search(
        r"\bunion\s+(?:all\s+)?select\b|(?:'|\")\s*(?:or|and)\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+|\b(?:sleep|benchmark)\s*\(",
        normalized,
    ):
        findings.append("sql-injection")
    if "script-injection" in rules and re.search(
        r"<\s*script\b|javascript\s*:|<[^>]{0,512}\bon(?:error|load)\s*=", normalized
    ):
        findings.append("script-injection")
    if "path-traversal" in rules and any(
        value in normalized for value in ["../", "..\\", "/etc/passwd", "\\windows\\system32"]
    ):
        findings.append("path-traversal")
    return findings


class SecurityIngressMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.lock = asyncio.Lock()
        self.active = 0
        self.windows: dict[str, deque[float]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = scope["app"].state.gateway_config.security_ingress
        if not settings.enabled or any(scope["path"].startswith(prefix) for prefix in settings.exclude_prefixes):
            return await self.app(scope, receive, send)
        peer = str((scope.get("client") or ("unknown",))[0])
        reason = await self._admit(peer, settings)
        if reason:
            return await self._deny(scope, receive, send, reason, 403 if reason == "client-network" else 429)
        try:
            await self._handle(scope, receive, send, settings)
        finally:
            async with self.lock:
                self.active -= 1

    async def _admit(self, peer, settings):
        if settings.allowed_client_networks and not _allowed_peer(peer, settings.allowed_client_networks):
            return "client-network"
        async with self.lock:
            now = time.monotonic()
            self._expire(now - settings.window_seconds)
            if self.active >= settings.max_concurrent_requests:
                return "concurrency-limit"
            if peer not in self.windows and len(self.windows) >= settings.max_rate_keys:
                return "rate-key-capacity"
            window = self.windows.setdefault(peer, deque())
            if len(window) >= settings.requests_per_window:
                return "rate-limit"
            if sum(len(items) for items in self.windows.values()) >= settings.max_rate_entries:
                return "rate-entry-capacity"
            window.append(now)
            self.active += 1
        return None

    def _expire(self, cutoff):
        for key, window in list(self.windows.items()):
            while window and window[0] <= cutoff:
                window.popleft()
            if not window:
                del self.windows[key]

    async def _handle(self, scope, receive, send, settings):
        if len(scope.get("query_string", b"")) > settings.max_query_bytes or len(scope["path"]) > 8192:
            return await self._deny(scope, receive, send, "url-limit", 414)
        try:
            async with asyncio.timeout(settings.body_read_timeout_seconds):
                body, error = await _read_bounded_body(scope, receive, settings.max_body_bytes)
        except TimeoutError:
            return await self._deny(scope, receive, send, "body-read-timeout", 408)
        if error:
            return await self._deny(scope, receive, send, error, 413 if error == "body-limit" else 400)
        matches = (
            waf_findings(
                scope["path"],
                scope.get("query_string", b"").decode(errors="replace"),
                body,
                settings.waf_rules,
            )
            if settings.waf_mode != "disabled"
            else []
        )
        if matches:
            self._audit(scope, "waf:" + ",".join(matches), 403 if settings.waf_mode == "block" else 200)
            if settings.waf_mode == "block":
                return await self._deny(scope, receive, send, "waf", 403, audit=False)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    def _audit(self, scope, reason, status):
        sink = getattr(scope["app"].state, "security_audit_sink", None)
        if sink:
            sink.record_event(
                "ingress_denial" if status >= 400 else "ingress_detection",
                security_actor(scope),
                scope["method"],
                scope["path"],
                status,
                reason=reason,
            )

    async def _deny(self, scope, receive, send, reason, status, *, audit=True):
        scope.setdefault("state", {})["apim_result_reason"] = reason
        if audit:
            self._audit(scope, reason, status)
        headers = {"Retry-After": "1"} if status == 429 else None
        await JSONResponse(
            {"message": "Request rejected by local ingress protection", "reason": reason},
            status_code=status,
            headers=headers,
        )(scope, receive, send)


def _allowed_peer(peer, networks):
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in ipaddress.ip_network(network) for network in networks)


async def _read_bounded_body(scope, receive, limit):
    headers = dict(scope["headers"])
    try:
        length = int(headers.get(b"content-length", b"0"))
    except ValueError:
        return b"", "invalid-content-length"
    if length < 0:
        return b"", "invalid-content-length"
    if length > limit:
        return b"", "body-limit"
    chunks, total = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"", "client-disconnected"
        chunk = message.get("body", b"")
        total += len(chunk)
        if total > limit:
            return b"", "body-limit"
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks), None
