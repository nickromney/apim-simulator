from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.config import ApiConfig, BackendConfig, GatewayConfig, NamedValueConfig, RouteConfig, SubscriptionConfig
from app.security_governance import (
    enforce_backend_transport,
    governance_findings,
    security_posture,
    validate_governance_mutation,
)
from app.security_monitoring import SecurityAuditSink, security_actor
from app.security_settings import SecurityGovernanceConfig, SecurityObservabilityConfig, SecurityThreatRule


@pytest.mark.parametrize(
    "control,config",
    [
        (
            "encrypted-protocols",
            {
                "apis": {
                    "a": {"name": "A", "path": "a", "upstream_base_url": "https://example.test", "protocols": ["http"]}
                }
            },
        ),
        (
            "backend-certificate-verification",
            {"backends": {"b": {"url": "https://example.test", "verify_certificate_name": False}}},
        ),
        ("vault-secret-named-values", {"named_values": {"n": {"secret": True, "value": "local-secret"}}}),
        ("private-gateway", {}),
        ("required-tags", {"apis": {"a": {"name": "A", "path": "a", "upstream_base_url": "https://example.test"}}}),
    ],
)
def test_governance_deny_and_audit_same_findings(control, config):
    fields = {
        "encrypted-protocols": "encrypted_protocols",
        "backend-certificate-verification": "backend_certificate_verification",
        "vault-secret-named-values": "vault_secret_named_values",
        "private-gateway": "private_gateway",
    }
    options = {fields[control]: True} if control in fields else {"required_api_tags": ["owner"]}
    cfg = GatewayConfig(**config, security_governance=SecurityGovernanceConfig(mode="audit", **options))
    assert validate_governance_mutation(None, cfg)[0]["control"] == control
    cfg.security_governance.mode = "deny"
    with pytest.raises(HTTPException) as error:
        validate_governance_mutation(None, cfg)
    assert error.value.status_code == 403
    assert governance_findings(cfg)[0]["control"] == control


def test_secure_configuration_and_delete_lock_cannot_be_removed_in_same_mutation():
    cfg = GatewayConfig(
        apis={
            "a": ApiConfig(
                name="A", path="a", upstream_base_url="https://backend.test", protocols=["https"], tags=["owner"]
            )
        },
        backends={"b": BackendConfig(url="https://backend.test")},
        named_values={
            "n": NamedValueConfig(
                secret=True, value_from_key_vault={"secret_id": "https://vault.example.test/secrets/n"}
            )
        },
        service={"public_network_access_enabled": False, "virtual_network_type": "Internal"},
        network_security={"private_peer_cidrs": ["127.0.0.1/32"]},
        security_ingress={"enabled": True, "allowed_client_networks": ["127.0.0.1/32"]},
        security_governance=SecurityGovernanceConfig(
            mode="deny",
            encrypted_protocols=True,
            backend_certificate_verification=True,
            vault_secret_named_values=True,
            private_gateway=True,
            required_api_tags=["owner"],
            delete_locks={"apis": ["a"]},
        ),
    )
    assert validate_governance_mutation(None, cfg) == []
    changed = cfg.model_copy(deep=True)
    del changed.apis["a"]
    changed.security_governance.delete_locks = {}
    with pytest.raises(HTTPException) as error:
        validate_governance_mutation(cfg, changed)
    assert error.value.status_code == 409 and "a" in cfg.apis
    unlocked = cfg.model_copy(deep=True)
    unlocked.security_governance.delete_locks = {}
    assert validate_governance_mutation(cfg, unlocked) == []
    assert validate_governance_mutation(unlocked, changed) == []


def test_unknown_lock_collection_rejected():
    cfg = GatewayConfig(security_governance={"delete_locks": {"typo": ["a"]}})
    with pytest.raises(HTTPException, match="Unknown delete-lock"):
        validate_governance_mutation(None, cfg)


@pytest.mark.parametrize(
    "resource",
    [
        "operations",
        "schemas",
        "revisions",
        "releases",
        "policy",
        "operation-policy",
        "revision-operation",
        "resolver",
        "graphql",
    ],
)
def test_api_delete_lock_inherits_to_descendants_and_requires_separate_unlock(resource):
    cfg = GatewayConfig(
        apis={
            "a": {
                "name": "A",
                "path": "a",
                "upstream_base_url": "https://backend.test",
                "policies_xml": "<policies />",
                "operations": {"get": {"name": "Get", "url_template": "/", "policies_xml": "<policies />"}},
                "schemas": {"schema": {"content_type": "application/json"}},
                "revisions": {"2": {"revision": "2", "definition": {"operations": {"get": {"name": "Get"}}}}},
                "releases": {"release": {"name": "release", "revision": "2"}},
                "graphql": {
                    "schema_document": "type Query { value: String }",
                    "resolvers": {
                        "value": {
                            "name": "value",
                            "type_name": "Query",
                            "field_name": "value",
                            "policies_xml": "<http-data-source />",
                        }
                    },
                },
            }
        },
        security_governance={"delete_locks": {"apis": ["a"]}},
    )
    changed = cfg.model_copy(deep=True)
    api = changed.apis["a"]
    if resource in {"operations", "schemas", "revisions", "releases"}:
        getattr(api, resource).clear()
    elif resource == "policy":
        api.policies_xml = None
    elif resource == "operation-policy":
        api.operations["get"].policies_xml = None
    elif resource == "revision-operation":
        api.revisions["2"].definition["operations"].clear()
    elif resource == "resolver":
        api.graphql.resolvers.clear()
    else:
        api.graphql = None
    changed.security_governance.delete_locks = {}
    with pytest.raises(HTTPException) as error:
        validate_governance_mutation(cfg, changed)
    assert error.value.status_code == 409 and "apis/a/" in error.value.detail
    unlocked = cfg.model_copy(deep=True)
    unlocked.security_governance.delete_locks = {}
    assert validate_governance_mutation(cfg, unlocked) == []
    assert validate_governance_mutation(unlocked, changed) == []
    edited = cfg.model_copy(deep=True)
    edited.apis["a"].operations["get"].description = "Allowed metadata update"
    assert validate_governance_mutation(cfg, edited) == []


