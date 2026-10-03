from __future__ import annotations

import json

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import ApiConfig, GatewayConfig, NamedValueConfig, OperationConfig, SubscriptionConfig, load_config
from app.control_plane import ControlPlaneConfig
from app.main import create_app
from app.secret_storage import SecretStorageConfig, decrypt_config, encrypt_config


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("APIM_CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)


def test_authenticated_encryption_roundtrip_and_no_plaintext_secret_in_artifact():
    payload = {"named_values": {"private": {"value": "synthetic-storage-secret", "secret": True}}}
    encrypted = encrypt_config(payload, "APIM_CONFIG_ENCRYPTION_KEY")
    assert "synthetic-storage-secret" not in json.dumps(encrypted)
    assert decrypt_config(encrypted) == payload
    assert decrypt_config(payload) is payload  # Explicit unencrypted demo compatibility.


@pytest.mark.parametrize("tamper", ["ciphertext", "wrong-key", "invalid-key-env", "invalid-ciphertext-type"])
def test_encryption_rejects_modified_or_unreadable_artifacts(monkeypatch, tamper):
    encrypted = encrypt_config({"private": "synthetic-secret"}, "APIM_CONFIG_ENCRYPTION_KEY")
    if tamper == "ciphertext":
        encrypted["ciphertext"] = (
            encrypted["ciphertext"][:20]
            + ("B" if encrypted["ciphertext"][20] == "A" else "A")
            + encrypted["ciphertext"][21:]
        )
    elif tamper == "wrong-key":
        monkeypatch.setenv("APIM_CONFIG_ENCRYPTION_KEY", Fernet.generate_key().decode())
    elif tamper == "invalid-key-env":
        encrypted["key_env"] = "OTHER_KEY"
    else:
        encrypted["ciphertext"] = None
    with pytest.raises(ValueError):
        decrypt_config(encrypted)


def test_missing_or_invalid_encryption_key_fails_closed(monkeypatch):
    monkeypatch.delenv("APIM_CONFIG_ENCRYPTION_KEY")
    with pytest.raises(ValueError, match="requires"):
        encrypt_config({}, "APIM_CONFIG_ENCRYPTION_KEY")
    monkeypatch.setenv("APIM_CONFIG_ENCRYPTION_KEY", "not-a-key")
    with pytest.raises(ValueError, match="Fernet"):
        encrypt_config({}, "APIM_CONFIG_ENCRYPTION_KEY")


def recovery_client():
    cfg = GatewayConfig(
        allow_anonymous=True,
        subscription=SubscriptionConfig(required=False),
        control_plane=ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False),
        secret_storage=SecretStorageConfig(enabled=True),
        named_values={"private": NamedValueConfig(value="synthetic-storage-secret", secret=True)},
        apis={
            "first": ApiConfig(
                name="First",
                path="first",
                upstream_base_url="https://backend.example.test",
                operations={"hello": OperationConfig(name="Hello", url_template="/hello")},
            )
        },
    )
    return TestClient(
        create_app(
            config=cfg,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"path": request.url.path}))
            ),
        )
    ), cfg


def contributor():
    return {"Authorization": "Bearer " + token(roles=["contributor"])}


def test_management_persists_encrypted_config_and_load_restores_keys(tmp_path, monkeypatch):
    config_path = tmp_path / "gateway.json"
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    client, _ = recovery_client()
    with client:
        response = client.put(
            "/apim/management/apis/second",
            headers=contributor(),
            json={
                "name": "Second",
                "path": "second",
                "upstream_base_url": "https://backend.example.test",
            },
        )
        assert response.status_code == 200
        raw = config_path.read_text()
        assert "synthetic-storage-secret" not in raw and '"ciphertext"' in raw
        restored = load_config()
        assert set(restored.apis) == {"first", "second"}
        assert restored.named_values["private"].value == "synthetic-storage-secret"
        assert restored.control_plane.enabled and not restored.control_plane.allow_legacy_tenant_keys


def test_backup_restore_recovers_configuration_and_gateway_behavior():
    client, cfg = recovery_client()
    with client:
        backup = client.post("/apim/security/backups", headers=contributor())
        assert backup.status_code == 200 and "synthetic-storage-secret" not in backup.text
        assert client.get("/first/hello").status_code == 200
        assert client.delete("/apim/management/apis/first", headers=contributor()).status_code == 200
        assert client.get("/first/hello").status_code == 404
        restored = client.post("/apim/security/restore", headers=contributor(), json=backup.json())
        assert restored.status_code == 200
        assert client.get("/first/hello").json() == {"path": "/hello"}
        assert client.app.state.gateway_config.named_values["private"].value == cfg.named_values["private"].value


def test_invalid_restore_does_not_change_live_configuration():
    client, _ = recovery_client()
    with client:
        backup = client.post("/apim/security/backups", headers=contributor()).json()
        backup["ciphertext"] = "invalid"
        for artifact in (backup, {"apis": {}}, encrypt_config({"apis": {"bad": {}}}, "APIM_CONFIG_ENCRYPTION_KEY")):
            result = client.post("/apim/security/restore", headers=contributor(), json=artifact)
            assert result.status_code == 400
            assert set(client.app.state.gateway_config.apis) == {"first"}
            assert client.get("/first/hello").status_code == 200


