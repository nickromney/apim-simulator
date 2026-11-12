import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import ApiConfig, BackendConfig, GatewayConfig, NamedValueConfig, OperationConfig, ProductConfig
from app.control_plane import ControlPlaneConfig
from app.main import create_app

pytestmark = pytest.mark.contract("MGMT-PRODUCT-ARTIFACT-PROMOTION")

PATH = "/apim/management/products/shop/artifacts"
HEADERS = {"X-Apim-Tenant-Key": "test-tenant"}
PRODUCT_POLICY = (
    "<policies><outbound><base /><set-header name='x-shop' exists-action='override'>"
    "<value>{{ShopLabel}}</value></set-header></outbound></policies>"
)
API_POLICY = (
    "<policies><outbound><base /><set-header name='x-api' exists-action='override'>"
    "<value>api</value></set-header></outbound></policies>"
)
OP_POLICY = API_POLICY.replace("x-api", "x-operation")
OVERRIDES = {
    "api_upstreams": {"billing": "http://target-direct/api"},
    "backends": {"catalog": "http://target-catalog/api"},
    "named_values": {"shop-label": "target-private-value"},
}


def gateway(**settings):
    return GatewayConfig.model_validate({"tenant_access": {"enabled": True, "primary_key": "test-tenant"}, **settings})


def api(name, **settings):
    return ApiConfig(
        name=name,
        path=name,
        upstream_base_url=f"http://source-{name}/api",
        products=["shop"],
        policies_xml=API_POLICY,
        operations={"ReadItem": OperationConfig(name="Read item", url_template="/item", policies_xml=OP_POLICY)},
        **settings,
    )


@pytest.fixture
def source():
    return gateway(
        products={"shop": ProductConfig(name="Shop", policies_xml=PRODUCT_POLICY)},
        apis={"catalog": api("catalog", backend="catalog"), "billing": api("billing")},
        backends={"catalog": BackendConfig(url="http://source-catalog/api")},
        named_values={
            "shop-label": NamedValueConfig(display_name="ShopLabel", value="source-private-value", secret=True)
        },
    )


@pytest.fixture
def artifact(source):
    with TestClient(create_app(config=source)) as client:
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200, response.text
        return response.json()


