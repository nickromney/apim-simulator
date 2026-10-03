from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt import InvalidTokenError, PyJWKClient

from app.certificate_security import certificate_identity, load_certificates, verify_certificate
from app.config import (
    ClientCertificateConfig,
    ClientCertificateMode,
    GatewayConfig,
    RouteConfig,
    SubscriptionIdentity,
    SubscriptionScope,
    SubscriptionState,
    TrustedClientCertificateConfig,
)
from app.gateway_errors import GatewayError, subscription_key_error


@dataclass(frozen=True)
class ClientCertContext:
    """Certificate-derived identity, or explicit demo header claims."""

    subject: str | None
    issuer: str | None
    thumbprint: str | None
    cert_pem: str | None


@dataclass(frozen=True)
class AuthContext:
    claims: dict[str, Any]
    subscription: SubscriptionIdentity | None
    subscription_products: list[str]
    subscription_scope: SubscriptionScope | None = None
    subscription_api_id: str | None = None
    client_cert: ClientCertContext | None = None


_SUPPORTED_OIDC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512", "PS256", "RS256", "RS512", "ES256"})


def build_client_principal(claims: dict[str, Any]) -> str:
    principal = {
        "auth_typ": "oauth2",
        "name_typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
        "role_typ": "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
        "claims": [
            {
                "typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
                "val": claims.get("sub", ""),
            },
            {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name", "val": claims.get("name", "")},
            {
                "typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                "val": claims.get("email", ""),
            },
            {"typ": "preferred_username", "val": claims.get("preferred_username", "")},
        ],
    }
    return base64.b64encode(json.dumps(principal).encode("utf-8")).decode("utf-8")


class OIDCVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_uri: str | None,
        jwks: dict[str, Any] | None,
        config: GatewayConfig | None = None,
        ca_file: str | None = None,
    ):
        self.issuer = issuer
        self.audience = audience
        self._jwks = jwks
        self._jwks_client = self._remote_jwk_client(jwks_uri, config, ca_file) if (jwks_uri and not jwks) else None

    @staticmethod
    def _remote_jwk_client(uri, config, ca_file):
        if config is None:
            return PyJWKClient(uri)
        from app.egress_security import GuardedJWKClient

        return GuardedJWKClient(uri, config, ca_file)

    def _get_key_from_static_jwks(self, token: str) -> Any:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        jwks = self._jwks or {}
        keys = jwks.get("keys") or []
        if not isinstance(keys, list) or not keys:
            raise HTTPException(status_code=401, detail="Invalid or expired access token")

        candidates = keys
        if kid:
            candidates = [k for k in keys if isinstance(k, dict) and k.get("kid") == kid]
        jwk = candidates[0] if candidates else None
        if not isinstance(jwk, dict):
            raise HTTPException(status_code=401, detail="Invalid or expired access token")
        try:
            return jwt.PyJWK(jwk).key
        except (InvalidTokenError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc

    def decode(self, token: str) -> dict[str, Any]:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm not in _SUPPORTED_OIDC_ALGORITHMS:
                raise HTTPException(status_code=401, detail="Invalid or expired access token")
            if self._jwks_client is not None:
                signing_key = self._jwks_client.get_signing_key_from_jwt(token)
                key = signing_key.key
            else:
                key = self._get_key_from_static_jwks(token)

            return jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self.audience,
                issuer=self.issuer,
            )
        except HTTPException:
            raise
        except InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail="Invalid or expired access token") from exc


def _unverified_claims(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": False,
            },
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired access token") from None


def _default_issuer_audience(config: GatewayConfig) -> tuple[str, str]:
    if config.oidc is not None:
        return config.oidc.issuer, config.oidc.audience
    if config.oidc_providers:
        first = next(iter(config.oidc_providers.values()))
        return first.issuer, first.audience
    return "", ""


def _subscription_bypassed(request: Request, config: GatewayConfig) -> bool:
    for cond in config.subscription.bypass:
        if cond.matches(request.headers):
            return True
    return False


def subscription_bypassed(request: Request, config: GatewayConfig) -> bool:
    return _subscription_bypassed(request, config)


def _subscription_header_names(config: GatewayConfig, route: RouteConfig | None) -> list[str]:
    if route is not None and route.subscription_header_names:
        return route.subscription_header_names
    return config.subscription.header_names


def _subscription_query_param_names(config: GatewayConfig, route: RouteConfig | None) -> list[str]:
    if route is not None and route.subscription_query_param_names:
        return route.subscription_query_param_names
    return config.subscription.query_param_names


