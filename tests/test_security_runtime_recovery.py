from __future__ import annotations

import json
import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from app.management_service import ManagementService
from app.security_monitoring import SecurityAuditSink
from app.security_recovery import validate_recovery_security
from app.security_settings import SecurityObservabilityConfig, SecurityThreatRule

KEY = "synthetic-control-plane-key-at-least32bytes"


def _token(**claims):
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "https://local-issuer.example.test",
            "aud": "apim-management",
            "sub": "named-operator",
            "iat": now,
            "exp": now + 300,
            **claims,
        },
        KEY,
        algorithm="HS256",
    )


def test_runtime_publication_reconfigures_audit_sink_and_retains_existing_store(tmp_path):
    cfg = GatewayConfig()
    app = SimpleNamespace(state=SimpleNamespace(gateway_config=cfg))
    manager = ManagementService(
        app=app,
        serialize_gateway_config=lambda config: json.dumps(config.model_dump(mode="json")),
        build_oidc_verifiers=lambda config: {},
    )
    assert manager.apply_runtime_config(cfg).security_observability.enabled is False
    enabled = cfg.model_copy(deep=True)
    enabled.security_observability = SecurityObservabilityConfig(
        enabled=True, database_path=str(tmp_path / "audit.sqlite")
    )
    manager.apply_runtime_config(enabled)
    first = app.state.security_audit_sink
    first.record_event("management_write", "operator", "PUT", "/apis/a", 200)
    manager.apply_runtime_config(enabled.model_copy(deep=True))
    assert app.state.security_audit_sink is first
    changed = enabled.model_copy(deep=True)
    changed.security_observability.rules = []
    manager.apply_runtime_config(changed)
    assert app.state.security_audit_sink is not first
    assert app.state.security_audit_sink.events()[0]["actor"] == "operator"


def test_security_routes_authorized_actor_writes_durable_restart_and_denied_governance(tmp_path, monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    settings = SecurityObservabilityConfig(
        enabled=True,
        database_path=str(tmp_path / "events.sqlite"),
        rules=[SecurityThreatRule(name="denied-writes", kinds=["management_write_denied"], threshold=2)],
    )
    cfg = GatewayConfig(
        control_plane={"enabled": True, "allow_legacy_tenant_keys": False}, security_observability=settings
    )
    writer = {"Authorization": "Bearer " + _token(roles=["contributor"])}
    reader = {"Authorization": "Bearer " + _token(roles=["reader"])}
    app = create_app(config=cfg)
    with TestClient(app) as client:
        endpoint = "/apim/management/security/governance"
        assert client.get(endpoint).status_code == 401
        assert client.get(endpoint, headers=reader).status_code == 200
        for _ in range(2):
            assert client.put(endpoint, headers=reader, json={"mode": "deny"}).status_code == 403
        assert (
            client.put(endpoint, headers=writer, json={"mode": "deny", "encrypted_protocols": True}).status_code == 200
        )
        denied = client.put(
            "/apim/management/apis/insecure",
            headers=writer,
            json={"name": "Insecure", "path": "insecure", "upstream_base_url": "http://backend", "protocols": ["http"]},
        )
        assert denied.status_code == 403
        assert "insecure" not in app.state.gateway_config.apis
        events = client.get("/apim/management/security/events", headers=reader).json()["events"]
        assert any(event["kind"] == "management_write" and event["actor"] == "named-operator" for event in events)
        assert (
            client.get("/apim/management/security/threats", headers=reader).json()["findings"][0]["actor"]
            == "named-operator"
        )
    assert any(event["actor"] == "named-operator" for event in SecurityAuditSink(settings).events())


def test_api_scoped_contributor_cannot_restore_entire_gateway(monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    cfg = GatewayConfig(control_plane={"enabled": True, "allow_legacy_tenant_keys": False})
    headers = {"Authorization": "Bearer " + _token(apim_grants=[{"role": "contributor", "api_ids": ["a"]}])}
    with TestClient(create_app(config=cfg)) as client:
        assert client.post("/apim/security/restore", headers=headers, json={}).status_code == 403
        assert client.post("/apim/security/backups", headers=headers).status_code == 403


def test_management_delete_inherits_api_lock_without_mutating_published_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    monkeypatch.setenv("APIM_CONFIG_PATH", str(tmp_path / "gateway.yaml"))
    cfg = GatewayConfig(
        control_plane={"enabled": True, "allow_legacy_tenant_keys": False},
        apis={
            "a": {
                "name": "A",
                "path": "a",
                "upstream_base_url": "https://backend.test",
                "operations": {"get": {"name": "Get", "url_template": "/"}},
            }
        },
        security_governance={"delete_locks": {"apis": ["a"]}},
    )
    app = create_app(config=cfg)
    headers = {"Authorization": "Bearer " + _token(roles=["contributor"])}
    endpoint = "/apim/management/apis/a/operations/get"
    with TestClient(app) as client:
        assert client.delete(endpoint, headers=headers).status_code == 409
        assert client.get(endpoint, headers=headers).status_code == 200
        assert not (tmp_path / "gateway.yaml").exists()
        assert (
            client.put("/apim/management/security/governance", headers=headers, json={"delete_locks": {}}).status_code
            == 200
        )
        assert client.delete(endpoint, headers=headers).status_code == 200
        assert client.get(endpoint, headers=headers).status_code == 404


@pytest.mark.parametrize(
    "field,change",
    [
        ("control_plane", {"enabled": False}),
        ("control_plane", {"require_mfa": False}),
        ("control_plane", {"allow_legacy_tenant_keys": True}),
        ("security_governance", {"mode": "audit"}),
        ("security_governance", {"delete_locks": {}}),
        ("security_ingress", {"enabled": False}),
        ("security_ingress", {"waf_mode": "detect"}),
        ("security_ingress", {"max_body_bytes": 200}),
        ("security_observability", {"enabled": False}),
        ("secret_storage", {"enabled": False}),
    ],
)
def test_recovery_rejects_security_downgrades_without_mutating_current(field, change, tmp_path):
    cfg = GatewayConfig(
        control_plane={"enabled": True, "require_mfa": True, "allow_legacy_tenant_keys": False},
        security_governance={"mode": "deny", "delete_locks": {"apis": ["a"]}},
        security_ingress={"enabled": True, "waf_mode": "block", "max_body_bytes": 100},
        security_observability={"enabled": True, "database_path": str(tmp_path / "events.sqlite")},
        secret_storage={"enabled": True},
    )
    candidate = cfg.model_copy(deep=True)
    for name, value in change.items():
        setattr(getattr(candidate, field), name, value)
    before = cfg.model_dump()
    with pytest.raises(HTTPException) as error:
        validate_recovery_security(cfg, candidate)
    assert error.value.status_code == 409 and cfg.model_dump() == before
    validate_recovery_security(cfg, cfg.model_copy(deep=True))