def test_recovery_permissions_and_encryption_key_outage(monkeypatch):
    client, _ = recovery_client()
    with client:
        reader = {"Authorization": "Bearer " + token(roles=["reader"])}
        operator = {"Authorization": "Bearer " + token(roles=["operator"])}
        assert client.post("/apim/security/backups", headers=reader).status_code == 403
        backup = client.post("/apim/security/backups", headers=operator)
        assert backup.status_code == 200
        assert client.post("/apim/security/restore", headers=operator, json=backup.json()).status_code == 403
        monkeypatch.delenv("APIM_CONFIG_ENCRYPTION_KEY")
        assert client.post("/apim/security/backups", headers=contributor()).status_code == 503
        assert set(client.app.state.gateway_config.apis) == {"first"}


@pytest.mark.parametrize(
    "change",
    [
        "portal-disabled",
        "portal-header",
        "portal-issuer",
        "portal-audience",
        "portal-key",
        "public-network",
        "private-peers",
        "trusted-proxies",
        "forwarding",
        "egress-hosts",
        "egress-cidrs",
        "admin-roles",
        "workspace-scope",
        "ingress-exclusions",
    ],
)
def test_restore_preserves_current_identity_and_network_boundaries(change):
    from app.control_plane import PortalIdentityConfig
    from app.network_security import NetworkSecurityConfig
    from app.security_settings import SecurityIngressConfig

    client, cfg = recovery_client()
    cfg.portal.identity = PortalIdentityConfig(enabled=True)
    cfg.control_plane.require_mfa = True
    cfg.control_plane.workspace_apis = {"team": ["first"]}
    cfg.service.public_network_access_enabled = False
    cfg.network_security = NetworkSecurityConfig(
        private_peer_cidrs=["192.0.2.0/24"],
        trusted_proxy_cidrs=["192.0.2.1/32"],
        allowed_backend_hosts=["backend.example.test"],
        allowed_backend_cidrs=["192.0.2.0/24"],
    )
    cfg.security_ingress = SecurityIngressConfig(enabled=True)
    # Validate recovery directly so network boundary rejects no test-host traffic.
    from fastapi import HTTPException

    from app.security_recovery import validate_recovery_security

    candidate = cfg.model_copy(deep=True)
    mutations = {
        "portal-disabled": lambda: setattr(candidate.portal.identity, "enabled", False),
        "portal-header": lambda: setattr(candidate.portal.identity, "allow_legacy_user_header", True),
        "portal-issuer": lambda: setattr(candidate.portal.identity, "issuer", "https://other-issuer.example.test"),
        "portal-audience": lambda: setattr(candidate.portal.identity, "audience", "other-audience"),
        "portal-key": lambda: setattr(candidate.portal.identity, "signing_key_env", "APIM_OTHER_KEY"),
        "public-network": lambda: setattr(candidate.service, "public_network_access_enabled", True),
        "private-peers": lambda: setattr(candidate.network_security, "private_peer_cidrs", ["0.0.0.0/0"]),
        "trusted-proxies": lambda: setattr(candidate.network_security, "trusted_proxy_cidrs", ["0.0.0.0/0"]),
        "forwarding": lambda: setattr(candidate.network_security, "allow_simulated_forwarded_headers", True),
        "egress-hosts": lambda: setattr(candidate.network_security, "allowed_backend_hosts", None),
        "egress-cidrs": lambda: setattr(candidate.network_security, "allowed_backend_cidrs", None),
        "admin-roles": lambda: setattr(candidate.control_plane, "administrator_roles", []),
        "workspace-scope": lambda: setattr(candidate.control_plane, "workspace_apis", {"team": ["first", "other"]}),
        "ingress-exclusions": lambda: setattr(candidate.security_ingress, "exclude_prefixes", ["/"]),
    }
    mutations[change]()
    with pytest.raises(HTTPException) as error:
        validate_recovery_security(cfg, candidate)
    assert error.value.status_code == 409
    assert cfg.control_plane.workspace_apis == {"team": ["first"]}


