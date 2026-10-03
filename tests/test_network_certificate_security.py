from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import ssl
import threading
from pathlib import Path
from urllib.parse import quote

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.certificate_security import certificate_identity, verify_certificate
from app.config import ApiConfig, BackendConfig, GatewayConfig, OperationConfig, RouteConfig
from app.egress_security import EgressTransport
from app.main import create_app
from app.network_security import NetworkSecurityMiddleware, forwarded_client_ip
from app.policy import PolicyRequest, PolicyRuntime, apply_inbound, parse_policies_xml
from app.workload_identity import WorkloadIdentityConfig, issue_workload_token, verify_workload_token


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    module_path = Path("examples/apim-security/generate_certs.py")
    spec = importlib.util.spec_from_file_location("security_lab_ca", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    directory = tmp_path_factory.mktemp("isolated-ca")
    module.generate(directory)
    return directory


def _network_app(config):
    async def echo(request):
        return JSONResponse({"headers": dict(request.headers), "peer": request.scope.get("apim.socket_peer")})

    app = Starlette(routes=[Route("/", echo)])
    app.add_middleware(NetworkSecurityMiddleware, config_provider=lambda: config)
    return app


def test_forged_forwarding_and_certificate_headers_are_removed_by_default():
    config = GatewayConfig()
    with TestClient(_network_app(config), client=("203.0.113.9", 40000)) as client:
        response = client.get(
            "/",
            headers={
                "X-Forwarded-For": "127.0.0.1",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "trusted.test",
                "Forwarded": "for=127.0.0.1",
                "X-Client-Cert-Thumbprint": "ABC",
            },
        )
    assert response.status_code == 200
    assert not any(
        name.startswith("x-forwarded-") or name in {"forwarded", "x-client-cert-thumbprint"}
        for name in response.json()["headers"]
    )


def test_trusted_proxy_stops_at_first_untrusted_hop():
    assert forwarded_client_ip("10.0.0.2", "127.0.0.1, 198.51.100.8, 10.0.0.1", ["10.0.0.0/24"]) == "198.51.100.8"
    cfg = GatewayConfig(network_security={"trusted_proxy_cidrs": ["10.0.0.0/24"]})
    with TestClient(_network_app(cfg), client=("10.0.0.2", 40000)) as client:
        response = client.get("/", headers={"X-Forwarded-For": "127.0.0.1, 198.51.100.8"})
    assert response.json()["headers"]["x-forwarded-for"] == "198.51.100.8"


@pytest.mark.parametrize("metadata", [{"public_network_access_enabled": False}, {"virtual_network_type": "Internal"}])
def test_private_network_access_uses_socket_peer_not_forwarded_claim(metadata):
    cfg = GatewayConfig(service=metadata, network_security={"private_peer_cidrs": ["10.0.0.0/24"]})
    with TestClient(_network_app(cfg), client=("198.51.100.8", 40000)) as client:
        assert client.get("/", headers={"X-Forwarded-For": "10.0.0.7"}).status_code == 403
    with TestClient(_network_app(cfg), client=("10.0.0.7", 40000)) as client:
        assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "scheme,selector", [("Segment", "/api/v1/health"), ("Header", "/api/health"), ("Query", "/api/health?version=v1")]
)
@pytest.mark.parametrize("offline", [False, True])
def test_selected_api_version_checks_protocol_and_online_status(scheme, selector, offline):
    cfg = GatewayConfig(
        allow_anonymous=True,
        api_version_sets={
            "versions": {
                "display_name": "Versions",
                "versioning_scheme": scheme,
                "version_header_name": "version" if scheme == "Header" else None,
                "version_query_name": "version" if scheme == "Query" else None,
            }
        },
        apis={
            "original": ApiConfig(
                name="Original",
                path="api",
                upstream_base_url="http://backend",
                protocols=["http", "https"],
                api_version_set="versions",
                operations={"health": OperationConfig(name="Health", url_template="/health")},
            ),
            "v1": ApiConfig(
                name="Secure version",
                path="api",
                upstream_base_url="http://backend",
                protocols=["https"],
                api_version_set="versions",
                api_version="v1",
                is_online=not offline,
                operations={"health": OperationConfig(name="Health", url_template="/health")},
            ),
        },
    )
    app = create_app(
        config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
    )
    headers = {"version": "v1"} if scheme == "Header" else {}
    with TestClient(app) as client:
        assert client.get(selector, headers={**headers, "X-Forwarded-Proto": "https"}).status_code == 404
        assert client.get("https://testserver" + selector, headers=headers).status_code == (404 if offline else 200)
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("name", ["bad-client", "expired", "revoked"])
def test_certificate_chain_expiry_and_signed_revocation_reject_invalid_certificates(pki, name):
    with pytest.raises(ValueError):
        verify_certificate(
            (pki / f"{name}.pem").read_bytes(),
            ca_file=str(pki / "ca.pem"),
            crl_file=str(pki / "ca.crl.pem"),
            validate_revocation=True,
        )
    leaf, chain = verify_certificate(
        (pki / "browser.pem").read_bytes(),
        ca_file=str(pki / "ca.pem"),
        crl_file=str(pki / "ca.crl.pem"),
        validate_revocation=True,
    )
    assert leaf.subject.rfc4514_string() == "CN=browser"
    assert len(chain) == 2