def _get_subscription_key_optional(
    request: Request, config: GatewayConfig, route: RouteConfig | None = None
) -> str | None:
    for header_name in _subscription_header_names(config, route):
        provided = request.headers.get(header_name)
        # A present header wins even when empty: the query parameter is
        # "checked only if the header isn't present".
        # https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
        if provided is not None:
            return provided
    for query_name in _subscription_query_param_names(config, route):
        provided = request.query_params.get(query_name)
        if provided:
            return provided
    return None


def _require_active_subscription(
    request: Request, config: GatewayConfig, route: RouteConfig | None, provided_key: str
) -> None:
    sub = config.subscription.lookup_subscription_by_key(provided_key)
    if sub is None:
        return
    if sub.state != SubscriptionState.Active:
        # APIM treats a key for an inactive subscription as an invalid key.
        raise subscription_key_error(request, config, route, missing=False)


def validate_subscription_key(
    request: Request, config: GatewayConfig, route: RouteConfig | None = None
) -> SubscriptionIdentity | None:
    if not config.subscription.required:
        return None
    if _subscription_bypassed(request, config):
        return None

    provided = _get_subscription_key_optional(request, config, route)
    if not provided:
        # The docs are silent on an empty key value; it carries no key, so it
        # reads as missing rather than invalid.
        raise subscription_key_error(request, config, route, missing=True)

    _require_active_subscription(request, config, route, provided)

    identity = config.subscription.lookup_identity_by_key(provided)
    if identity is None:
        raise subscription_key_error(request, config, route, missing=False)
    return identity


def get_subscription_identity_optional(
    request: Request, config: GatewayConfig, route: RouteConfig | None = None
) -> SubscriptionIdentity | None:
    if _subscription_bypassed(request, config):
        return None

    provided = _get_subscription_key_optional(request, config, route)
    if not provided:
        return None

    _require_active_subscription(request, config, route, provided)
    identity = config.subscription.lookup_identity_by_key(provided)
    if identity is None:
        raise subscription_key_error(request, config, route, missing=False)
    return identity


def get_subscription_products_optional(
    request: Request, config: GatewayConfig, route: RouteConfig | None = None
) -> list[str]:
    if _subscription_bypassed(request, config):
        return []
    provided = _get_subscription_key_optional(request, config, route)
    if not provided:
        return []

    _require_active_subscription(request, config, route, provided)
    sub = config.subscription.lookup_subscription_by_key(provided)
    return sub.products if sub is not None else []


def require_subscription_products(
    request: Request, config: GatewayConfig, route: RouteConfig | None = None
) -> list[str]:
    if _subscription_bypassed(request, config):
        return []
    provided = _get_subscription_key_optional(request, config, route)
    if not provided:
        raise subscription_key_error(request, config, route, missing=True)

    _require_active_subscription(request, config, route, provided)
    sub = config.subscription.lookup_subscription_by_key(provided)
    if sub is None:
        # Back-compat: key->identity mode has no products.
        if config.subscription.lookup_identity_by_key(provided) is not None:
            return []
        raise subscription_key_error(request, config, route, missing=False)
    return sub.products


def _subscription_scope_optional(
    request: Request, config: GatewayConfig, route: RouteConfig | None = None
) -> tuple[SubscriptionScope | None, str | None]:
    if _subscription_bypassed(request, config):
        return None, None
    provided = _get_subscription_key_optional(request, config, route)
    if not provided:
        return None, None
    _require_active_subscription(request, config, route, provided)
    sub = config.subscription.lookup_subscription_by_key(provided)
    if sub is None:
        return None, None
    return sub.scope_kind, sub.api_id


def _anonymous_context(request: Request, config: GatewayConfig, route: RouteConfig | None) -> AuthContext:
    """The stand-in identity used when the gateway allows anonymous calls.

    Subscriptions are still read, because a product grant can apply without any
    bearer token being required.
    """
    issuer, audience = _default_issuer_audience(config)
    if route_has_open_product(config, route):
        subscription, products, scope, api_id = _lenient_subscription(request, config, route)
    else:
        subscription = get_subscription_identity_optional(request, config, route)
        products = get_subscription_products_optional(request, config, route)
        scope, api_id = _subscription_scope_optional(request, config, route)
    return AuthContext(
        claims={
            "sub": "anon-demo",
            "email": "demo@dev.test",
            "name": "Demo User",
            "preferred_username": "demo@dev.test",
            "iss": issuer,
            "aud": audience,
        },
        subscription=subscription,
        subscription_products=products,
        subscription_scope=scope,
        subscription_api_id=api_id,
    )