@pytest.mark.parametrize(
    "change",
    [
        "certificate-disabled",
        "certificate-optional",
        "certificate-header",
        "ca-store",
        "crl",
        "certificate-binding",
        "anonymous",
        "public-trace",
        "simulated-backend-cert",
        "workload-demo",
        "workload-issuer",
        "workload-grants",
        "workload-lifetime",
        "subscription-disable",
        "subscription-bypass",
        "oidc-trust",
        "ingress-rate-window",
        "ingress-body-timeout",
    ],
)
def test_recovery_preserves_gateway_authentication_and_certificate_controls(change):
    from fastapi import HTTPException

    from app.config import ClientCertificateConfig, OIDCConfig
    from app.security_recovery import validate_recovery_security
    from app.security_settings import SecurityIngressConfig
    from app.workload_identity import WorkloadIdentityConfig

    previous = GatewayConfig(
        allow_anonymous=False,
        oidc=OIDCConfig(issuer="https://issuer.example.test", audience="gateway", jwks={"keys": []}),
        client_certificate=ClientCertificateConfig(
            mode="required", ca_file="/tmp/local-ca.pem", crl_file="/tmp/local.crl"
        ),
        workload_identity=WorkloadIdentityConfig(audience_grants={"system-assigned": ["local-backend"]}),
        security_ingress=SecurityIngressConfig(enabled=True),
    )
    candidate = previous.model_copy(deep=True)
    mutations = {
        "certificate-disabled": lambda: setattr(candidate.client_certificate, "mode", "disabled"),
        "certificate-optional": lambda: setattr(candidate.client_certificate, "mode", "optional"),
        "certificate-header": lambda: setattr(candidate.client_certificate, "allow_simulated_headers", True),
        "ca-store": lambda: setattr(candidate.client_certificate, "ca_file", None),
        "crl": lambda: setattr(candidate.client_certificate, "crl_file", None),
        "certificate-binding": lambda: setattr(candidate.client_certificate, "cert_header", "X-Other-Cert"),
        "anonymous": lambda: setattr(candidate, "allow_anonymous", True),
        "public-trace": lambda: setattr(candidate, "trace_allow_unauthenticated", True),
        "simulated-backend-cert": lambda: setattr(candidate, "allow_simulated_certificate_authentication", True),
        "workload-demo": lambda: setattr(candidate.workload_identity, "mode", "demo"),
        "workload-issuer": lambda: setattr(candidate.workload_identity, "issuer", "https://other.example.test"),
        "workload-grants": lambda: setattr(
            candidate.workload_identity, "audience_grants", {"system-assigned": ["other"]}
        ),
        "workload-lifetime": lambda: setattr(candidate.workload_identity, "token_lifetime_seconds", 600),
        "subscription-disable": lambda: setattr(candidate.subscription, "required", False),
        "subscription-bypass": lambda: setattr(
            candidate.subscription, "bypass", [{"header": "X-Anonymous", "value": "yes"}]
        ),
        "oidc-trust": lambda: setattr(candidate.oidc, "issuer", "https://other.example.test"),
        "ingress-rate-window": lambda: setattr(candidate.security_ingress, "window_seconds", 1),
        "ingress-body-timeout": lambda: setattr(candidate.security_ingress, "body_read_timeout_seconds", 60),
    }
    mutations[change]()
    with pytest.raises(HTTPException) as error:
        validate_recovery_security(previous, candidate)
    assert error.value.status_code == 409


def test_restore_on_another_gateway_preserves_destination_region():
    source, source_cfg = recovery_client()
    source_cfg.service.region = "local-a"
    with source:
        snapshot = source.post("/apim/security/backups", headers=contributor()).json()
    destination, destination_cfg = recovery_client()
    destination_cfg.service.region = "local-b"
    destination_cfg.apis[
        "first"
    ].policies_xml = '<policies><outbound><set-header name="X-Deployment-Region" exists-action="override"><value>@(context.Deployment.Region)</value></set-header></outbound></policies>'
    with destination:
        # Add the same ordinary region-reporting policy to the restored resources.
        restored_resources = decrypt_config(snapshot)
        restored_resources["apis"]["first"]["policies_xml"] = destination_cfg.apis["first"].policies_xml
        snapshot = encrypt_config(restored_resources, "APIM_CONFIG_ENCRYPTION_KEY")
        restored = destination.post("/apim/security/restore", headers=contributor(), json=snapshot)
        assert restored.status_code == 200
        assert destination.app.state.gateway_config.service.region == "local-b"
        assert destination.get("/first/hello").headers["X-Deployment-Region"] == "local-b"


@pytest.mark.parametrize(
    "field,value",
    [
        ("verify_certificate_chain", False),
        ("verify_certificate_name", False),
        ("allow_simulated_certificate", True),
        ("ca_file", None),
        ("crl_file", None),
    ],
)
def test_recovery_preserves_existing_backend_tls_control_flags(field, value):
    from fastapi import HTTPException

    from app.config import BackendConfig
    from app.security_recovery import validate_recovery_security

    previous = GatewayConfig(
        backends={
            "secure": BackendConfig(
                url="https://backend.example.test", ca_file="/tmp/local-ca.pem", crl_file="/tmp/local.crl"
            )
        }
    )
    candidate = previous.model_copy(deep=True)
    setattr(candidate.backends["secure"], field, value)
    with pytest.raises(HTTPException) as error:
        validate_recovery_security(previous, candidate)
    assert error.value.status_code == 409


def test_secure_storage_rejects_plaintext_configuration_at_startup(tmp_path, monkeypatch):
    from app.config import load_config

    source = tmp_path / "plaintext.json"
    source.write_text(
        json.dumps(
            {
                "secret_storage": {"enabled": True},
                "named_values": {"private": {"secret": True, "value": "must-not-persist-plain"}},
            }
        )
    )
    monkeypatch.setenv("APIM_CONFIG_PATH", str(source))
    with pytest.raises(ValueError, match="requires an encrypted configuration"):
        load_config()