def test_gateway_derives_certificate_claims_from_trusted_tls_peer(pki):
    cfg = GatewayConfig(
        allow_anonymous=True,
        client_certificate={
            "mode": "required",
            "ca_file": str(pki / "ca.pem"),
            "crl_file": str(pki / "ca.crl.pem"),
            "trusted_certificates": [{"name": "browser", "subject": "CN=browser"}],
        },
        network_security={"trusted_proxy_cidrs": ["10.0.0.2/32"]},
        routes=[RouteConfig(name="test", path_prefix="/api", upstream_base_url="http://backend")],
    )
    app = create_app(
        config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
    )
    headers = {"X-Client-Cert": quote((pki / "browser.pem").read_text()), "X-Client-Cert-Subject": "CN=forged"}
    with TestClient(app, client=("203.0.113.8", 40000)) as client:
        assert client.get("/api", headers=headers).status_code == 401
    with TestClient(app, client=("10.0.0.2", 40000)) as client:
        assert client.get("/api", headers=headers).status_code == 200
        assert client.get("/api", headers={"X-Client-Cert-Subject": "CN=browser"}).status_code == 401
        assert (
            client.get("/api", headers={"X-Client-Cert": quote((pki / "revoked.pem").read_text())}).status_code == 403
        )


def test_validate_client_certificate_policy_ands_claims_ors_identities_and_handles_on_error(pki):
    cfg = GatewayConfig(client_certificate={"ca_file": str(pki / "ca.pem"), "crl_file": str(pki / "ca.crl.pem")})
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers={},
        variables={"_client_certificate_pem": (pki / "browser.pem").read_text()},
    )
    doc = parse_policies_xml(
        '<policies><inbound><validate-client-certificate><identities><identity common-name="other" />'
        '<identity common-name="browser" dns-name="browser" issuer-subject="CN=APIM isolated local lab CA" />'
        "</identities></validate-client-certificate></inbound></policies>"
    )
    assert apply_inbound([doc], req, PolicyRuntime(gateway_config=cfg)) is None
    bad = parse_policies_xml(
        '<policies><inbound><validate-client-certificate><identities><identity common-name="browser" subject="CN=other" /></identities></validate-client-certificate></inbound></policies>'
    )
    with pytest.raises(HTTPException, match="validation failed"):
        apply_inbound([bad], req, PolicyRuntime(gateway_config=cfg))
    ignored = parse_policies_xml(
        '<policies><inbound><validate-client-certificate ignore-error="true" /></inbound></policies>'
    )
    req.variables.clear()
    assert apply_inbound([ignored], req, PolicyRuntime(gateway_config=cfg)) is None


