from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig, KeyVaultNamedValueConfig, NamedValueConfig, RouteConfig, TenantAccessConfig
from app.main import create_app
from app.named_values import resolve_named_value


def test_display_name_rename_updates_policies_and_live_resolution():
    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="operator"),
        named_values={"identifier": NamedValueConfig(display_name="Before", value="one")},
        routes=[
            RouteConfig(
                name="named",
                path_prefix="/named",
                upstream_base_url="http://backend",
                policies_xml='<policies><inbound><set-header name="Named"><value>{{ Before }}</value></set-header></inbound><backend><forward-request /></backend></policies>',
            )
        ],
    )
    calls = []

    def backend(request):
        calls.append(request)
        return httpx.Response(200, text="ok")

    with TestClient(
        create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    ) as client:
        assert client.get("/named").status_code == 200
        response = client.put(
            "/apim/management/named-values/identifier",
            headers={"X-Apim-Tenant-Key": "operator"},
            json={"display_name": "After", "value": "two"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["display_name"] == "After"
        assert "{{After}}" in client.app.state.gateway_config.routes[0].policies_xml
        assert client.get("/named").status_code == 200
        assert (
            client.delete(
                "/apim/management/named-values/identifier", headers={"X-Apim-Tenant-Key": "operator"}
            ).status_code
            == 400
        )
    assert [request.headers["Named"] for request in calls] == ["one", "two"]


def test_vault_mapping_rotation_secret_masking_and_env_override(monkeypatch):
    value = {"current": "first"}
    seen = []
    original_client = httpx.Client

    def vault(request):
        seen.append(request)
        assert request.url == "http://local-vault/secrets/demo"
        assert request.headers["Authorization"] == "Bearer read-key"
        return httpx.Response(200, json={"value": value["current"]})

    monkeypatch.setenv("APIM_LOCAL_VAULT_BASE_URL", "http://local-vault")
    monkeypatch.setenv("APIM_LOCAL_VAULT_KEY", "read-key")
    monkeypatch.setattr(
        "app.named_values.httpx.Client",
        lambda **kwargs: original_client(**{**kwargs, "transport": httpx.MockTransport(vault)}),
    )
    cfg = GatewayConfig(
        workload_identity={"mode": "demo"},
        named_values={
            "Demo": NamedValueConfig(
                display_name="Display",
                value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://vault.example.test/secrets/demo"),
            )
        },
    )
    assert resolve_named_value(cfg, "Display").value == "first"
    value["current"] = "rotated"
    assert resolve_named_value(cfg, "Display").value == "rotated"
    from app.named_values import mask_secret_data

    assert mask_secret_data({"secret": "rotated"}, cfg) == {"secret": "***"}
    monkeypatch.setenv("APIM_NAMED_VALUE_DEMO", "environment")
    assert resolve_named_value(cfg, "Display").value == "environment"
    assert len(seen) == 3


@pytest.mark.parametrize("payload", [{"value": ""}, {"value": "x" * 4097}, {"value": 5}, {"value": "x" * 33000}, []])
def test_local_vault_refuses_invalid_or_oversized_secret(monkeypatch, payload):
    original_client = httpx.Client
    monkeypatch.setenv("APIM_LOCAL_VAULT_BASE_URL", "http://local-vault")
    monkeypatch.setattr(
        "app.named_values.httpx.Client",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, content=json.dumps(payload).encode())),
            **kwargs,
        ),
    )
    cfg = GatewayConfig(
        workload_identity={"mode": "demo"},
        named_values={
            "Demo": NamedValueConfig(
                value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://vault.example.test/secrets/demo")
            )
        },
    )
    with pytest.raises(ValueError):
        resolve_named_value(cfg, "Demo")


def test_keyvault_reference_never_fetches_external_identifier_without_local_mapping(monkeypatch):
    monkeypatch.delenv("APIM_LOCAL_VAULT_BASE_URL", raising=False)
    cfg = GatewayConfig(
        workload_identity={"mode": "demo"},
        named_values={
            "Demo": NamedValueConfig(
                value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://vault.example.test/secrets/demo")
            )
        },
    )
    assert resolve_named_value(cfg, "Demo").value is None


def test_vault_rotation_reaches_next_gateway_request_without_management_reload(monkeypatch):
    current = {"value": "before"}
    real_client = httpx.Client
    monkeypatch.setenv("APIM_LOCAL_VAULT_BASE_URL", "http://local-vault")
    monkeypatch.setattr(
        "app.named_values.httpx.Client",
        lambda **kwargs: real_client(
            **{**kwargs, "transport": httpx.MockTransport(lambda req: httpx.Response(200, json=current))}
        ),
    )
    cfg = GatewayConfig(
        workload_identity={"mode": "demo"},
        allow_anonymous=True,
        named_values={
            "Vault": NamedValueConfig(
                value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://vault.example.test/secrets/demo")
            )
        },
        routes=[
            RouteConfig(
                name="vault",
                path_prefix="/vault",
                upstream_base_url="http://backend",
                policies_xml='<policies><inbound><set-header name="Vault"><value>{{Vault}}</value></set-header></inbound><backend><forward-request /></backend></policies>',
            )
        ],
    )
    seen = []

    def backend(req):
        seen.append(req.headers["Vault"])
        return httpx.Response(200, text="ok")

    with TestClient(
        create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    ) as client:
        assert client.get("/vault").status_code == 200
        current["value"] = "after"
        assert client.get("/vault").status_code == 200
    assert seen == ["before", "after"]


def test_request_snapshot_redacts_value_used_before_external_rotation(monkeypatch):
    from app.named_values import mask_secret_data, named_value_resolution_scope

    current = {"value": "before"}
    seen = []
    real_client = httpx.Client

    def vault(req):
        seen.append(req)
        return httpx.Response(200, json=current)

    monkeypatch.setenv("APIM_LOCAL_VAULT_BASE_URL", "http://local-vault")
    monkeypatch.setattr(
        "app.named_values.httpx.Client",
        lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(vault)}),
    )
    cfg = GatewayConfig(
        workload_identity={"mode": "demo"},
        named_values={
            "Vault": NamedValueConfig(
                value_from_key_vault=KeyVaultNamedValueConfig(secret_id="https://vault.example.test/secrets/demo")
            )
        },
    )
    with named_value_resolution_scope():
        assert resolve_named_value(cfg, "Vault").value == "before"
        current["value"] = "after"
        assert mask_secret_data({"old": "before", "nested": ["before", "more before"]}, cfg) == {
            "old": "***",
            "nested": ["***", "more ***"],
        }
        assert len(seen) == 1
    assert resolve_named_value(cfg, "Vault").value == "after"
