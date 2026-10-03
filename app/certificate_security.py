"""Certificate verification and per-credential TLS transports for local mTLS."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import ssl
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError
from pydantic import BaseModel, ConfigDict, model_validator

from app.tls_pool_lifecycle import LeasedTLSTransport


@dataclass(frozen=True)
class CertificateMaterial:
    cert_pem: bytes
    key_pem: bytes
    source_digest: str


class TLSCertificateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    certificate_file: str | None = None
    key_file: str | None = None
    key_vault_secret_id: str | None = None
    key_vault_identity_client_id: str | None = None
    password_env: str | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if bool(self.certificate_file) == bool(self.key_vault_secret_id):
            raise ValueError("Specify exactly one certificate file or vault secret")
        if self.key_file and not self.certificate_file:
            raise ValueError("A private key file requires a certificate file")
        return self


def certificate_material(certificate: TLSCertificateConfig | None, config: Any) -> CertificateMaterial | None:
    if certificate is None or not certificate.key_vault_secret_id:
        return None
    if config is None:
        raise ValueError("Vault certificates require gateway identity configuration")
    from app.named_values import _local_vault_value

    value = _local_vault_value(
        certificate.key_vault_secret_id, config, certificate.key_vault_identity_client_id, value_limit=32768
    )
    if value is None:
        raise ValueError("Certificate vault is not configured")
    password = os.environ.get(certificate.password_env) if certificate.password_env else None
    return pkcs12_material(value, password)


def pkcs12_material(value: str, password: str | None = None) -> CertificateMaterial:
    if len(value) > 32768:
        raise ValueError("Certificate PKCS12 bundle exceeds 32 KB")
    bundle = base64.b64decode(value, validate=True)
    key, certificate, chain = pkcs12.load_key_and_certificates(bundle, password.encode() if password else None)
    if key is None or certificate is None:
        raise ValueError("Certificate PKCS12 bundle requires a certificate and private key")
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM) + b"".join(
        item.public_bytes(serialization.Encoding.PEM) for item in chain
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return CertificateMaterial(cert_pem, key_pem, hashlib.sha256(bundle).hexdigest())


def _load_material(context: ssl.SSLContext, material: CertificateMaterial) -> None:
    with tempfile.TemporaryDirectory(prefix="apim-certificate-") as directory:
        cert_path, key_path = Path(directory) / "certificate.pem", Path(directory) / "private-key.pem"
        for path, value in zip((cert_path, key_path), (material.cert_pem, material.key_pem), strict=True):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as file:
                file.write(value)
        context.load_cert_chain(cert_path, key_path)


def load_certificates(value: str | bytes) -> list[x509.Certificate]:
    data = unquote(value).encode() if isinstance(value, str) else value
    if b"-----BEGIN CERTIFICATE-----" in data:
        return x509.load_pem_x509_certificates(data)
    return [x509.load_der_x509_certificate(base64.b64decode(data, validate=True))]


def _validate_revocation(chain: list[x509.Certificate], crl_file: str, now: datetime) -> None:
    raw = Path(crl_file).read_bytes()
    crls = [
        x509.load_pem_x509_crl(part + b"-----END X509 CRL-----\n")
        for part in raw.split(b"-----END X509 CRL-----")
        if part.strip()
    ]
    for leaf, issuer in zip(chain[:-1], chain[1:], strict=True):
        matching = [crl for crl in crls if crl.issuer == issuer.subject and crl.is_signature_valid(issuer.public_key())]
        valid = [
            crl
            for crl in matching
            if crl.last_update_utc <= now and crl.next_update_utc is not None and now <= crl.next_update_utc
        ]
        if not valid:
            raise ValueError("A current signed revocation list is required for each certificate issuer")
        if any(crl.get_revoked_certificate_by_serial_number(leaf.serial_number) is not None for crl in valid):
            raise ValueError("Client certificate is revoked")


def verify_certificate(
    value: str | bytes,
    *,
    ca_file: str | None,
    crl_file: str | None = None,
    validate_trust: bool = True,
    validate_revocation: bool = False,
    validate_not_before: bool = True,
    validate_not_after: bool = True,
) -> tuple[x509.Certificate, list[x509.Certificate]]:
    certificates = load_certificates(value)
    leaf = certificates[0]
    now = datetime.now(UTC)
    if validate_not_before and now < leaf.not_valid_before_utc:
        raise ValueError("Client certificate is not yet valid")
    if validate_not_after and now > leaf.not_valid_after_utc:
        raise ValueError("Client certificate has expired")
    chain = certificates
    if validate_trust:
        if not ca_file:
            raise ValueError("Client certificate trust store is not configured")
        roots = x509.load_pem_x509_certificates(Path(ca_file).read_bytes())
        verification_time = max(leaf.not_valid_before_utc, min(now, leaf.not_valid_after_utc))
        try:
            chain = (
                PolicyBuilder()
                .store(Store(roots))
                .time(verification_time)
                .build_client_verifier()
                .verify(leaf, certificates[1:])
                .chain
            )
        except VerificationError as exc:
            raise ValueError("Client certificate chain is not trusted") from exc
    if validate_revocation:
        if not crl_file or len(chain) < 2:
            raise ValueError("Client certificate revocation checking requires a configured chain and CRL")
        _validate_revocation(chain, crl_file, now)
    return leaf, chain


def certificate_identity(leaf: x509.Certificate) -> dict[str, str]:
    return {
        "subject": leaf.subject.rfc4514_string(),
        "issuer": leaf.issuer.rfc4514_string(),
        "thumbprint": leaf.fingerprint(hashes.SHA1()).hex().upper(),
    }


def tls_context(
    backend: Any, certificate: TLSCertificateConfig | None = None, material: CertificateMaterial | None = None
) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=backend.ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if not backend.verify_certificate_chain:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif not backend.verify_certificate_name:
        context.check_hostname = False
    if backend.crl_file:
        context.load_verify_locations(cafile=backend.crl_file)
        context.verify_flags |= ssl.VERIFY_CRL_CHECK_CHAIN
    cert_file = certificate.certificate_file if certificate else backend.client_certificate_file
    key_file = certificate.key_file if certificate else backend.client_certificate_key_file
    if cert_file:
        context.load_cert_chain(cert_file, key_file)
    if material is not None:
        _load_material(context, material)
    if backend.auth_type == "client_certificate" and not cert_file and material is None:
        raise ValueError("Client certificate backend requires a certificate and private key")
    return context


def _transport_key(
    backend: Any, certificate: TLSCertificateConfig | None, material: CertificateMaterial | None = None
) -> str:
    files = [
        backend.ca_file,
        backend.crl_file,
        certificate.certificate_file if certificate else backend.client_certificate_file,
        certificate.key_file if certificate else backend.client_certificate_key_file,
    ]
    digest = hashlib.sha256()
    digest.update(str((backend.verify_certificate_chain, backend.verify_certificate_name)).encode())
    for filename in files:
        digest.update(Path(filename).read_bytes() if filename else b"<none>")
    if material is not None:
        digest.update(material.source_digest.encode())
    return digest.hexdigest()


class TLSClientPool:
    """A credential never crosses transports; file rotation gets a fresh connection pool."""

    def __init__(self, config_provider=None, *, max_clients: int = 128) -> None:
        if max_clients < 1:
            raise ValueError("TLS client cache must permit at least one client")
        self.clients: dict[str, httpx.AsyncClient] = {}
        self.config_provider = config_provider
        self.max_clients = max_clients
        self.slots: dict[str, str] = {}
        self.retired_clients: set[httpx.AsyncClient] = set()
        self.retirement_tasks: set[asyncio.Task] = set()

    def client_for(
        self, backend: Any, fallback: httpx.AsyncClient, certificate: TLSCertificateConfig | None = None
    ) -> httpx.AsyncClient:
        if (
            certificate
            or backend.client_certificate_file
            or (backend.auth_type == "client_certificate" and not backend.allow_simulated_certificate)
        ) and not backend.url.startswith("https://"):
            raise ValueError("Client certificate authentication requires an HTTPS backend")
        configured = any(
            (
                backend.ca_file,
                backend.crl_file,
                backend.client_certificate_file,
                certificate,
                not backend.verify_certificate_chain,
                not backend.verify_certificate_name,
            )
        )
        if not configured:
            if backend.auth_type == "client_certificate" and not backend.allow_simulated_certificate:
                raise ValueError("Client certificate backend requires a real TLS certificate")
            return fallback
        material = certificate_material(certificate, self.config_provider() if self.config_provider else None)
        slot = self._slot(backend, certificate)
        key = hashlib.sha256((slot + _transport_key(backend, certificate, material)).encode()).hexdigest()
        if key not in self.clients:
            previous = self.slots.get(slot)
            if previous is not None:
                self._retire(previous)
            transport = httpx.AsyncHTTPTransport(verify=tls_context(backend, certificate, material))
            if self.config_provider is not None:
                from app.egress_security import EgressTransport

                transport = EgressTransport(transport, self.config_provider)
            self.clients[key] = httpx.AsyncClient(
                transport=LeasedTLSTransport(transport), timeout=fallback.timeout, trust_env=False
            )
            self.slots[slot] = key
            self._trim_idle_cache()
        client = self.clients.pop(key)
        self.clients[key] = client
        return client

    def _slot(self, backend, certificate):
        url = urlsplit(backend.url)
        certificate_id = None
        if certificate is not None and self.config_provider is not None:
            certificate_id = next(
                (name for name, value in self.config_provider().certificates.items() if value is certificate), None
            )
        source = certificate_id or (
            certificate.model_dump_json()
            if certificate
            else str((backend.client_certificate_file, backend.client_certificate_key_file))
        )
        return str(
            (
                url.scheme,
                url.netloc,
                backend.ca_file,
                backend.crl_file,
                backend.verify_certificate_chain,
                backend.verify_certificate_name,
                source,
            )
        )

    def _trim_idle_cache(self):
        while len(self.clients) > self.max_clients:
            self._retire(next(iter(self.clients)))

    def _retire(self, key):
        client = self.clients.pop(key)
        self.slots = {slot: current for slot, current in self.slots.items() if current != key}
        self.retired_clients.add(client)
        client._transport.retire(lambda: self._schedule_close(client))

    def _schedule_close(self, client):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._close_retired(client))
        self.retirement_tasks.add(task)
        task.add_done_callback(self.retirement_tasks.discard)

    async def _close_retired(self, client):
        try:
            await client.aclose()
        finally:
            self.retired_clients.discard(client)

    async def aclose(self) -> None:
        if self.retirement_tasks:
            await asyncio.gather(*self.retirement_tasks)
        for client in (*self.clients.values(), *self.retired_clients):
            await client.aclose()
        self.clients.clear()
        self.retired_clients.clear()
        self.slots.clear()


def select_certificate(
    config: Any, *, certificate_id: str | None = None, thumbprint: str | None = None
) -> TLSCertificateConfig:
    if certificate_id is not None:
        certificate = config.certificates.get(certificate_id)
        if certificate is None or (certificate.key_file is None and not certificate.key_vault_secret_id):
            raise ValueError("Authentication certificate and private key are not configured")
        return certificate
    for certificate in config.certificates.values():
        material = certificate_material(certificate, config)
        leaf = x509.load_pem_x509_certificate(
            material.cert_pem if material else Path(certificate.certificate_file).read_bytes()
        )
        if (certificate.key_file or certificate.key_vault_secret_id) and certificate_identity(leaf)[
            "thumbprint"
        ].casefold() == (thumbprint or "").casefold():
            return certificate
    raise ValueError("Authentication certificate thumbprint was not found")


def selected_backend_certificate(backend: Any, variables: dict, config: Any) -> TLSCertificateConfig | None:
    if variables.get("_authentication_certificate_id"):
        return select_certificate(config, certificate_id=variables["_authentication_certificate_id"])
    thumbprint = variables.get("_authentication_certificate_thumbprint")
    if thumbprint:
        return select_certificate(config, thumbprint=thumbprint)
    if backend.client_certificate_thumbprints and not backend.allow_simulated_certificate:
        for thumbprint in backend.client_certificate_thumbprints:
            try:
                return select_certificate(config, thumbprint=thumbprint)
            except ValueError:
                continue
        raise ValueError("No configured backend client certificate matches its thumbprints")
    return None
