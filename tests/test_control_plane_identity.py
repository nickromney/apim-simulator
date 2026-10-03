from __future__ import annotations

import time
from types import SimpleNamespace

import httpx
import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import GatewayConfig, PortalConfig, Subscription, SubscriptionConfig, UserConfig
from app.control_plane import ControlPlaneConfig, PortalIdentityConfig
from app.main import create_app
from app.security import require_tenant_access

KEY = "synthetic-local-issuer-signing-key-32-bytes"


@pytest.fixture(autouse=True)
def signing_keys(monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    monkeypatch.setenv("APIM_PORTAL_SIGNING_KEY", KEY)


def token(*, audience="apim-management", subject="operator-1", **claims):
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "iss": "https://local-issuer.example.test",
            "aud": audience,
            "iat": now,
            "exp": now + 300,
            **claims,
        },
        KEY,
        algorithm="HS256",
    )


def config(**claims):
    return GatewayConfig(control_plane=ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False, **claims))


def authorize(cfg, path, method="GET", bearer=None, **headers):
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    if bearer:
        raw_headers.append((b"authorization", ("Bearer " + bearer).encode()))
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": raw_headers,
            "app": SimpleNamespace(state=SimpleNamespace(gateway_config=cfg)),
            "query_string": b"",
        }
    )
    require_tenant_access(request)
    return request.state.management_actor


def denied(cfg, path, method="GET", expected=403, bearer=None, **headers):
    with pytest.raises(HTTPException) as error:
        authorize(cfg, path, method, bearer, **headers)
    assert error.value.status_code == expected


def test_reader_metadata_without_mutation_debug_or_subscription_keys():
    cfg = config()
    bearer = token(roles=["reader"])
    actor = authorize(cfg, "/apim/management/apis", bearer=bearer)
    assert actor == {"subject": "operator-1", "roles": ["reader"], "authentication": "signed-jwt"}
    denied(cfg, "/apim/management/apis/a", "PUT", bearer=bearer)
    denied(cfg, "/apim/management/subscriptions", bearer=bearer)
    denied(cfg, "/apim/management/gateways/managed/listDebugCredentials", "POST", bearer=bearer)


def test_operator_can_operate_and_debug_without_editing_catalog():
    cfg = config()
    bearer = token(roles=["operator"])
    authorize(cfg, "/apim/reload", "POST", bearer)
    authorize(cfg, "/apim/management/traces", bearer=bearer)
    denied(cfg, "/apim/management/products/p", "PUT", bearer=bearer)


def test_content_editor_only_edits_portal():
    cfg = config()
    bearer = token(roles=["content-editor"])
    authorize(cfg, "/apim/portal/editor/draft", "PUT", bearer)
    authorize(cfg, "/apim/portal/editor/preview", bearer=bearer)
    denied(cfg, "/apim/management/apis", bearer=bearer)
    denied(cfg, "/apim/reload", "POST", bearer=bearer)


def test_workspace_and_api_grants_intersect_and_do_not_grant_global_inventory():
    cfg = config(workspace_apis={"team-a": ["orders", "inventory"], "team-b": ["payroll"]})
    bearer = token(apim_grants=[{"role": "contributor", "workspace_id": "team-a", "api_ids": ["orders", "payroll"]}])
    authorize(cfg, "/apim/management/apis/orders/operations/get", "PUT", bearer)
    authorize(cfg, "/apim/management/policies/api/orders", "PUT", bearer)
    denied(cfg, "/apim/management/apis/payroll", "PUT", bearer=bearer)
    denied(cfg, "/apim/management/apis/inventory", bearer=bearer)
    denied(cfg, "/apim/management/apis", bearer=bearer)
    denied(cfg, "/apim/management/policies/global/service", "PUT", bearer=bearer)


def test_api_scoped_reader_and_unknown_workspace():
    cfg = config()
    bearer = token(apim_grants=[{"role": "reader", "api_ids": ["orders"]}])
    authorize(cfg, "/apim/management/apis/orders", bearer=bearer)
    denied(cfg, "/apim/management/apis/orders", "DELETE", bearer=bearer)
    unknown = token(apim_grants=[{"role": "contributor", "workspace_id": "missing"}])
    denied(cfg, "/apim/management/apis/orders", bearer=unknown)


@pytest.mark.parametrize(
    "claims",
    [
        {"roles": ["contributor"]},
        {"roles": ["contributor"], "amr": ["mfa"]},
        {"roles": ["contributor"], "device_compliant": True},
        {"apim_grants": [{"role": "contributor", "api_ids": ["a"]}]},
    ],
)
def test_administrator_mfa_and_device_claims_required(claims):
    denied(
        config(require_mfa=True, require_compliant_device=True),
        "/apim/management/apis/a",
        "PUT",
        bearer=token(**claims),
    )


