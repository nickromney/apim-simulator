"""The documented validate-client-certificate policy using a real local CA/CRL."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from app.certificate_security import verify_certificate
from app.policy import PolicyNode

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

    from app.policy import PolicyRequest, PolicyRuntime


def _certificate_claims(leaf: x509.Certificate, chain: list[x509.Certificate]) -> dict[str, str]:
    common_names = leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    claims = {
        "thumbprint": leaf.fingerprint(hashes.SHA1()).hex(),
        "serial-number": format(leaf.serial_number, "X"),
        "subject": leaf.subject.rfc4514_string(),
        "issuer-subject": leaf.issuer.rfc4514_string(),
        "common-name": common_names[0].value if common_names else "",
    }
    if len(chain) > 1:
        claims["issuer-thumbprint"] = chain[1].fingerprint(hashes.SHA1()).hex()
    return claims


def _dns_matches(leaf, expected):
    try:
        names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.DNSName
        )
    except x509.ExtensionNotFound:
        return False
    return expected.casefold() in {name.casefold() for name in names}


def _issuer_matches(leaf, expected, config):
    certificate = config.certificates.get(expected)
    if certificate is None:
        return False
    issuer = x509.load_pem_x509_certificate(Path(certificate.certificate_file).read_bytes())
    try:
        leaf.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def _identity_matches(identity: dict[str, str], leaf: x509.Certificate, chain: list[x509.Certificate], config) -> bool:
    claims = _certificate_claims(leaf, chain)
    for name, expected in identity.items():
        if name == "dns-name":
            if not _dns_matches(leaf, expected):
                return False
        elif name == "issuer-certificate-id":
            if not _issuer_matches(leaf, expected, config):
                return False
        elif not _claim_matches(claims, name, expected):
            return False
    return bool(identity)


def _claim_matches(claims, name, expected):
    if name in {"thumbprint", "issuer-thumbprint", "serial-number"}:
        return claims.get(name, "").casefold() == expected.casefold()
    return claims.get(name) == expected


@dataclass(frozen=True)
class ValidateClientCertificate(PolicyNode):
    validate_trust: bool = True
    validate_revocation: bool = True
    validate_not_before: bool = True
    validate_not_after: bool = True
    ignore_error: bool = False
    identities: tuple[dict[str, str], ...] = ()

    def apply(self, req: PolicyRequest, runtime: PolicyRuntime | None = None):
        config = runtime.gateway_config if runtime else None
        settings = config.client_certificate if config else None
        try:
            if settings is None or not req.variables.get("_client_certificate_pem"):
                raise ValueError("Client certificate is required")
            leaf, chain = verify_certificate(
                req.variables["_client_certificate_pem"],
                ca_file=settings.ca_file,
                crl_file=settings.crl_file,
                validate_trust=self.validate_trust,
                validate_revocation=self.validate_revocation,
                validate_not_before=self.validate_not_before,
                validate_not_after=self.validate_not_after,
            )
            if self.identities and not any(
                _identity_matches(identity, leaf, chain, config) for identity in self.identities
            ):
                raise ValueError("Client certificate identity does not match")
        except (ValueError, OSError) as exc:
            if self.ignore_error:
                return None
            raise HTTPException(403, "Client certificate validation failed") from exc
        return None


def _bool_attribute(element: Element, name: str, default: bool) -> bool:
    value = element.attrib.get(name, str(default).lower())
    if value not in {"true", "false"}:
        raise ValueError(f"validate-client-certificate {name} must be a literal boolean")
    return value == "true"


def _identities(element: Element) -> tuple[dict[str, str], ...]:
    identities = []
    allowed = {
        "thumbprint",
        "serial-number",
        "common-name",
        "subject",
        "dns-name",
        "issuer-subject",
        "issuer-thumbprint",
        "issuer-certificate-id",
    }
    for child in element:
        if child.tag != "identities":
            raise ValueError("validate-client-certificate supports only identities")
        for identity in child:
            if identity.tag != "identity" or not identity.attrib or set(identity.attrib) - allowed:
                raise ValueError("Invalid validate-client-certificate identity")
            if (
                "issuer-certificate-id" in identity.attrib
                and {"issuer-subject", "issuer-thumbprint"} & identity.attrib.keys()
            ):
                raise ValueError("issuer-certificate-id is mutually exclusive with issuer claims")
            identities.append(dict(identity.attrib))
    if len(identities) > 10:
        raise ValueError("validate-client-certificate supports at most ten identities")
    return tuple(identities)


def parse_certificate_policy(element: Element) -> PolicyNode | None:
    if element.tag != "validate-client-certificate":
        return None
    names = ["validate-trust", "validate-revocation", "validate-not-before", "validate-not-after", "ignore-error"]
    if set(element.attrib) - {*names, "id"}:
        raise ValueError("Unsupported validate-client-certificate attribute")
    settings = {name.replace("-", "_"): _bool_attribute(element, name, name != "ignore-error") for name in names}
    return ValidateClientCertificate(**settings, identities=_identities(element))