def test_promotion_uses_target_overrides_and_persists_once(artifact, tmp_path, monkeypatch):
    assert artifact["product"]["require_subscription"] is True
    assert artifact["product"]["policies_xml"] == PRODUCT_POLICY
    assert set(artifact["apis"]) == {"catalog", "billing"}
    assert artifact["apis"]["catalog"]["artifact"]["api_policy"] == API_POLICY
    assert artifact["apis"]["catalog"]["artifact"]["operation_policies"] == {"ReadItem": OP_POLICY}
    assert artifact["backends"] == ["catalog"]
    assert artifact["named_values"] == {"shop-label": {"display_name": "ShopLabel", "secret": True}}
    assert "http://source" not in json.dumps(artifact)
    assert "source-private-value" not in json.dumps(artifact)

    target = gateway(backends={"catalog": BackendConfig(url="http://old", description="target-local setting")})
    config_path = tmp_path / "gateway.json"
    config_path.write_text(target.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    before = config_path.read_bytes()
    with TestClient(create_app(config=target)) as client:
        body = {"artifact": artifact, "overrides": OVERRIDES}
        plan = client.post(PATH, json=body, headers=HEADERS)
        assert plan.status_code == 200, plan.text
        assert plan.json()["overrides"] == {key: list(value) for key, value in OVERRIDES.items()}
        assert "target-private-value" not in plan.text
        assert "http://target" not in plan.text
        assert "ReadItem" in plan.json()["diff"]
        assert config_path.read_bytes() == before
        assert client.get(PATH, headers=HEADERS).status_code == 404
        assert client.put(PATH, json=body, headers=HEADERS).status_code == 428

        match = {**HEADERS, "If-Match": plan.json()["etag"]}
        applied = client.put(PATH, json=body, headers=match)
        assert applied.status_code == 200, applied.text
        assert applied.json() == artifact
        assert client.get(PATH, headers=HEADERS).json() == artifact
        assert client.get("/catalog/item").status_code == 401
        assert client.get("/billing/item").status_code == 401
        saved = json.loads(config_path.read_bytes())
        assert saved["apis"]["catalog"]["products"] == ["shop"]
        assert saved["apis"]["billing"]["products"] == ["shop"]
        assert saved["apis"]["billing"]["upstream_base_url"] == "http://target-direct/api"
        assert saved["backends"]["catalog"]["url"] == "http://target-catalog/api"
        assert saved["backends"]["catalog"]["description"] == "target-local setting"
        assert saved["named_values"]["shop-label"]["value"] == "target-private-value"
        assert saved["named_values"]["shop-label"]["secret"] is True


def test_invalid_apply_and_stale_dependency_plan_preserve_disk(artifact, tmp_path, monkeypatch):
    target = gateway()
    config_path = tmp_path / "gateway.json"
    config_path.write_text(target.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    with TestClient(create_app(config=target)) as client:
        body = {"artifact": artifact, "overrides": OVERRIDES}
        initial = client.post(PATH, json=body, headers=HEADERS)
        assert initial.status_code == 200, initial.text
        match = {**HEADERS, "If-Match": initial.json()["etag"]}
        original = config_path.read_bytes()
        bad = {"artifact": artifact, "overrides": {**OVERRIDES, "api_upstreams": {"billing": "file:///etc/passwd"}}}
        assert client.put(PATH, json=bad, headers=match).status_code == 400
        invalid_policy = {
            **artifact["product"],
            "policies_xml": PRODUCT_POLICY.replace("exists-action='override'", "exists-action='{{ShopLabel}}'"),
        }
        bad = {**body, "artifact": {**artifact, "product": invalid_policy}}
        response = client.put(PATH, json=bad, headers=match)
        assert response.status_code == 400, response.text
        assert "target-private-value" not in response.text
        assert config_path.read_bytes() == original
        assert client.get(PATH, headers=HEADERS).status_code == 404
        assert client.put(PATH, json=body, headers=match).status_code == 200

        plan = client.post(PATH, json={"artifact": artifact}, headers=HEADERS)
        assert plan.status_code == 200, plan.text
        match = {**HEADERS, "If-Match": plan.json()["etag"]}

        async def apply_while_target_changes():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as http:
                saved = None

                async def stream():
                    nonlocal saved
                    update = await http.put(
                        "/apim/management/named-values/shop-label",
                        headers=HEADERS,
                        json={"value": "later-target-value"},
                    )
                    assert update.status_code == 200, update.text
                    saved = config_path.read_bytes()
                    yield json.dumps(body).encode()

                stale = await http.put(PATH, headers={**match, "Content-Type": "application/json"}, content=stream())
                return stale, saved

        stale, saved = asyncio.run(apply_while_target_changes())
        assert stale.status_code == 412
        assert config_path.read_bytes() == saved
        assert json.loads(saved)["named_values"]["shop-label"]["value"] == "later-target-value"


def test_product_bundle_permissions_and_validation_privacy(source, artifact, monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    source.control_plane = ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False)

    def global_role(role):
        return {"Authorization": "Bearer " + token(roles=[role])}

    reader = global_role("reader")
    contributor = global_role("contributor")
    scoped = {
        "Authorization": "Bearer " + token(apim_grants=[{"role": "contributor", "api_ids": ["catalog", "billing"]}])
    }
    body = {"artifact": artifact}
    with TestClient(create_app(config=source)) as client:
        assert client.get(PATH, headers=scoped).status_code == 403
        assert client.post(PATH, json=body, headers=scoped).status_code == 403
        assert client.put(PATH, json=body, headers=scoped).status_code == 403
        assert client.get(PATH, headers=reader).status_code == 200
        plan = client.post(PATH, json=body, headers=reader)
        assert plan.status_code == 200, plan.text
        match = {"If-Match": plan.json()["etag"]}
        assert client.put(PATH, json=body, headers={**reader, **match}).status_code == 403
        bad = {"artifact": artifact, "overrides": {"named_values": ["do-not-echo-this"]}}
        response = client.post(PATH, json=bad, headers=reader)
        assert response.status_code in (400, 422)
        assert "do-not-echo-this" not in response.text
        assert client.put(PATH, json=body, headers={**contributor, **match}).status_code == 200


def test_link_removal_cannot_unprotect_api(source, artifact):
    artifact["apis"].pop("billing")
    with TestClient(create_app(config=source)) as client:
        plan = client.post(PATH, json={"artifact": artifact}, headers=HEADERS)
        assert plan.status_code == 400
        assert "include all currently linked APIs" in plan.json()["detail"]
        assert set(client.get(PATH, headers=HEADERS).json()["apis"]) == {"catalog", "billing"}
