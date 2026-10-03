from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.config import GatewayConfig
from app.security_ingress import SecurityIngressMiddleware, waf_findings
from app.security_monitoring import SecurityAuditSink, SecurityMonitoringMiddleware
from app.security_settings import SecurityObservabilityConfig


def _app(tmp_path, **settings):
    app = FastAPI()
    app.state.gateway_config = GatewayConfig(security_ingress={"enabled": True, **settings})
    app.state.security_audit_sink = SecurityAuditSink(
        SecurityObservabilityConfig(enabled=True, database_path=str(tmp_path / "audit.sqlite"))
    )

    @app.post("/echo")
    async def echo(request: Request):
        return Response(
            await request.body(), media_type=request.headers.get("content-type", "application/octet-stream")
        )

    @app.get("/echo")
    async def get():
        return {"ok": True}

    app.add_middleware(SecurityIngressMiddleware)
    app.add_middleware(SecurityMonitoringMiddleware)
    return app


@pytest.mark.parametrize(
    "query,body,content_type,rule",
    [
        ("q=%27%20OR%201%3D1", b"", "text/plain", "sql-injection"),
        ("q=%2527%2520OR%25201%253D1", b"", "text/plain", "sql-injection"),
        ("", b'{"value":"<script>alert(1)</script>"}', "application/json", "script-injection"),
        ("filename=..%2f..%2fetc%2fpasswd", b"", "text/plain", "path-traversal"),
        ("", b'<img onerror="alert(1)">', "text/html", "script-injection"),
        ("", b"<script>alert(1)</script>", None, "script-injection"),
        ("", b"\x00<script>alert(1)</script>", "application/octet-stream", "script-injection"),
    ],
)
def test_negative_injection_matrix_blocks_and_audits_without_logging_payload(tmp_path, query, body, content_type, rule):
    app = _app(tmp_path, waf_mode="block")
    with TestClient(app) as client:
        headers = {"Content-Type": content_type} if content_type is not None else {}
        response = client.post("/echo?" + query, content=body, headers=headers)
    assert response.status_code == 403
    events = app.state.security_audit_sink.events()
    assert any(event["kind"] == "ingress_denial" and rule in event["reason"] for event in events)
    assert all("?" not in event["path"] for event in events)


@pytest.mark.parametrize(
    "body,content_type",
    [
        (b'{"description":"JavaScript help and trade union guidance"}', "application/json"),
        (b"\x00\xff\x10binary fixture", "application/octet-stream"),
        (b"select a lesson", "text/plain"),
    ],
)
def test_valid_text_and_binary_body_is_preserved_exactly(tmp_path, body, content_type):
    with TestClient(_app(tmp_path, waf_mode="block")) as client:
        response = client.post("/echo", content=body, headers={"Content-Type": content_type})
    assert response.status_code == 200 and response.content == body


def test_detect_mode_records_findings_and_forwards_unchanged_body(tmp_path):
    app = _app(tmp_path, waf_mode="detect")
    body = b"<script>teaching fixture</script>"
    with TestClient(app) as client:
        response = client.post("/echo", content=body, headers={"Content-Type": "text/plain"})
    assert response.content == body and response.status_code == 200
    assert any(event["kind"] == "ingress_detection" for event in app.state.security_audit_sink.events())


def test_body_limits_declared_and_chunked_query_limits_and_rate_ignore_spoofed_forwarding(tmp_path):
    app = _app(tmp_path, max_body_bytes=5, max_query_bytes=10, requests_per_window=4)
    with TestClient(app) as client:
        assert client.post("/echo", content=b"123456").status_code == 413
        assert client.post("/echo", content=iter([b"12", b"3456"])).status_code == 413
        assert client.get("/echo?q=abcdefghijk").status_code == 414
        assert client.get("/echo", headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 200
        assert client.get("/echo", headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 429


def test_peer_network_restriction_denies_spoofed_header(tmp_path):
    app = _app(tmp_path, allowed_client_networks=["127.0.0.1/32"])
    with TestClient(app) as client:
        assert client.get("/echo", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403


def test_concurrency_limit_releases_slot_and_api_body_read_timeout(tmp_path):
    async def run():
        app = _app(tmp_path, max_concurrent_requests=1)
        started, finish = asyncio.Event(), asyncio.Event()

        @app.get("/slow")
        async def slow():
            started.set()
            await finish.wait()
            return {"ok": True}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
            first = asyncio.create_task(client.get("/slow"))
            await started.wait()
            assert (await client.get("/echo")).status_code == 429
            finish.set()
            assert (await first).status_code == 200
            assert (await client.get("/echo")).status_code == 200

        app.state.gateway_config.security_ingress.body_read_timeout_seconds = 0.01
        middleware = SecurityIngressMiddleware(app)
        messages = []

        async def receive():
            await asyncio.sleep(1)
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await middleware(
            {"type": "http", "app": app, "path": "/echo", "method": "POST", "headers": [], "client": ("127.0.0.1", 1)},
            receive,
            send,
        )
        assert messages[0]["status"] == 408

    asyncio.run(run())


def test_rule_filtering_and_empty_rule_set():
    assert waf_findings("/", "q=' or 1=1", b"", ["path-traversal"]) == []
    assert waf_findings("/", "", b"<script>x</script>", []) == []
