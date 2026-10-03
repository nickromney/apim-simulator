"""Generate disposable lab keys and encrypt its tenant configuration."""

import base64
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from app.secret_storage import encrypt_config

ROOT = Path(__file__).resolve().parent
runtime = ROOT / ".runtime"
runtime.mkdir(exist_ok=True)
env_path = runtime / "lab.env"
if env_path.exists():
    env = dict(line.split("=", 1) for line in env_path.read_text().splitlines())
else:
    env = {
        "APIM_CONFIG_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "APIM_CONTROL_PLANE_SIGNING_KEY": secrets.token_urlsafe(48),
        "APIM_PORTAL_SIGNING_KEY": secrets.token_urlsafe(48),
    }
for key, value in env.items():
    os.environ[key] = value
(runtime / "lab.env").write_text("".join(f"{key}={value}\n" for key, value in env.items()))
(runtime / "lab.env").chmod(0o600)
config = {
    "service": {
        "name": "security-lab",
        "region": "local-a",
        "public_network_access_enabled": False,
        "virtual_network_type": "Internal",
    },
    "allow_anonymous": True,
    "trace_enabled": True,
    "trace_allow_unauthenticated": False,
    "network_security": {
        "trusted_proxy_cidrs": [os.getenv("SECURITY_EDGE_PRIVATE_IP", "172.30.77.10") + "/32"],
        "private_peer_cidrs": ["172.30.77.0/24", "127.0.0.0/8"],
        "allowed_backend_hosts": ["secure-backend-private"],
    },
    "client_certificate": {"mode": "optional", "ca_file": "/certs/ca.pem", "crl_file": "/certs/ca.crl.pem"},
    "control_plane": {
        "issuer": "https://apim.local/management",
        "enabled": True,
        "allow_legacy_tenant_keys": False,
        "require_mfa": True,
        "require_compliant_device": True,
    },
    "portal": {"identity": {"enabled": True}},
    "secret_storage": {"enabled": True},
    "security_ingress": {
        "enabled": True,
        "waf_mode": "block",
        "max_body_bytes": 4096,
        "max_concurrent_requests": 10,
        "requests_per_window": 100,
        "allowed_client_networks": ["127.0.0.0/8", "172.30.77.0/24"],
    },
    "security_observability": {"enabled": True, "database_path": "/state/security-events.sqlite3"},
    "security_governance": {
        "mode": "deny",
        "encrypted_protocols": True,
        "backend_certificate_verification": True,
        "vault_secret_named_values": True,
        "private_gateway": True,
        "required_api_tags": ["owner-local"],
        "delete_locks": {"apis": ["secure"]},
    },
    "tags": {"owner-local": {"display_name": "Local security lab"}},
    "workload_identity": {
        "private_key_file": "/certs/workload-private.pem",
        "public_key_file": "/certs/workload-public.pem",
        "audience_grants": {"system-assigned": ["https://backend.local"]},
    },
    "named_values": {
        "backend-secret": {
            "secret": True,
            "value_from_key_vault": {"secret_id": "https://local-vault.example.test/secrets/backend-secret"},
        }
    },
    "certificates": {
        "gateway-vault": {"key_vault_secret_id": "https://local-vault.example.test/secrets/gateway-certificate"}
    },
    "backends": {
        "secure": {
            "url": "https://secure-backend-private:8443",
            "auth_type": "managed_identity",
            "managed_identity_resource": "https://backend.local",
            "ca_file": "/certs/ca.pem",
        }
    },
    "apis": {
        "secure": {
            "name": "Secured echo",
            "path": "secure",
            "protocols": ["https"],
            "tags": ["owner-local"],
            "subscription_required": False,
            "upstream_base_url": "https://secure-backend-private:8443",
            "policies_xml": '<policies><inbound><base /><validate-client-certificate validate-revocation="true" /><set-header name="X-Vault-Secret" exists-action="override"><value>{{backend-secret}}</value></set-header><set-backend-service backend-id="secure" /><authentication-certificate certificate-id="gateway-vault" /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>',
            "operations": {"echo": {"name": "Echo", "method": "POST", "url_template": "/echo"}},
        }
    },
}
(runtime / "config.json").write_text(json.dumps(encrypt_config(config, "APIM_CONFIG_ENCRYPTION_KEY"), indent=2) + "\n")
print("Generated encrypted lab configuration and disposable local keys")

(runtime / "vault").mkdir(exist_ok=True)
(runtime / "vault" / "secrets.json").write_text(
    json.dumps(
        {
            "backend-secret": "disposable-local-vault-value",
            "gateway-certificate": base64.b64encode((runtime / "certs" / "gateway-client.p12").read_bytes()).decode(),
        }
    )
    + "\n"
)