def test_dynamic_backend_transport_cannot_bypass_declared_certificate_governance():
    cfg = GatewayConfig(security_governance={"mode": "deny", "backend_certificate_verification": True})
    enforce_backend_transport(cfg, "https://backend.example.test")
    with pytest.raises(HTTPException) as error:
        enforce_backend_transport(cfg, "http://policy-selected-backend.example.test")
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        enforce_backend_transport(
            cfg,
            "https://backend.example.test",
            BackendConfig(url="https://backend.example.test", verify_certificate_chain=False),
        )
    cfg.security_governance.mode = "audit"
    enforce_backend_transport(cfg, "http://backend.example.test")


def test_posture_distinguishes_declared_public_and_unobserved_routes():
    cfg = GatewayConfig(
        allow_anonymous=True,
        subscription=SubscriptionConfig(required=False),
        routes=[RouteConfig(name="public", path_prefix="/api", upstream_base_url="http://backend")],
    )
    findings = security_posture(cfg)
    assert {f["control"] for f in findings} == {"unauthenticated-endpoint", "unobserved-endpoint"}
    assert {f["control"] for f in security_posture(cfg, {"public": 100})} == {"unauthenticated-endpoint"}
    cfg.routes[0].policies_xml = '<policies><inbound><validate-jwt header-name="Authorization" /></inbound></policies>'
    assert security_posture(cfg, {"public": 100}) == []


def test_posture_does_not_treat_header_shape_or_open_products_as_authentication():
    cfg = GatewayConfig(
        allow_anonymous=True,
        products={"open": {"name": "Open", "require_subscription": False}},
        routes=[
            RouteConfig(
                name="public",
                path_prefix="/api",
                upstream_base_url="http://backend",
                products=["open"],
                policies_xml='<policies><inbound><check-header name="Content-Type" failed-check-httpcode="400" failed-check-error-message="wrong" ignore-case="true" /></inbound></policies>',
            )
        ],
    )
    assert {finding["control"] for finding in security_posture(cfg, {"public": 100})} == {"unauthenticated-endpoint"}


def test_audit_restarts_retains_bounded_events_and_never_stores_body_headers_queries(tmp_path):
    settings = SecurityObservabilityConfig(
        enabled=True,
        database_path=str(tmp_path / "audit.sqlite"),
        max_events=100,
        rules=[
            SecurityThreatRule(
                name="failed-auth", kinds=["authentication_failure"], threshold=3, recommendation="Review credentials"
            )
        ],
    )
    sink = SecurityAuditSink(settings)
    for _ in range(102):
        sink.record_event(
            "authentication_failure",
            "caller",
            "POST",
            "/api?secret=query",
            401,
            metadata={"Authorization": "Bearer secret", "body": "private", "resource": "api"},
        )
    restarted = SecurityAuditSink(settings)
    assert len(restarted.events(limit=1000)) == 100
    serialized = json.dumps(restarted.events(limit=1000))
    assert "secret" not in serialized and "private" not in serialized and "Authorization" not in serialized
    assert restarted.threats() == [
        {
            "rule": "failed-auth",
            "actor": "caller",
            "count": 100,
            "window_seconds": 60,
            "recommendation": "Review credentials",
        }
    ]
    restarted.record_event("gateway_request", "caller", "GET", "/api/echo", 200, metadata={"route": "api:echo"})
    assert "api:echo" in restarted.observed_paths()
    assert restarted.events(after=restarted.events(limit=1)[0]["id"]) == []


def test_disabled_audit_creates_no_file_and_trusted_actor_not_user_header(tmp_path):
    path = tmp_path / "disabled.sqlite"
    sink = SecurityAuditSink(SecurityObservabilityConfig(database_path=str(path)))
    sink.record_event("management_write", "operator", "PUT", "/apis", 200)
    assert not path.exists() and sink.events() == [] and sink.threats() == []
    assert (
        security_actor({"client": ("127.0.0.1", 1), "headers": [(b"x-actor", b"administrator")]}) == "client:127.0.0.1"
    )
    assert (
        security_actor({"state": {"management_actor": {"subject": "signed-operator", "roles": ["writer"]}}})
        == "signed-operator"
    )


def test_threat_rules_filter_status_isolate_actor_and_expire(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import app.security_monitoring as monitoring

    clock = SimpleNamespace(time=lambda: 100)
    monkeypatch.setattr(monitoring, "time", clock)
    sink = SecurityAuditSink(
        SecurityObservabilityConfig(
            enabled=True,
            database_path=str(tmp_path / "rules.sqlite"),
            rules=[SecurityThreatRule(name="probes", kinds=["validation_failure"], status_codes=[400], threshold=2)],
        )
    )
    for actor, status in [("a", 400), ("b", 400), ("a", 422)]:
        sink.record_event("validation_failure", actor, "POST", "/api", status)
    assert sink.threats() == []
    sink.record_event("validation_failure", "a", "POST", "/api", 400)
    assert sink.threats()[0]["actor"] == "a" and sink.threats()[0]["count"] == 2
    clock.time = lambda: 200
    assert sink.threats() == []
