from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import GatewayConfig, TenantAccessConfig
from app.main import create_app

TENANT = {"X-Apim-Tenant-Key": "tenant"}


def test_api_center_continuously_syncs_definitions_and_unlink_removes_inventory() -> None:
    cfg = GatewayConfig(tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"))
    with TestClient(create_app(config=cfg)) as client:
        center = "/apim/management/api-centers/local"
        assert client.put(center, json={"name": "Local catalog"}).status_code == 403
        assert client.put(center, headers=TENANT, json={"name": "Local catalog"}).status_code == 200
        link = "/apim/management/api-center/link"
        assert client.put(link, headers=TENANT, json={"center_id": "missing"}).status_code == 404
        assert (
            client.put(
                link, headers=TENANT, json={"center_id": "local", "include_definitions": True, "lifecycle": "testing"}
            ).json()["state"]
            == "Linked and syncing"
        )
        api = "/apim/management/apis/pets"
        assert (
            client.put(
                api, headers=TENANT, json={"name": "Pets", "path": "pets", "upstream_base_url": "http://backend"}
            ).status_code
            == 200
        )
        assert (
            client.put(
                api + "/operations/list", headers=TENANT, json={"method": "GET", "url_template": "/list"}
            ).status_code
            == 200
        )
        inventory = client.get(center + "/apis", headers=TENANT).json()["items"]
        assert inventory[0]["definition"]["openapi"] == "3.0.3"
        assert inventory[0]["definition"]["paths"]["/list"]["get"]["operationId"] == "list"
        assert inventory[0]["lifecycle"] == "testing"
        assert (
            client.put(
                api,
                headers=TENANT,
                json={"name": "Updated pets", "path": "pets", "upstream_base_url": "http://backend"},
            ).status_code
            == 200
        )
        assert client.get(center + "/apis", headers=TENANT).json()["items"][0]["title"] == "Updated pets"
        assert client.delete(api, headers=TENANT).status_code == 200
        assert client.get(center + "/apis", headers=TENANT).json()["items"] == []
        assert (
            client.put(
                api, headers=TENANT, json={"name": "Pets", "path": "pets", "upstream_base_url": "http://backend"}
            ).status_code
            == 200
        )
        assert client.delete(link, headers=TENANT).status_code == 200
        assert client.get(center + "/apis", headers=TENANT).json()["items"] == []
        assert client.get(link, headers=TENANT).json()["state"] == "Not linked"
        assert (
            client.put(
                api + "/operations/new", headers=TENANT, json={"method": "GET", "url_template": "/new"}
            ).status_code
            == 200
        )
        assert client.get(center + "/apis", headers=TENANT).json()["items"] == []


def test_api_center_single_link_and_optional_definitions() -> None:
    cfg = GatewayConfig(tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"))
    with TestClient(create_app(config=cfg)) as client:
        for key in ("first", "second"):
            client.put(f"/apim/management/api-centers/{key}", headers=TENANT, json={"name": key})
        link = "/apim/management/api-center/link"
        client.put(link, headers=TENANT, json={"center_id": "first"})
        assert client.put(link, headers=TENANT, json={"center_id": "second"}).status_code == 409
        client.put(
            "/apim/management/apis/pets",
            headers=TENANT,
            json={"name": "Pets", "path": "pets", "upstream_base_url": "http://backend"},
        )
        asset = client.get("/apim/management/api-centers/first/apis", headers=TENANT).json()["items"][0]
        assert "definition" not in asset