def _bearer_token(request: Request) -> str:
    token = ""
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return token


def _verifier_for_token(token: str, oidc_verifiers: dict[str, OIDCVerifier]) -> OIDCVerifier:
    """Pick the verifier for this token's issuer.

    With one configured provider there is nothing to choose. With several, the
    token's unverified `iss` selects one; the claims are read without validating
    them, which is safe because the chosen verifier then validates properly.
    """
    if not oidc_verifiers:
        raise HTTPException(status_code=500, detail="OIDC verifier not configured")
    if len(oidc_verifiers) == 1:
        return next(iter(oidc_verifiers.values()))

    issuer = _unverified_claims(token).get("iss")
    if isinstance(issuer, str) and issuer:
        for candidate in oidc_verifiers.values():
            if candidate.issuer == issuer:
                return candidate
    raise HTTPException(status_code=401, detail="Invalid or expired access token")


def route_has_open_product(config: GatewayConfig, route: RouteConfig | None) -> bool:
    """Whether the route's API is in a product that doesn't require a subscription.

    With an open product, APIM ignores a key it can't accept and serves the
    request in that product's context:
    https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions
    """
    if route is None:
        return False
    ids = list(route.products) if route.products else ([route.product] if route.product else [])
    return any((p := config.products.get(pid)) is not None and not p.require_subscription for pid in ids)


def _lenient_subscription(
    request: Request, config: GatewayConfig, route: RouteConfig | None
) -> tuple[SubscriptionIdentity | None, list[str], SubscriptionScope | None, str | None]:
    """Read a subscription key, dropping one that can't be accepted (open product)."""
    try:
        scope, api_id = _subscription_scope_optional(request, config, route)
        return (
            get_subscription_identity_optional(request, config, route),
            get_subscription_products_optional(request, config, route),
            scope,
            api_id,
        )
    except GatewayError:
        return None, [], None, None


def authenticate_request(
    request: Request, config: GatewayConfig, oidc_verifiers: dict[str, OIDCVerifier], route: RouteConfig | None = None
) -> AuthContext:
    """Establish who is calling, from a subscription key and a bearer token."""
    if config.allow_anonymous:
        return _anonymous_context(request, config, route)

    if route_has_open_product(config, route):
        subscription, products, scope, api_id = _lenient_subscription(request, config, route)
    else:
        subscription, products, scope, api_id = _strict_subscription(request, config, route)

    token = _bearer_token(request)
    verifier = _verifier_for_token(token, oidc_verifiers)
    return AuthContext(
        claims=verifier.decode(token),
        subscription=subscription,
        subscription_products=products,
        subscription_scope=scope,
        subscription_api_id=api_id,
    )


def _strict_subscription(
    request: Request, config: GatewayConfig, route: RouteConfig | None
) -> tuple[SubscriptionIdentity | None, list[str], SubscriptionScope | None, str | None]:
    """Read a subscription key, rejecting a missing or invalid one when required."""
    subscription = validate_subscription_key(request, config, route)
    if config.subscription.required:
        products = require_subscription_products(request, config, route)
    else:
        products = get_subscription_products_optional(request, config, route)
    scope, api_id = _subscription_scope_optional(request, config, route)
    return subscription, products, scope, api_id


def _extract_client_cert_context(request: Request, cert_cfg: ClientCertificateConfig) -> ClientCertContext | None:
    """Extract client certificate info from proxy headers."""
    subject = request.headers.get(cert_cfg.subject_header)
    issuer = request.headers.get(cert_cfg.issuer_header)
    thumbprint = request.headers.get(cert_cfg.thumbprint_header)
    cert_pem = request.headers.get(cert_cfg.cert_header)

    if not subject and not issuer and not thumbprint and not cert_pem:
        return None

    return ClientCertContext(
        subject=subject,
        issuer=issuer,
        thumbprint=thumbprint.upper() if thumbprint else None,
        cert_pem=cert_pem,
    )


def extract_request_certificate(request: Request, config: GatewayConfig) -> ClientCertContext | None:
    """Read a certificate from actual TLS or an attested TLS terminator."""
    settings = config.client_certificate
    tls_chain = request.scope.get("extensions", {}).get("tls", {}).get("client_cert_chain") or []
    if tls_chain:
        pem = "\n".join(tls_chain)
    elif request.scope.get("apim.trusted_proxy"):
        pem = request.headers.get(settings.cert_header)
    elif settings.allow_simulated_headers:
        return _extract_client_cert_context(request, settings)
    else:
        return None
    if not pem:
        return None
    try:
        leaf = load_certificates(pem)[0]
        identity = certificate_identity(leaf)
    except ValueError as exc:
        raise HTTPException(403, "Invalid client certificate") from exc
    request.scope["apim.client_certificate_pem"] = pem
    return ClientCertContext(**identity, cert_pem=pem)