def test_signed_workload_identity_requires_audience_grant_and_verifies_signature_claims(pki):
    settings = WorkloadIdentityConfig(
        private_key_file=str(pki / "workload-private.pem"),
        audience_grants={"system-assigned": ["https://backend.local"]},
    )
    token = issue_workload_token(settings, "https://backend.local")
    verify = {
        "public_key_file": str(pki / "workload-public.pem"),
        "issuer": settings.issuer,
        "audience": "https://backend.local",
        "allowed_identities": ["system-assigned"],
    }
    assert verify_workload_token(token, **verify)["sub"] == "system-assigned"
    with pytest.raises(ValueError):
        issue_workload_token(settings, "https://ungranted.local")
    with pytest.raises(jwt.InvalidTokenError):
        verify_workload_token(token, **{**verify, "audience": "https://other.local"})
    header, payload, signature = token.split(".")
    forged_payload = jwt.utils.base64url_encode(json.dumps({"sub": "admin"}).encode()).decode()
    with pytest.raises(jwt.InvalidTokenError):
        verify_workload_token(header + "." + forged_payload + "." + signature, **verify)
    with pytest.raises(jwt.InvalidTokenError):
        verify_workload_token(token, **{**verify, "allowed_identities": ["other"]})


@pytest.mark.parametrize("url", ["http://blocked.local/api", "http://allowed.local/redirect"])
def test_outgoing_firewall_checks_backend_and_redirect_destinations(url):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"Location": "http://blocked.local/api"})

    cfg = GatewayConfig(network_security={"allowed_backend_hosts": ["allowed.local"]})

    async def run():
        async with httpx.AsyncClient(
            transport=EgressTransport(httpx.MockTransport(handler), lambda: cfg), follow_redirects=True
        ) as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(url)

    asyncio.run(run())
    assert len(seen) == (1 if "/redirect" in url else 0)


