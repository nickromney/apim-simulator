"""The workload identity issuer publishes keys a backend can verify tokens with."""

from __future__ import annotations

from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from app.policy import issue_local_managed_identity_token
from app.workload_identity import (
    DISCOVERY_PATH,
    JWKS_PATH,
    PRIVATE_KEY_ENV,
    WorkloadIdentityConfig,
    issue_workload_token,
    published_jwks,
)

AUDIENCE = "https://cognitiveservices.azure.com"


def _write_rsa_key(directory: Path, name: str) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = directory / f"{name}-private.pem"
    public = directory / f"{name}-public.pem"
    private.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    public.write_bytes(
        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    return private, public


@pytest.fixture(scope="module")
def keys(tmp_path_factory):
    directory = tmp_path_factory.mktemp("workload-keys")
    current = _write_rsa_key(directory, "current")
    previous = _write_rsa_key(directory, "previous")
    return {
        "private": current[0],
        "public": current[1],
        "previous_private": previous[0],
        "previous_public": previous[1],
    }


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    monkeypatch.delenv(PRIVATE_KEY_ENV, raising=False)


def _settings(keys, **overrides) -> WorkloadIdentityConfig:
    return WorkloadIdentityConfig(
        private_key_file=str(keys["private"]),
        audience_grants={"system-assigned": [AUDIENCE], "team-a": [AUDIENCE]},
        **overrides,
    )


def _verify_with_jwks(token: str, jwks: dict, issuer: str) -> dict:
    key = jwt.PyJWKSet.from_dict(jwks)[jwt.get_unverified_header(token)["kid"]]
    return jwt.decode(token, key.key, algorithms=["RS256"], audience=AUDIENCE, issuer=issuer)


def test_token_kid_matches_published_key_and_carries_entra_shaped_claims(keys):
    settings = _settings(keys)
    token = issue_workload_token(settings, AUDIENCE, "team-a")
    jwks = published_jwks(settings)

    assert [k["kid"] for k in jwks["keys"]] == [jwt.get_unverified_header(token)["kid"]]
    assert {k["alg"] for k in jwks["keys"]} == {"RS256"}
    assert all("d" not in k for k in jwks["keys"]), "private exponent must never be published"
    claims = _verify_with_jwks(token, jwks, settings.issuer)
    assert claims["sub"] == claims["oid"] == claims["appid"] == "team-a"
    assert claims["tid"] == settings.tenant_id


def test_kid_is_the_rfc7638_thumbprint(keys):
    # RFC 7638 section 3.1 example key and its published thumbprint.
    from jwt.algorithms import RSAAlgorithm

    from app.workload_identity import _public_jwk

    rfc_jwk = {
        "kty": "RSA",
        "e": "AQAB",
        "n": (
            "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1L6tSoc_BJECP"
            "ebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY"
            "368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0f"
            "M4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-kEgU8awapJzKnqDKgw"
        ),
    }
    assert _public_jwk(RSAAlgorithm.from_jwk(rfc_jwk))["kid"] == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


def test_previous_public_key_is_published_alongside_for_rotation(keys):
    old = WorkloadIdentityConfig(
        private_key_file=str(keys["previous_private"]), audience_grants={"system-assigned": [AUDIENCE]}
    )
    in_flight = issue_workload_token(old, AUDIENCE)
    rotated = _settings(keys, public_key_file=str(keys["previous_public"]))

    jwks = published_jwks(rotated)

    assert len(jwks["keys"]) == 2
    assert _verify_with_jwks(in_flight, jwks, old.issuer)["sub"] == "system-assigned"
    assert _verify_with_jwks(issue_workload_token(rotated, AUDIENCE), jwks, rotated.issuer)


def test_public_key_matching_the_signing_key_is_not_duplicated(keys):
    assert len(published_jwks(_settings(keys, public_key_file=str(keys["public"])))["keys"]) == 1


def test_nothing_is_published_in_demo_mode_or_without_a_signing_key(keys):
    assert published_jwks(_settings(keys, mode="demo")) is None
    assert published_jwks(WorkloadIdentityConfig()) is None


def test_non_rsa_signing_key_is_rejected(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "ec.pem"
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    settings = WorkloadIdentityConfig(private_key_file=str(path), audience_grants={"system-assigned": [AUDIENCE]})
    with pytest.raises(ValueError):
        issue_workload_token(settings, AUDIENCE)
    with pytest.raises(ValueError):
        published_jwks(settings)


def test_endpoints_publish_discovery_and_keys_that_verify_policy_tokens(keys):
    cfg = GatewayConfig(allow_anonymous=True, workload_identity=_settings(keys).model_dump())
    with TestClient(create_app(config=cfg), base_url="http://apim-simulator:8000") as client:
        discovery = client.get(DISCOVERY_PATH)
        jwks = client.get(JWKS_PATH)

    assert discovery.status_code == 200
    assert discovery.json()["issuer"] == cfg.workload_identity.issuer
    assert discovery.json()["jwks_uri"] == "http://apim-simulator:8000" + JWKS_PATH
    assert jwks.status_code == 200
    # The same token the authentication-managed-identity policy sends.
    token = issue_local_managed_identity_token(AUDIENCE, config=cfg)
    assert _verify_with_jwks(token, jwks.json(), cfg.workload_identity.issuer)["oid"] == "system-assigned"


@pytest.mark.parametrize(
    "workload_identity",
    [{"mode": "demo"}, {"mode": "signed"}],
    ids=["demo-mode", "signed-without-key"],
)
def test_endpoints_return_404_when_there_is_nothing_to_publish(workload_identity):
    cfg = GatewayConfig(allow_anonymous=True, workload_identity=workload_identity)
    with TestClient(create_app(config=cfg)) as client:
        assert client.get(DISCOVERY_PATH).status_code == 404
        assert client.get(JWKS_PATH).status_code == 404


def test_signing_key_from_environment_is_published(keys, monkeypatch):
    monkeypatch.setenv(PRIVATE_KEY_ENV, str(keys["private"]))
    cfg = GatewayConfig(allow_anonymous=True)
    with TestClient(create_app(config=cfg)) as client:
        assert len(client.get(JWKS_PATH).json()["keys"]) == 1


def test_unreadable_signing_key_is_a_server_error_not_a_silent_404(tmp_path):
    cfg = GatewayConfig(allow_anonymous=True, workload_identity={"private_key_file": str(tmp_path / "missing.pem")})
    with TestClient(create_app(config=cfg), raise_server_exceptions=False) as client:
        response = client.get(JWKS_PATH)
    assert response.status_code == 500
    assert "missing.pem" not in response.text
