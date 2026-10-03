"""Bounded durable security events and configurable local threat findings."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Query, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.security import require_tenant_access
from app.security_settings import SecurityObservabilityConfig


class SecurityAuditSink:
    def __init__(self, settings: SecurityObservabilityConfig):
        self.settings = settings
        self.lock = threading.Lock()
        if settings.enabled:
            Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL, reason TEXT NOT NULL, metadata TEXT NOT NULL)"
                )
                db.execute("CREATE INDEX IF NOT EXISTS event_time ON events(timestamp)")
            Path(settings.database_path).chmod(0o600)

    def _connect(self):
        return sqlite3.connect(self.settings.database_path, timeout=5)

    def record_event(self, kind, actor, method, path, status, *, reason="", metadata=None):
        if not self.settings.enabled:
            return
        # Never accept arbitrary headers/bodies/token text into the audit store.
        details = {
            key: str(value)[:256]
            for key, value in (metadata or {}).items()
            if key in {"rule", "resource", "action", "correlation_id", "route"}
        }
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT INTO events(timestamp,kind,actor,method,path,status,reason,metadata) VALUES(?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    str(kind)[:80],
                    str(actor)[:256],
                    str(method)[:16],
                    str(path).split("?", 1)[0][:2048],
                    int(status),
                    str(reason)[:256],
                    json.dumps(details),
                ),
            )
            db.execute(
                "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)",
                (self.settings.max_events,),
            )

    def events(self, *, limit=100, after=0):
        if not self.settings.enabled:
            return []
        with self.lock, self._connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id DESC LIMIT ?", (max(0, after), min(max(1, limit), 1000))
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def observed_paths(self):
        if not self.settings.enabled:
            return {}
        with self.lock, self._connect() as db:
            rows = db.execute(
                "SELECT path,timestamp,metadata FROM events WHERE kind='gateway_request' AND timestamp >= ?",
                (time.time() - self.settings.unused_endpoint_seconds,),
            ).fetchall()
        observed = {}
        for path, timestamp, metadata in rows:
            identifier = json.loads(metadata).get("route", path)
            observed[identifier] = max(timestamp, observed.get(identifier, 0))
        return observed

    def threats(self):
        findings = []
        for rule in self.settings.rules:
            rows = self._rule_rows(rule)
            counts = {}
            for row in rows:
                if (not rule.kinds or row["kind"] in rule.kinds) and (
                    not rule.status_codes or row["status"] in rule.status_codes
                ):
                    counts[row["actor"]] = counts.get(row["actor"], 0) + 1
            findings.extend(
                {
                    "rule": rule.name,
                    "actor": actor,
                    "count": count,
                    "window_seconds": rule.window_seconds,
                    "recommendation": rule.recommendation,
                }
                for actor, count in counts.items()
                if count >= rule.threshold
            )
        return findings

    def _rule_rows(self, rule):
        if not self.settings.enabled:
            return []
        with self.lock, self._connect() as db:
            db.row_factory = sqlite3.Row
            return db.execute(
                "SELECT kind,actor,status FROM events WHERE timestamp >= ?", (time.time() - rule.window_seconds,)
            ).fetchall()


def security_actor(scope: Scope, *, verified=False) -> str:
    state = scope.get("state", {})
    identity = state.get("management_actor")
    if identity:
        return str(identity.get("subject", "management")) if isinstance(identity, dict) else str(identity)
    if verified:
        # Shared legacy credentials are principals, not human attribution.
        return "legacy-management-credential"
    return "client:" + str((scope.get("client") or ("unknown",))[0])


class SecurityMonitoringMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        status = 500
        initial_sink = getattr(scope["app"].state, "security_audit_sink", None)

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            sink = getattr(scope["app"].state, "security_audit_sink", None)
            if initial_sink and initial_sink.settings.enabled and scope["path"].startswith("/apim/"):
                sink = initial_sink
            if sink:
                _record_request(sink, scope, status)


def _record_request(sink, scope, status):
    path, method = scope["path"], scope["method"]
    management = path.startswith("/apim/management/") or path.startswith("/apim/security/")
    reason = str(scope.get("state", {}).get("apim_result_reason", ""))
    actor = security_actor(scope, verified=management and status < 400)
    if management and method in {"PUT", "POST", "PATCH", "DELETE"}:
        kind = "management_write" if status < 400 else "management_write_denied"
    elif status == 401 or "jwt" in reason.lower():
        kind = "authentication_failure"
    elif status in {400, 413, 422}:
        kind = "validation_failure"
    else:
        kind = "gateway_request"
    sink.record_event(
        kind,
        actor,
        method,
        path,
        status,
        reason=reason,
        metadata={"route": scope.get("state", {}).get("apim_route_name", path)},
    )
    if management and kind == "management_write_denied":
        failure = _management_failure_kind(scope, status)
        if failure:
            sink.record_event(failure, actor, method, path, status, reason=reason)


def _management_failure_kind(scope, status):
    if status == 401 or (status == 403 and not scope.get("state", {}).get("management_actor")):
        return "authentication_failure"
    if status == 403:
        return "authorization_failure"
    if status in {400, 413, 422}:
        return "validation_failure"
    return None


def build_security_monitoring_router() -> APIRouter:
    router = APIRouter()

    @router.get("/apim/management/security/events")
    async def events(request: Request, limit: int = Query(100, ge=1, le=1000), after: int = Query(0, ge=0)) -> dict:
        require_tenant_access(request)
        sink = getattr(request.app.state, "security_audit_sink", None)
        return {
            "events": sink.events(limit=limit, after=after) if sink else [],
            "enabled": bool(sink and sink.settings.enabled),
        }

    @router.get("/apim/management/security/threats")
    async def threats(request: Request) -> dict:
        require_tenant_access(request)
        sink = getattr(request.app.state, "security_audit_sink", None)
        return {"findings": sink.threats() if sink else []}

    return router