def _cert_matches_trusted(cert: ClientCertContext, trusted: TrustedClientCertificateConfig) -> bool:
    """Check every configured claim on one trusted identity.

    Microsoft documents OR across identities and AND across the claims on an
    identity. Subject and issuer are distinguished-name claims, not substring
    filters.
    https://learn.microsoft.com/en-us/azure/api-management/validate-client-certificate-policy
    """
    criteria = (
        (trusted.thumbprint, cert.thumbprint, str.casefold),
        (trusted.subject, cert.subject, lambda value: value),
        (trusted.issuer, cert.issuer, lambda value: value),
    )
    configured = [(expected, actual, normalize) for expected, actual, normalize in criteria if expected is not None]
    return bool(configured) and all(
        actual is not None and normalize(actual) == normalize(expected) for expected, actual, normalize in configured
    )


def require_admin(request: Request) -> None:
    cfg: GatewayConfig = request.app.state.gateway_config
    if not cfg.admin_token:
        raise HTTPException(status_code=404, detail="Not found")
    provided = request.headers.get("x-apim-admin-token", "")
    if provided != cfg.admin_token:
        raise HTTPException(status_code=403, detail="Forbidden")


def require_tenant_access(request: Request, *, permission=None, api_id: str | None = None) -> None:
    from app.control_plane import authorize_operator

    cfg: GatewayConfig = request.app.state.gateway_config
    if not cfg.tenant_access.enabled and not cfg.control_plane.enabled:
        raise HTTPException(status_code=404, detail="Not found")
    if cfg.control_plane.enabled and request.headers.get("authorization"):
        authorize_operator(request, cfg.control_plane, permission=permission, api_id=api_id)
        return
    if cfg.control_plane.enabled and not cfg.control_plane.allow_legacy_tenant_keys:
        authorize_operator(request, cfg.control_plane, permission=permission, api_id=api_id)
        return
    admin = request.headers.get("x-apim-admin-token", "")
    provided = request.headers.get("x-apim-tenant-key", "")
    admin_valid = bool(cfg.admin_token and admin == cfg.admin_token)
    tenant_valid = bool(provided and provided in {cfg.tenant_access.primary_key, cfg.tenant_access.secondary_key})
    if not admin_valid and not tenant_valid:
        raise HTTPException(status_code=403, detail="Forbidden")
    request.state.management_actor = {
        "subject": "legacy-admin" if admin_valid else "legacy-tenant-key",
        "roles": ["contributor"],
        "authentication": "legacy-shared-key",
    }


def _verify_certificate_trust(request: Request, cert_ctx: ClientCertContext, cert_cfg: ClientCertificateConfig) -> None:
    if cert_cfg.allow_simulated_headers:
        return
    try:
        verify_certificate(
            cert_ctx.cert_pem or "",
            ca_file=cert_cfg.ca_file,
            crl_file=cert_cfg.crl_file,
            validate_revocation=bool(cert_cfg.crl_file),
        )
    except (ValueError, OSError) as exc:
        raise HTTPException(403, "Client certificate not trusted or no longer valid") from exc
    request.scope["apim.client_certificate_verified"] = True


def validate_client_certificate(request: Request, config: GatewayConfig) -> ClientCertContext | None:
    """Validate client certificate based on gateway config.

    Returns:
        ClientCertContext if cert present and valid (or mode=disabled/optional with no cert)
        Raises HTTPException if mode=required and no cert, or cert doesn't match trusted list
    """
    cert_cfg = config.client_certificate
    mode = cert_cfg.mode

    cert_ctx = extract_request_certificate(request, config)
    if mode == ClientCertificateMode.Disabled:
        return cert_ctx

    if mode == ClientCertificateMode.Required and cert_ctx is None:
        raise HTTPException(status_code=401, detail="Client certificate required")

    if cert_ctx is None:
        return None

    _verify_certificate_trust(request, cert_ctx, cert_cfg)

    # If we have trusted certificates, validate against them
    if cert_cfg.trusted_certificates:
        for trusted in cert_cfg.trusted_certificates:
            if _cert_matches_trusted(cert_ctx, trusted):
                return cert_ctx
        raise HTTPException(status_code=403, detail="Client certificate not trusted")

    # No trusted list configured - accept any cert
    return cert_ctx