def test_administrator_claims_allow_authorized_write():
    cfg = config(require_mfa=True, require_compliant_device=True)
    authorize(cfg, "/apim/management/apis/a", "PUT", token(roles=["contributor"], amr=["mfa"], device_compliant=True))
    authorize(cfg, "/apim/management/apis", bearer=token(roles=["reader"]))


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"exp": 1},
        {"roles": "contributor"},
        {"apim_grants": [{"role": "contributor"}]},
    ],
)
def test_invalid_token_or_permissions_rejected(claims):
    expected = 401 if any(k in claims for k in ("aud", "iss", "exp")) else 403
    denied(config(), "/apim/management/apis", expected=expected, bearer=token(**claims))


def test_secure_mode_disables_shared_keys_and_invalid_bearer_does_not_fallback():
    cfg = config()
    cfg.tenant_access.enabled = True
    cfg.tenant_access.primary_key = "synthetic-tenant-key"
    cfg.admin_token = "synthetic-admin-key"
    denied(cfg, "/apim/management/apis", expected=401, **{"x-apim-tenant-key": cfg.tenant_access.primary_key})
    cfg.control_plane.allow_legacy_tenant_keys = True
    actor = authorize(cfg, "/apim/management/apis", **{"x-apim-tenant-key": cfg.tenant_access.primary_key})
    assert actor["authentication"] == "legacy-shared-key"
    denied(cfg, "/apim/management/apis", expected=401, bearer="invalid", **{"x-apim-admin-token": cfg.admin_token})


def test_missing_signing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("APIM_CONTROL_PLANE_SIGNING_KEY")
    denied(config(), "/apim/management/apis", expected=503, bearer=token(roles=["contributor"]))


def portal_client(**identity_overrides):
    cfg = GatewayConfig(
        allow_anonymous=True,
        portal=PortalConfig(enabled=True, identity=PortalIdentityConfig(enabled=True, **identity_overrides)),
        users={"alice": UserConfig(id="alice"), "bob": UserConfig(id="bob")},
        subscription=SubscriptionConfig(
            required=False,
            subscriptions={
                "alice-sub": Subscription(
                    id="alice-sub",
                    name="Alice subscription",
                    created_by="portal:alice",
                    keys={"primary": "alice-primary", "secondary": "alice-secondary"},
                ),
                "bob-sub": Subscription(
                    id="bob-sub",
                    name="Bob subscription",
                    created_by="portal:bob",
                    keys={"primary": "bob-primary", "secondary": "bob-secondary"},
                ),
            },
        ),
    )
    return TestClient(
        create_app(
            config=cfg,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
            ),
        )
    ), cfg


def test_signed_portal_subject_binds_keys_and_user_inventory():
    client, _ = portal_client()
    with client:
        assert client.get("/apim/portal/users").status_code == 401
        assert client.get("/apim/portal/subscriptions", headers={"X-Apim-Portal-User": "bob"}).status_code == 401
        headers = {
            "Authorization": "Bearer " + token(audience="apim-portal", subject="alice"),
            "X-Apim-Portal-User": "bob",
        }
        response = client.get("/apim/portal/subscriptions", headers=headers)
        assert response.status_code == 200
        assert response.json()["user"] == "alice"
        assert response.headers["cache-control"] == "no-store"
        assert "alice-primary" in response.text and "bob-primary" not in response.text
        assert client.get("/apim/portal/users", headers=headers).json()["users"] == [{"id": "alice", "name": "alice"}]
        page = client.get("/apim/portal").text
        assert 'id="portal-token"' in page and "localStorage" not in page


def test_portal_rejects_management_audience_unknown_and_inactive_subjects():
    client, cfg = portal_client()
    cfg.users["bob"].state = "blocked"
    with client:
        for bearer, status in [
            (token(subject="alice"), 401),
            (token(audience="apim-portal", subject="missing"), 401),
            (token(audience="apim-portal", subject="bob"), 403),
        ]:
            assert (
                client.get("/apim/portal/catalog", headers={"Authorization": "Bearer " + bearer}).status_code == status
            )


def test_portal_legacy_header_requires_explicit_signed_mode_compatibility():
    client, _ = portal_client(allow_legacy_user_header=True)
    with client:
        assert client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "alice"}).status_code == 200
        assert (
            client.get(
                "/apim/portal/catalog", headers={"X-Apim-Portal-User": "alice", "Authorization": "Bearer invalid"}
            ).status_code
            == 401
        )


