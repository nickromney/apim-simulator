from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig, ProductConfig, RouteConfig, TenantAccessConfig
from app.main import create_app


def _saved_gateway_config() -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant-test"),
        products={"starter": ProductConfig(name="Starter", description="Before save")},
        routes=[
            RouteConfig(
                name="stable",
                path_prefix="/stable",
                upstream_base_url="http://backend",
            )
        ],
    )


def _client(config: GatewayConfig) -> TestClient:
    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"gateway-stable"))
    )
    return TestClient(create_app(config=config, http_client=async_client))


def _write_source_config(path: Path, config: GatewayConfig) -> bytes:
    payload = json.dumps(config.model_dump(mode="json"), indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")
    return payload.encode("utf-8")


def test_failed_management_save_keeps_disk_and_gateway_config(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "apim.json"
    config = _saved_gateway_config()
    original_payload = _write_source_config(config_path, config)
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))

    with _client(config) as client:
        app = client.app
        original_runtime_config = app.state.gateway_config
        before = client.get("/stable/item")

        def fail_replace(_source: str, _target: Path) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr("app.management_service.os.replace", fail_replace)
        response = client.put(
            "/apim/management/products/starter",
            headers={"X-Apim-Tenant-Key": "tenant-test"},
            json={"name": "Starter", "description": "Should not publish"},
        )
        after = client.get("/stable/item")

        assert response.status_code == 500
        assert config_path.read_bytes() == original_payload
        assert app.state.gateway_config is original_runtime_config
        assert app.state.gateway_config.products["starter"].description == "Before save"
        assert before.content == after.content == b"gateway-stable"


def test_successful_management_save_is_atomic_published_and_watcher_acknowledged(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "apim.json"
    config = _saved_gateway_config()
    _write_source_config(config_path, config)
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("APIM_CONFIG_WATCH", "true")
    monkeypatch.setenv("APIM_CONFIG_WATCH_INTERVAL", "0.01")

    with _client(config) as client:
        app = client.app
        app.state.policy_response_cache["retained"] = "response"
        app.state.policy_value_cache["retained"] = "value"
        response = client.put(
            "/apim/management/products/starter",
            headers={"X-Apim-Tenant-Key": "tenant-test"},
            json={"name": "Starter", "description": "Saved"},
        )
        time.sleep(0.08)

        disk_config = json.loads(config_path.read_text(encoding="utf-8"))
        assert response.status_code == 200
        assert disk_config["products"]["starter"]["description"] == "Saved"
        assert app.state.gateway_config.products["starter"].description == "Saved"
        assert app.state.policy_response_cache == {"retained": "response"}
        assert app.state.policy_value_cache == {"retained": "value"}

        reloaded = client.post("/apim/reload", headers={"X-Apim-Tenant-Key": "tenant-test"})
        assert reloaded.status_code == 200
        assert app.state.gateway_config.products["starter"].description == "Saved"
        assert app.state.policy_response_cache == {}
        assert app.state.policy_value_cache == {}
