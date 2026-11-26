"""validate-azure-ad-token: Entra token validation for one tenant.

https://learn.microsoft.com/en-us/azure/api-management/validate-azure-ad-token-policy
Keys are discovered from {entra_authority}/{tenant}/v2.0, so local runs can
point the authority at a local OIDC provider instead of Microsoft Entra.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.algorithms import RSAAlgorithm

from app.config import GatewayConfig
from app.policy import PolicyRequest, PolicyRuntime, ValidateAzureAdToken, parse_policies_xml

TENANT = "11111111-1111-1111-1111-111111111111"
V2_ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
V1_ISSUER = f"https://sts.windows.net/{TENANT}/"
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = {**json.loads(RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key())), "kid": "k1"}


def _token(**claims: Any) -> str:
    payload = {"iss": V2_ISSUER, "aud": "https://cognitiveservices.azure.com", "exp": int(time.time()) + 300}
    payload.update(claims)
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256", headers={"kid": "k1"})


def _policy(inner: str = "", attrs: str = "") -> ValidateAzureAdToken:
    xml = (
        f'<policies><inbound><validate-azure-ad-token tenant-id="{TENANT}" {attrs}>'
        f"{inner}</validate-azure-ad-token></inbound></policies>"
    )
    node = parse_policies_xml(xml).inbound[0]
    assert isinstance(node, ValidateAzureAdToken)
    return node


def _run(
    node: ValidateAzureAdToken, token: str | None, *, authority: str | None = None, scheme: str = "Bearer"
) -> tuple[Any, PolicyRequest, list[str]]:
    requested: list[str] = []
    configured = authority or "https://login.microsoftonline.com"
    metadata_issuer = V2_ISSUER if authority is None else f"{authority}/{TENANT}"

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url).endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": metadata_issuer, "jwks_uri": "https://keys.example/jwks"})
        return httpx.Response(200, json={"keys": [JWK]})

    req = PolicyRequest(
        method="POST",
        path="/chat/completions",
        query={},
        headers={"authorization": f"{scheme} {token}"} if token else {},
        variables={},
    )

    async def run() -> Any:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = PolicyRuntime(http_client=client, gateway_config=GatewayConfig(entra_authority=configured))
            return await node.apply_async(req, runtime)

    return asyncio.run(run()), req, requested


def test_discovers_keys_from_the_tenant_and_accepts_its_v2_issuer() -> None:
    result, req, requested = _run(_policy(attrs='output-token-variable-name="jwt"'), _token(oid="o1"))
    assert result is None
    assert requested[0] == f"https://login.microsoftonline.com/{TENANT}/v2.0/.well-known/openid-configuration"
    assert req.variables["jwt"].Claims.GetValueOrDefault("oid") == "o1"


def test_accepts_the_tenants_v1_issuer() -> None:
    result, _, _ = _run(_policy(), _token(iss=V1_ISSUER))
    assert result is None


def test_rejects_another_tenants_issuer_with_the_configured_failure() -> None:
    node = _policy(attrs='failed-validation-httpcode="403" failed-validation-error-message="Entra token required"')
    result, _, _ = _run(node, _token(iss="https://login.microsoftonline.com/other/v2.0"))
    assert result.status_code == 403
    assert result.body == b"Entra token required"


def test_requires_a_bearer_token_in_authorization_by_default() -> None:
    result, req, _ = _run(_policy(), _token(), scheme="Basic")
    assert result.status_code == 401
    assert req.variables["_policy_error_reason"] == "TokenNotPresent"


def test_audiences_and_required_claims_are_enforced() -> None:
    inner = (
        "<audiences><audience>https://cognitiveservices.azure.com</audience></audiences>"
        '<required-claims><claim name="xms_mirid" match="any"><value>/subscriptions/s/project</value></claim>'
        "</required-claims>"
    )
    assert _run(_policy(inner), _token(xms_mirid="/subscriptions/s/project"))[0] is None
    assert _run(_policy(inner), _token(xms_mirid="/subscriptions/s/other"))[0].status_code == 401
    assert _run(_policy(inner), _token(aud="api://else", xms_mirid="/subscriptions/s/project"))[0].status_code == 401


def test_backend_application_ids_are_accepted_as_audiences() -> None:
    inner = "<backend-application-ids><application-id>app-123</application-id></backend-application-ids>"
    assert _run(_policy(inner), _token(aud="api://app-123"))[0] is None
    assert _run(_policy(inner), _token(aud="app-123"))[0] is None
    assert _run(_policy(inner), _token(aud="api://app-999"))[0].status_code == 401


def test_client_application_ids_match_azp_or_v1_appid() -> None:
    inner = "<client-application-ids><application-id>client-1</application-id></client-application-ids>"
    assert _run(_policy(inner), _token(azp="client-1"))[0] is None
    assert _run(_policy(inner), _token(iss=V1_ISSUER, appid="client-1"))[0] is None
    result, req, _ = _run(_policy(inner), _token(azp="client-2"))
    assert result.status_code == 401
    assert req.variables["_policy_error_reason"] == "TokenClaimValueNotAllowed"


def test_local_authority_accepts_only_the_discovered_issuer() -> None:
    authority = "http://idp.local/realms"
    result, _, requested = _run(_policy(), _token(iss=f"{authority}/{TENANT}"), authority=authority)
    assert result is None
    assert requested[0] == f"{authority}/{TENANT}/v2.0/.well-known/openid-configuration"
    assert _run(_policy(), _token(iss=V2_ISSUER), authority=authority)[0].status_code == 401


def test_parse_errors_name_the_policy() -> None:
    with pytest.raises(HTTPException, match="requires tenant-id"):
        parse_policies_xml("<policies><inbound><validate-azure-ad-token /></inbound></policies>")
    with pytest.raises(HTTPException, match="only one token source"):
        _policy(attrs='header-name="a" query-parameter-name="b"')
    with pytest.raises(HTTPException, match="not allowed in outbound"):
        parse_policies_xml(
            f'<policies><outbound><validate-azure-ad-token tenant-id="{TENANT}" /></outbound></policies>'
        )


def test_exported_foundry_gateway_policy_parses() -> None:
    xml = f"""<policies><inbound>
      <choose><when condition="@(context.Subscription == null)">
        <validate-azure-ad-token tenant-id="{TENANT}" header-name="Authorization" failed-validation-httpcode="401"
            failed-validation-error-message="Unauthorized. Valid Entra token is required." output-token-variable-name="jwt">
          <audiences><audience>https://cognitiveservices.azure.com</audience></audiences>
          <required-claims><claim name="xms_mirid" match="any"><value>/subscriptions/s/project</value></claim></required-claims>
        </validate-azure-ad-token>
      </when></choose>
      <base />
    </inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>"""
    document = parse_policies_xml(xml)
    assert document.inbound
