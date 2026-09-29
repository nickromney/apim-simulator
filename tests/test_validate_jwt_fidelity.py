from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.policy import (
    PolicyRequest,
    PolicyRuntime,
    ValidateJwt,
    _load_openid_configuration,
    parse_policies_xml,
)
from app.security import OIDCVerifier

VALIDATE_JWT_DOC = "https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy"
ERROR_HANDLING_DOC = "https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies"


def _request(token: str | None = None) -> PolicyRequest:
    return PolicyRequest(
        method="GET",
        path="/api/resource",
        query={},
        headers={"authorization": f"Bearer {token}"} if token is not None else {},
        variables={},
    )


def _runtime(client: httpx.AsyncClient, **kwargs: Any) -> PolicyRuntime:
    return PolicyRuntime(http_client=client, **kwargs)


def _run(node: ValidateJwt, request: PolicyRequest, runtime: PolicyRuntime):
    return asyncio.run(node.apply_async(request, runtime))


def _rsa_material() -> tuple[dict[str, Any], rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = "rsa-kid"
    return jwk, private_key


def _signed_token(key: Any, algorithm: str, **claims: Any) -> str:
    payload = {"iss": "https://issuer.example", "aud": "api", "exp": int(time.time()) + 300, **claims}
    return jwt.encode(payload, key, algorithm=algorithm, headers={"kid": "rsa-kid"})


def _static_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))


def test_validate_jwt_accepts_expression_attributes_and_resolves_them_per_request() -> None:
    """The policy allows expressions for failure status/message and evaluates them at request time.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    document = parse_policies_xml(
        """
        <policies>
          <inbound>
            <validate-jwt header-name="Authorization"
                failed-validation-httpcode="@(418)"
                failed-validation-error-message="@('bad')">
              <issuer-signing-keys><key>c2VjcmV0</key></issuer-signing-keys>
            </validate-jwt>
          </inbound>
        </policies>
        """
    )

    client = _static_client()
    try:
        request = _request()
        response = _run(document.inbound[0], request, _runtime(client))
    finally:
        asyncio.run(client.aclose())

    assert response is not None
    assert response.status_code == 418
    assert response.body == b"bad"


def test_validate_jwt_requires_one_token_source_and_nonempty_audiences() -> None:
    """The policy requires exactly one token source and at least one audience value.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    with pytest.raises(HTTPException, match="exactly one"):
        parse_policies_xml(
            "<policies><inbound><validate-jwt><issuer-signing-keys><key>c2VjcmV0</key></issuer-signing-keys></validate-jwt></inbound></policies>"
        )
    with pytest.raises(HTTPException, match="at least one audience"):
        parse_policies_xml(
            '<policies><inbound><validate-jwt header-name="Authorization"><audiences /></validate-jwt></inbound></policies>'
        )


def test_validate_jwt_rejects_invalid_required_claim_match_and_decryption_keys() -> None:
    """Only `all` and `any` claim matching is documented; JWE decryption is explicitly unsupported locally.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    with pytest.raises(HTTPException, match="match must be"):
        parse_policies_xml(
            """
            <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer">
              <required-claims><claim name="role" match="sometimes"><value>admin</value></claim></required-claims>
            </validate-jwt></inbound></policies>
            """
        )
    with pytest.raises(HTTPException, match="decryption-keys.*unsupported"):
        parse_policies_xml(
            """
            <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer">
              <decryption-keys><key>c2VjcmV0</key></decryption-keys>
            </validate-jwt></inbound></policies>
            """
        )


def test_validate_jwt_accepts_base64_symmetric_key_and_unsigned_only_when_disabled() -> None:
    """Inline Base64 signing keys validate symmetric tokens, while unsigned tokens require an explicit opt-out.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    secret = b"a sufficiently long local test secret"
    signed = _signed_token(secret, "HS256")
    unsigned = jwt.encode(
        {"iss": "https://issuer.example", "aud": "api", "exp": int(time.time()) + 300},
        None,
        algorithm="none",
    )
    client = _static_client()
    try:
        signed_node = parse_policies_xml(
            """
            <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer">
              <issuer-signing-keys><key>YSBzdWZmaWNpZW50bHkgbG9uZyBsb2NhbCB0ZXN0IHNlY3JldA==</key></issuer-signing-keys>
            </validate-jwt></inbound></policies>
            """
        ).inbound[0]
        unsigned_node = parse_policies_xml(
            """
            <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer" require-signed-tokens="false">
            </validate-jwt></inbound></policies>
            """
        ).inbound[0]
        default_node = parse_policies_xml(
            """
            <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer">
            </validate-jwt></inbound></policies>
            """
        ).inbound[0]
        signed_result = _run(signed_node, _request(signed), _runtime(client))
        unsigned_result = _run(unsigned_node, _request(unsigned), _runtime(client))
        default_unsigned_result = _run(default_node, _request(unsigned), _runtime(client))
    finally:
        asyncio.run(client.aclose())

    assert signed_result is None
    assert unsigned_result is None
    assert default_unsigned_result is not None