def test_outgoing_cidr_firewall_pins_approved_address_and_preserves_host_sni(monkeypatch):
    seen = []
    monkeypatch.setattr("app.egress_security.socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("127.0.0.1", 443))])

    def handler(req):
        seen.append(req)
        return httpx.Response(200)

    cfg = GatewayConfig(
        network_security={"allowed_backend_hosts": ["backend.local"], "allowed_backend_cidrs": ["127.0.0.0/8"]}
    )

    async def run():
        async with httpx.AsyncClient(transport=EgressTransport(httpx.MockTransport(handler), lambda: cfg)) as client:
            assert (await client.get("https://backend.local/path")).status_code == 200

    asyncio.run(run())
    assert seen[0].url.host == "127.0.0.1"
    assert seen[0].headers["Host"] == "backend.local"
    assert seen[0].extensions["sni_hostname"] == "backend.local"


@pytest.fixture
def tls_backend(pki, monkeypatch):
    spec = importlib.util.spec_from_file_location("security_lab_backend", Path("examples/apim-security/tls_backend.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("CERTS_DIRECTORY", str(pki))
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    server.daemon_threads = True
    server.socket = module.server_context(pki).wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize("certificate", [None, "bad-client", "expired", "revoked"])
def test_real_tls_handshake_rejects_missing_untrusted_expired_and_revoked_client_cert(pki, tls_backend, certificate):
    context = ssl.create_default_context(cafile=str(pki / "ca.pem"))
    if certificate:
        context.load_cert_chain(pki / f"{certificate}.pem", pki / f"{certificate}-key.pem")
    with httpx.Client(verify=context, trust_env=False, timeout=3) as client, pytest.raises(httpx.HTTPError):
        client.get(tls_backend)


def test_real_tls_backend_rejects_demo_token_and_accepts_signed_identity(pki, tls_backend):
    context = ssl.create_default_context(cafile=str(pki / "ca.pem"))
    context.load_cert_chain(pki / "gateway-client.pem", pki / "gateway-client-key.pem")
    settings = WorkloadIdentityConfig(
        private_key_file=str(pki / "workload-private.pem"),
        audience_grants={"system-assigned": ["https://backend.local"]},
    )
    with httpx.Client(verify=context, trust_env=False) as client:
        assert client.get(tls_backend, headers={"Authorization": "Bearer local-apim-mi.invalid"}).status_code == 401
        response = client.post(
            tls_backend + "/echo",
            json={"hello": "world"},
            headers={"Authorization": "Bearer " + issue_workload_token(settings, "https://backend.local")},
        )
        assert response.status_code == 200
        assert response.json()["body"] == {"hello": "world"}


def test_gateway_real_mtls_and_signed_identity_rotation_uses_new_certificate(pki, tls_backend, tmp_path):
    cert, key = tmp_path / "client.pem", tmp_path / "key.pem"
    shutil.copy(pki / "gateway-client.pem", cert)
    shutil.copy(pki / "gateway-client-key.pem", key)
    cfg = GatewayConfig(
        allow_anonymous=True,
        workload_identity={
            "private_key_file": str(pki / "workload-private.pem"),
            "audience_grants": {"system-assigned": ["https://backend.local"]},
        },
        backends={
            "secure": BackendConfig(
                url=tls_backend,
                auth_type="client_certificate",
                ca_file=str(pki / "ca.pem"),
                client_certificate_file=str(cert),
                client_certificate_key_file=str(key),
            )
        },
        routes=[
            RouteConfig(
                name="secure",
                path_prefix="/api",
                upstream_base_url=tls_backend,
                backend="secure",
                policies_xml='<policies><inbound><authentication-managed-identity resource="https://backend.local" /></inbound></policies>',
            )
        ],
    )
    app = create_app(config=cfg)
    with TestClient(app) as client:
        first = client.get("/api")
        assert first.status_code == 200
        shutil.copy(pki / "gateway-client-rotated.pem", cert)
        shutil.copy(pki / "gateway-client-rotated-key.pem", key)
        second = client.get("/api")
        assert second.status_code == 200
        assert first.json()["client_thumbprint"] != second.json()["client_thumbprint"]
        assert len(app.state.tls_client_pool.clients) == 1
    rotated = x509.load_pem_x509_certificate((pki / "gateway-client-rotated.pem").read_bytes())
    assert second.json()["client_thumbprint"] == certificate_identity(rotated)["thumbprint"]


def test_private_network_denial_is_in_security_audit(pki, tmp_path):
    cfg = GatewayConfig(
        service={"public_network_access_enabled": False},
        network_security={"private_peer_cidrs": ["10.0.0.0/24"]},
        security_observability={"enabled": True, "database_path": str(tmp_path / "events.sqlite")},
    )
    app = create_app(config=cfg)
    with TestClient(app, client=("198.51.100.8", 40000)) as client:
        assert client.get("/apim/health", headers={"Authorization": "Bearer secret"}).status_code == 403
        events = app.state.security_audit_sink.events()
    assert events[0]["kind"] == "ingress_denial"
    assert events[0]["reason"] == "private-network"
    assert "secret" not in json.dumps(events)


@pytest.mark.parametrize("claims", [{"iss": "https://untrusted.local"}, {"exp": 1}, {"nbf": 4102444800}])
def test_workload_verifier_rejects_issuer_expiry_and_future_validity(pki, claims):
    settings = WorkloadIdentityConfig(
        private_key_file=str(pki / "workload-private.pem"),
        audience_grants={"system-assigned": ["https://backend.local"]},
    )
    valid = jwt.decode(issue_workload_token(settings, "https://backend.local"), options={"verify_signature": False})
    token = jwt.encode({**valid, **claims}, (pki / "workload-private.pem").read_bytes(), algorithm="RS256")
    with pytest.raises(jwt.InvalidTokenError):
        verify_workload_token(
            token,
            public_key_file=str(pki / "workload-public.pem"),
            issuer=settings.issuer,
            audience="https://backend.local",
            allowed_identities=["system-assigned"],
        )


def test_real_tls_server_rejects_tls11(pki, tls_backend):
    context = ssl.create_default_context(cafile=str(pki / "ca.pem"))
    context.load_cert_chain(pki / "gateway-client.pem", pki / "gateway-client-key.pem")
    with pytest.warns(DeprecationWarning):
        context.minimum_version = ssl.TLSVersion.TLSv1_1
        context.maximum_version = ssl.TLSVersion.TLSv1_1
    context.set_ciphers("ALL:@SECLEVEL=0")
    with httpx.Client(verify=context, trust_env=False, timeout=3) as client, pytest.raises(httpx.ConnectError):
        client.get(tls_backend)


def test_jwks_discovery_cannot_bypass_outbound_firewall(monkeypatch, pki):
    from jwt.algorithms import RSAAlgorithm

    from app.security import OIDCVerifier

    public = load_pem_public_key((pki / "workload-public.pem").read_bytes())
    settings = WorkloadIdentityConfig(
        private_key_file=str(pki / "workload-private.pem"),
        audience_grants={"system-assigned": ["https://backend.local"]},
    )
    token = issue_workload_token(settings, "https://backend.local")
    token = jwt.encode(
        jwt.decode(token, options={"verify_signature": False}),
        (pki / "workload-private.pem").read_bytes(),
        algorithm="RS256",
        headers={"kid": "local"},
    )
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200, json={"keys": [{**json.loads(RSAAlgorithm.to_jwk(public)), "kid": "local", "use": "sig"}]}
        )

    monkeypatch.setattr("app.egress_security.httpx.HTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    blocked = OIDCVerifier(
        settings.issuer,
        "https://backend.local",
        jwks_uri="https://blocked.local/jwks",
        jwks=None,
        config=GatewayConfig(network_security={"allowed_backend_hosts": ["allowed.local"]}),
    )
    with pytest.raises(HTTPException) as caught:
        blocked.decode(token)
    assert caught.value.status_code == 401
    assert seen == []
    allowed = OIDCVerifier(
        settings.issuer,
        "https://backend.local",
        jwks_uri="https://allowed.local/jwks",
        jwks=None,
        config=GatewayConfig(network_security={"allowed_backend_hosts": ["allowed.local"]}),
    )
    assert allowed.decode(token)["sub"] == "system-assigned"
    assert len(seen) == 1


def test_gateway_outbound_firewall_also_denies_send_request_callout():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    xml = '<policies><inbound><send-request mode="new" response-variable-name="r"><set-url>http://blocked.local/api</set-url><set-method>GET</set-method></send-request></inbound></policies>'
    cfg = GatewayConfig(
        allow_anonymous=True,
        network_security={"allowed_backend_hosts": ["allowed.local"]},
        routes=[
            RouteConfig(name="api", path_prefix="/api", upstream_base_url="http://allowed.local", policies_xml=xml)
        ],
    )
    app = create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        assert client.get("/api").status_code == 500
    assert seen == []


def test_real_tls_vault_requires_signed_identity_and_reloads_rotated_secret(pki, tls_backend, monkeypatch, tmp_path):
    from app.named_values import resolve_named_value

    secrets_file = tmp_path / "secrets.json"
    secrets_file.write_text(json.dumps({"test": "first"}))
    monkeypatch.setenv("VAULT_SECRETS_FILE", str(secrets_file))
    monkeypatch.setenv("APIM_LOCAL_VAULT_BASE_URL", tls_backend)
    monkeypatch.setenv("APIM_LOCAL_VAULT_RESOURCE", "https://backend.local")
    monkeypatch.setenv("APIM_LOCAL_VAULT_CA_FILE", str(pki / "ca.pem"))
    monkeypatch.setenv("APIM_LOCAL_VAULT_CLIENT_CERT_FILE", str(pki / "gateway-client.pem"))
    monkeypatch.setenv("APIM_LOCAL_VAULT_CLIENT_KEY_FILE", str(pki / "gateway-client-key.pem"))
    cfg = GatewayConfig(
        workload_identity={
            "private_key_file": str(pki / "workload-private.pem"),
            "audience_grants": {"system-assigned": ["https://backend.local"]},
        },
        named_values={
            "vault": {"secret": True, "value_from_key_vault": {"secret_id": "https://vault.local/secrets/test"}}
        },
    )
    assert resolve_named_value(cfg, "vault").value == "first"
    secrets_file.write_text(json.dumps({"test": "rotated"}))
    assert resolve_named_value(cfg, "vault").value == "rotated"
    cfg.network_security.allowed_backend_hosts = []
    with pytest.raises(ValueError, match="resolve"):
        resolve_named_value(cfg, "vault")


@pytest.mark.parametrize("hostname,expected", [("secure-backend-private", 200), ("wrong.local", None)])
def test_cidr_pinned_tls_transport_preserves_original_hostname_verification(
    pki, tls_backend, monkeypatch, hostname, expected
):
    from app.certificate_security import TLSClientPool

    port = httpx.URL(tls_backend).port
    monkeypatch.setattr("app.egress_security.socket.getaddrinfo", lambda *args: [(2, 1, 6, "", ("127.0.0.1", port))])
    cfg = GatewayConfig(
        network_security={"allowed_backend_hosts": [hostname], "allowed_backend_cidrs": ["127.0.0.0/8"]}
    )
    backend = BackendConfig(
        url=f"https://{hostname}:{port}",
        ca_file=str(pki / "ca.pem"),
        client_certificate_file=str(pki / "gateway-client.pem"),
        client_certificate_key_file=str(pki / "gateway-client-key.pem"),
    )
    settings = WorkloadIdentityConfig(
        private_key_file=str(pki / "workload-private.pem"),
        audience_grants={"system-assigned": ["https://backend.local"]},
    )

    async def run():
        pool = TLSClientPool(config_provider=lambda: cfg)
        async with httpx.AsyncClient(trust_env=False) as fallback:
            client = pool.client_for(backend, fallback)
            try:
                if expected is None:
                    with pytest.raises(httpx.ConnectError):
                        await client.get(
                            backend.url,
                            headers={
                                "Authorization": "Bearer " + issue_workload_token(settings, "https://backend.local")
                            },
                        )
                else:
                    assert (
                        await client.get(
                            backend.url,
                            headers={
                                "Authorization": "Bearer " + issue_workload_token(settings, "https://backend.local")
                            },
                        )
                    ).status_code == expected
            finally:
                await pool.aclose()

    asyncio.run(run())


def test_certificate_vault_pkcs12_is_bounded_and_requires_private_key(pki):
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    from app.certificate_security import TLSCertificateConfig, pkcs12_material

    valid = base64.b64encode((pki / "gateway-client.p12").read_bytes()).decode()
    material = pkcs12_material(valid)
    assert b"BEGIN PRIVATE KEY" in material.key_pem
    for value in ("not-base64", base64.b64encode(b"not a PKCS12 bundle").decode(), "A" * 32769):
        with pytest.raises(ValueError):
            pkcs12_material(value)
    leaf = x509.load_pem_x509_certificate((pki / "browser.pem").read_bytes())
    missing_key = pkcs12.serialize_key_and_certificates(b"public-only", None, leaf, None, serialization.NoEncryption())
    with pytest.raises(ValueError, match="private key"):
        pkcs12_material(base64.b64encode(missing_key).decode())
    with pytest.raises(ValueError, match="exactly one"):
        TLSCertificateConfig(certificate_file="/cert.pem", key_vault_secret_id="https://vault/secrets/cert")


def _configure_certificate_vault(monkeypatch, pki, tls_backend, tmp_path):
    import base64

    secrets_file = tmp_path / "certificate-vault.json"
    secrets_file.write_text(
        json.dumps({"gateway-client": base64.b64encode((pki / "gateway-client.p12").read_bytes()).decode()})
    )
    settings = {
        "VAULT_SECRETS_FILE": str(secrets_file),
        "APIM_LOCAL_VAULT_BASE_URL": tls_backend,
        "APIM_LOCAL_VAULT_RESOURCE": "https://backend.local",
        "APIM_LOCAL_VAULT_CA_FILE": str(pki / "ca.pem"),
        "APIM_LOCAL_VAULT_CLIENT_CERT_FILE": str(pki / "gateway-client.pem"),
        "APIM_LOCAL_VAULT_CLIENT_KEY_FILE": str(pki / "gateway-client-key.pem"),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    cfg = GatewayConfig(
        allow_anonymous=True,
        workload_identity={
            "private_key_file": str(pki / "workload-private.pem"),
            "audience_grants": {"system-assigned": ["https://backend.local"]},
        },
        certificates={"gateway-vault": {"key_vault_secret_id": "https://vault.local/secrets/gateway-client"}},
        backends={
            "secure": BackendConfig(
                url=tls_backend,
                ca_file=str(pki / "ca.pem"),
                auth_type="managed_identity",
                managed_identity_resource="https://backend.local",
            )
        },
        routes=[
            RouteConfig(
                name="secure",
                path_prefix="/api",
                upstream_base_url=tls_backend,
                backend="secure",
                policies_xml='<policies><inbound><authentication-certificate certificate-id="gateway-vault" /></inbound></policies>',
            )
        ],
    )
    return cfg, secrets_file


def test_real_signed_certificate_vault_rotation_refreshes_tls_pool_and_removes_private_pem(
    pki, tls_backend, tmp_path, monkeypatch
):
    import base64
    import stat

    cfg, secrets_file = _configure_certificate_vault(monkeypatch, pki, tls_backend, tmp_path)
    temporary_files = []
    original_load = ssl.SSLContext.load_cert_chain

    def record_load(context, certfile, keyfile=None, password=None):
        if "apim-certificate-" in str(certfile):
            for path in (Path(certfile), Path(keyfile)):
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
                temporary_files.append(path)
        return original_load(context, certfile, keyfile, password)

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", record_load)
    app = create_app(config=cfg)
    with TestClient(app) as client:
        first = client.get("/api")
        assert first.status_code == 200
        secrets_file.write_text(
            json.dumps({"gateway-client": base64.b64encode((pki / "gateway-client-rotated.p12").read_bytes()).decode()})
        )
        second = client.get("/api")
        assert second.status_code == 200
        assert first.json()["client_thumbprint"] != second.json()["client_thumbprint"]
        assert len(app.state.tls_client_pool.clients) == 1
        assert len(temporary_files) == 4
        assert not any(path.exists() for path in temporary_files)
        assert all(len(key) == 64 for key in app.state.tls_client_pool.clients)
    leaf = x509.load_pem_x509_certificate((pki / "gateway-client-rotated.pem").read_bytes())
    assert second.json()["client_thumbprint"] == certificate_identity(leaf)["thumbprint"]


def test_certificate_vault_identity_requires_resource_grant_before_transport(pki, tls_backend, tmp_path, monkeypatch):
    from app.certificate_security import certificate_material

    cfg, _ = _configure_certificate_vault(monkeypatch, pki, tls_backend, tmp_path)
    cfg.certificates["gateway-vault"].key_vault_identity_client_id = "ungranted-identity"
    with pytest.raises(ValueError, match="not granted"):
        certificate_material(cfg.certificates["gateway-vault"], cfg)
    cfg.workload_identity.audience_grants["ungranted-identity"] = ["https://backend.local"]
    # Resource grant alone cannot bypass the vault service's identity allowlist.
    with pytest.raises(ValueError, match="resolve"):
        certificate_material(cfg.certificates["gateway-vault"], cfg)


def test_callout_certificate_selection_does_not_leak_into_main_backend(pki):
    from app.policy import apply_inbound_async

    leaf = x509.load_pem_x509_certificate((pki / "gateway-client.pem").read_bytes())
    thumbprint = certificate_identity(leaf)["thumbprint"]
    cfg = GatewayConfig(
        certificates={
            "sidecall": {
                "certificate_file": str(pki / "gateway-client.pem"),
                "key_file": str(pki / "gateway-client-key.pem"),
            }
        }
    )
    xml = f'<policies><inbound><send-request mode="new" response-variable-name="side"><set-url>https://side.local</set-url><set-method>GET</set-method><authentication-certificate thumbprint="{thumbprint}" /></send-request></inbound></policies>'
    document = parse_policies_xml(xml)
    req = PolicyRequest(method="GET", path="/api", query={}, headers={}, variables={"caller": "kept"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="side response"))
        ) as client:
            await apply_inbound_async([document], req, PolicyRuntime(gateway_config=cfg, http_client=client))

    asyncio.run(run())
    assert req.variables["side"].Body.AsString() == "side response"
    assert req.variables["caller"] == "kept"
    assert "_authentication_certificate_thumbprint" not in req.variables
    assert "_authentication_certificate_id" not in req.variables


def test_rotated_tls_pool_keeps_inflight_stream_until_close_then_retires(pki, tmp_path):
    from app.certificate_security import TLSClientPool
    from app.tls_pool_lifecycle import LeasedTLSTransport

    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    shutil.copy(pki / "gateway-client.pem", cert)
    shutil.copy(pki / "gateway-client-key.pem", key)
    backend = BackendConfig(
        url="https://backend.local",
        ca_file=str(pki / "ca.pem"),
        client_certificate_file=str(cert),
        client_certificate_key_file=str(key),
    )

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"still available after rotation"

    async def run():
        pool = TLSClientPool()
        async with httpx.AsyncClient() as fallback:
            old = pool.client_for(backend, fallback)
            await old._transport.aclose()
            old._transport = LeasedTLSTransport(httpx.MockTransport(lambda req: httpx.Response(200, stream=Body())))
            response = await old.send(old.build_request("GET", backend.url), stream=True)
            assert old._transport.active == 1
            shutil.copy(pki / "gateway-client-rotated.pem", cert)
            shutil.copy(pki / "gateway-client-rotated-key.pem", key)
            new = pool.client_for(backend, fallback)
            assert new is not old
            assert len(pool.clients) == 1
            assert not old.is_closed
            assert await response.aread() == b"still available after rotation"
            await response.aclose()
            await asyncio.gather(*pool.retirement_tasks)
            assert old.is_closed
            assert pool.retired_clients == set()
            assert not new.is_closed
            await pool.aclose()

    asyncio.run(run())


def test_tls_connection_cache_is_bounded_across_multiple_certificates(pki):
    from app.certificate_security import TLSClientPool

    async def run():
        pool = TLSClientPool(max_clients=2)
        async with httpx.AsyncClient() as fallback:
            clients = []
            for hostname in ("first.local", "second.local", "third.local"):
                backend = BackendConfig(url=f"https://{hostname}", ca_file=str(pki / "ca.pem"))
                clients.append(pool.client_for(backend, fallback))
            await asyncio.gather(*pool.retirement_tasks)
            assert len(pool.clients) == 2
            assert clients[0].is_closed
            assert not clients[1].is_closed
            assert not clients[2].is_closed
            await pool.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["new", "copy"])
def test_callout_does_not_inherit_main_backend_certificate_without_its_own_policy(pki, mode):
    from app.policy import apply_inbound_async

    selected = []

    class RecordingPool:
        def client_for(self, backend, client, certificate=None):
            selected.append(certificate)
            return client

    cfg = GatewayConfig(
        certificates={
            "main": {
                "certificate_file": str(pki / "gateway-client.pem"),
                "key_file": str(pki / "gateway-client-key.pem"),
            }
        }
    )
    xml = f'<policies><inbound><send-request mode="{mode}" response-variable-name="side"><set-url>https://side.local</set-url><set-method>GET</set-method></send-request></inbound></policies>'
    req = PolicyRequest(
        method="GET", path="/api", query={}, headers={}, variables={"_authentication_certificate_id": "main"}
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
            await apply_inbound_async(
                [parse_policies_xml(xml)],
                req,
                PolicyRuntime(gateway_config=cfg, http_client=client, tls_client_pool=RecordingPool()),
            )

    asyncio.run(run())
    assert selected == [None]
    assert req.variables["_authentication_certificate_id"] == "main"