def test_signed_roles_are_enforced_on_real_management_and_editor_routes():
    client, cfg = portal_client()
    cfg.control_plane = ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False)
    with client:
        reader = {"Authorization": "Bearer " + token(roles=["reader"])}
        assert client.get("/apim/management/apis", headers=reader).status_code == 200
        assert (
            client.put(
                "/apim/management/apis/a",
                headers=reader,
                json={"name": "A", "path": "a", "upstream_base_url": "https://backend.example.test"},
            ).status_code
            == 403
        )
        editor = {"Authorization": "Bearer " + token(roles=["content-editor"])}
        draft = client.get("/apim/portal/editor/draft", headers=editor).json()["site"]
        draft["site_title"] = "Signed operator draft"
        assert client.put("/apim/portal/editor/draft", headers=editor, json=draft).status_code == 200
        assert client.post("/apim/portal/editor/publish", headers=editor).status_code == 200
        assert "Signed operator draft" in client.get("/apim/portal").text
        assert client.get("/apim/management/users", headers=editor).status_code == 403


def test_reader_backend_metadata_redacts_inline_credentials_without_mutating_config():
    from app.config import BackendConfig

    client, cfg = portal_client()
    cfg.control_plane = ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False)
    cfg.backends["private"] = BackendConfig(
        url="https://user:url-password@backend.example.test/api?sig=url-token",
        auth_type="basic",
        basic_username="backend-user",
        basic_password="basic-secret",
        authorization_scheme="Bearer",
        authorization_parameter="bearer-secret",
        header_credentials={"x-api-key": "header-secret"},
        query_credentials={"code": "query-secret"},
        client_certificate_key_file="/tmp/local-client.key",
    )
    with client:
        headers = {"Authorization": "Bearer " + token(roles=["reader"])}
        single = client.get("/apim/management/backends/private", headers=headers)
        collection = client.get("/apim/management/backends", headers=headers)
        assert single.status_code == collection.status_code == 200
        for secret in ("url-password", "url-token", "basic-secret", "bearer-secret", "header-secret", "query-secret"):
            assert secret not in single.text and secret not in collection.text
        assert single.json()["auth_type"] == "basic"
        assert single.json()["basic_username"] == "backend-user"
        assert single.json()["header_credentials"] == {"x-api-key": "[redacted]"}
        assert single.json()["basic_password"] == "[redacted]"
        assert cfg.backends["private"].basic_password == "basic-secret"


@pytest.mark.parametrize("mode", ["signature", "algorithm", "missing-expiry", "future-not-before", "empty-subject"])
def test_identity_verification_requires_trusted_signature_algorithm_and_lifetime(mode):
    now = int(time.time())
    claims = {
        "sub": "operator-1",
        "iss": "https://local-issuer.example.test",
        "aud": "apim-management",
        "iat": now,
        "exp": now + 300,
        "roles": ["contributor"],
    }
    key, algorithm = KEY, "HS256"
    if mode == "signature":
        key = "different-synthetic-signing-key-32-bytes"
    elif mode == "algorithm":
        algorithm = "HS512"
        key = KEY.ljust(64, "x")
    elif mode == "missing-expiry":
        claims.pop("exp")
    elif mode == "future-not-before":
        claims["nbf"] = now + 300
    else:
        claims["sub"] = ""
    bearer = jwt.encode(claims, key, algorithm=algorithm)
    denied(config(), "/apim/management/apis", expected=401, bearer=bearer)


def test_resource_names_do_not_change_operator_permissions_and_summary_keys_are_restricted():
    cfg = config()
    operator = token(roles=["operator"])
    reader = token(roles=["reader"])
    for identifier in ("replay", "traces", "listDebugCredentials", "subscriptions"):
        denied(cfg, "/apim/management/apis/" + identifier, "PUT", bearer=operator)
        authorize(cfg, "/apim/management/apis/" + identifier, bearer=reader)
    denied(cfg, "/apim/management/summary", bearer=reader)
    denied(cfg, "/apim/management/summary", bearer=operator)


def test_signed_portal_uses_verified_subject_when_user_metadata_id_differs():
    client, cfg = portal_client()
    cfg.users["alice"].id = "bob"
    with client:
        response = client.get(
            "/apim/portal/subscriptions",
            headers={
                "Authorization": "Bearer " + token(audience="apim-portal", subject="alice"),
            },
        )
        assert response.status_code == 200 and response.json()["user"] == "alice"
        assert "alice-primary" in response.text and "bob-primary" not in response.text