def test_validate_jwt_uses_clock_skew_for_expiration_and_not_before() -> None:
    """The documented clock skew is applied as leeway to expiration and not-before validation.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    jwk, private_key = _rsa_material()
    now = int(time.time())
    token = _signed_token(private_key, "RS256", exp=now - 30, nbf=now + 30)
    document = parse_policies_xml(
        f"""
        <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer" clock-skew="60">
          <issuer-signing-keys><key id="rsa-kid" n="{jwk["n"]}" e="{jwk["e"]}" /></issuer-signing-keys>
        </validate-jwt></inbound></policies>
        """
    )

    client = _static_client()
    try:
        result = _run(document.inbound[0], _request(token), _runtime(client))
    finally:
        asyncio.run(client.aclose())

    assert result is None


def test_validate_jwt_supports_rsa_n_e_and_rejects_rs384() -> None:
    """The documented asymmetric algorithm set uses RSA n/e keys and excludes RS384.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    jwk, private_key = _rsa_material()
    token = _signed_token(private_key, "RS256")
    rs384 = _signed_token(private_key, "RS384")
    document = parse_policies_xml(
        f"""
        <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer">
          <issuer-signing-keys><key id="rsa-kid" n="{jwk["n"]}" e="{jwk["e"]}" /></issuer-signing-keys>
        </validate-jwt></inbound></policies>
        """
    )
    client = _static_client()
    try:
        valid = _run(document.inbound[0], _request(token), _runtime(client))
        rejected = _run(document.inbound[0], _request(rs384), _runtime(client))
    finally:
        asyncio.run(client.aclose())

    assert valid is None
    assert rejected is not None
    assert rejected.status_code == 401


def test_validate_jwt_records_documented_failure_reasons() -> None:
    """JWT refusal reasons are the predefined values exposed through context.LastError.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    document = parse_policies_xml(
        """
        <policies><inbound><validate-jwt header-name="Authorization" require-scheme="Bearer">
          <issuer-signing-keys><key>c2VjcmV0</key></issuer-signing-keys>
        </validate-jwt></inbound></policies>
        """
    )
    client = _static_client()
    try:
        request = _request()
        result = _run(document.inbound[0], request, _runtime(client))
    finally:
        asyncio.run(client.aclose())

    assert result is not None
    assert result.body == b"JWT not present."
    assert request.variables["_policy_error_reason"] == "TokenNotPresent"


def test_openid_configuration_cache_is_shared_for_one_hour() -> None:
    """APIM caches OpenID configuration and JWKS for one hour.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    calls: list[str] = []
    cache: dict[str, Any] = {}
    current = [1000.0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("config"):
            return httpx.Response(200, json={"jwks_uri": "https://issuer.example/jwks"})
        return httpx.Response(200, json={"keys": []})

    async def load() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _load_openid_configuration(
                "https://issuer.example/config",
                _runtime(client, openid_cache=cache, clock=lambda: current[0]),
            )
            current[0] += 3599
            await _load_openid_configuration(
                "https://issuer.example/config",
                _runtime(client, openid_cache=cache, clock=lambda: current[0]),
            )
            current[0] += 2
            await _load_openid_configuration(
                "https://issuer.example/config",
                _runtime(client, openid_cache=cache, clock=lambda: current[0]),
            )

    asyncio.run(load())
    assert calls == [
        "https://issuer.example/config",
        "https://issuer.example/jwks",
        "https://issuer.example/config",
        "https://issuer.example/jwks",
    ]


def test_config_oidc_verifier_selects_ec_algorithm_and_rejects_rs384() -> None:
    """OIDC verification supports the documented EC algorithm and rejects RS384.

    https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(ECAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = "ec-kid"
    token = jwt.encode(
        {"iss": "https://issuer.example", "aud": "api", "exp": int(time.time()) + 300},
        private_key,
        algorithm="ES256",
        headers={"kid": "ec-kid"},
    )
    verifier = OIDCVerifier(
        "https://issuer.example",
        "api",
        jwks_uri=None,
        jwks={"keys": [jwk]},
    )

    assert verifier.decode(token)["aud"] == "api"
    rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException):
        verifier.decode(_signed_token(rsa_private_key, "RS384"))
