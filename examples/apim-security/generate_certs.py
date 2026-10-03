"""Generate isolated local PKI material; never install its CA in the host trust store."""

from __future__ import annotations

import argparse
import ipaddress
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write(path: Path, value: bytes, *, private: bool = False):
    path.write_bytes(value)
    path.chmod(0o600 if private else 0o644)


def _private(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _ca(name: str):
    key = _key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _leaf(name, ca_key, ca_cert, *, server=False, expired=False):
    key = _key()
    now = datetime.now(UTC)
    names = [x509.DNSName(name)]
    if server:
        names += [
            x509.DNSName("localhost"),
            x509.DNSName("edge"),
            x509.DNSName("secure-backend"),
            x509.DNSName("secure-backend-private"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def generate(directory: Path) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    ca_key, ca_cert = _ca("APIM isolated local lab CA")
    bad_key, bad_ca = _ca("Untrusted local CA")
    _write(directory / "ca.pem", ca_cert.public_bytes(serialization.Encoding.PEM))
    _write(directory / "bad-ca.pem", bad_ca.public_bytes(serialization.Encoding.PEM))
    revoked = None
    for name in (
        "edge",
        "backend",
        "gateway-client",
        "gateway-client-rotated",
        "browser",
        "revoked",
        "expired",
        "bad-client",
    ):
        key, certificate = _leaf(
            name,
            bad_key if name == "bad-client" else ca_key,
            bad_ca if name == "bad-client" else ca_cert,
            server=name in {"edge", "backend"},
            expired=name == "expired",
        )
        _write(directory / f"{name}.pem", certificate.public_bytes(serialization.Encoding.PEM))
        _write(directory / f"{name}-key.pem", _private(key), private=True)
        if name in {"gateway-client", "gateway-client-rotated"}:
            _write(
                directory / f"{name}.p12",
                pkcs12.serialize_key_and_certificates(
                    name.encode(), key, certificate, [ca_cert], serialization.NoEncryption()
                ),
                private=True,
            )
        if name == "revoked":
            revoked = certificate
    now = datetime.now(UTC)
    crl = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(ca_cert.subject)
        .last_update(now - timedelta(minutes=1))
        .next_update(now + timedelta(days=7))
        .add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(revoked.serial_number)
            .revocation_date(now - timedelta(minutes=1))
            .build()
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write(directory / "ca.crl.pem", crl.public_bytes(serialization.Encoding.PEM))
    workload_key = _key()
    _write(directory / "workload-private.pem", _private(workload_key), private=True)
    _write(
        directory / "workload-public.pem",
        workload_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )
    return {path.name: str(path) for path in directory.iterdir()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--container-readable",
        action="store_true",
        help="Explicitly make ephemeral lab keys readable by nonroot bind-mounted containers",
    )
    args = parser.parse_args()
    files = generate(args.directory)
    if args.container_readable:
        args.directory.chmod(0o755)
        for filename in files.values():
            os.chmod(filename, 0o644)


if __name__ == "__main__":
    main()
