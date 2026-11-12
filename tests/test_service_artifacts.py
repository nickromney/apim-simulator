import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import (
    ApiConfig,
    BackendConfig,
    GatewayConfig,
    NamedValueConfig,
    OperationConfig,
    ProductConfig,
    RouteConfig,
)
from app.control_plane import ControlPlaneConfig
from app.main import create_app

pytestmark = pytest.mark.contract("MGMT-SERVICE-ARTIFACT-PROMOTION")

PATH = "/apim/management/service/artifacts"
FRAGMENT_PATH = "/apim/management/policy-fragments/shared"
HEADERS = {"X-Apim-Tenant-Key": "service-test-key"}
FRAGMENT = "<set-header name='x-region' exists-action='override'><value>{{RegionLabel}}</value></set-header>"
POLICY = "<policies><outbound><base /><include-fragment fragment-id='shared' /></outbound></policies>"
OTHER_POLICY = "<policies><inbound><base /></inbound></policies>"
OVERRIDES = {
    "api_upstreams": {"billing": "http://target-billing/api", "status": "http://target-status/api"},
    "backends": {"catalog-backend": "http://target-catalog/api"},
    "named_values": {"region-label": "target-private-region"},
}


def gateway(**settings):
    return GatewayConfig.model_validate(
        {"allow_anonymous": True, "tenant_access": {"enabled": True, "primary_key": "service-test-key"}, **settings}
    )


def api(name, products, backend=None):
    return ApiConfig(
        name=name,
        path=name,
        products=products,
        backend=backend,
        upstream_base_url=f"http://source-{name}/api",
        operations={"ReadItem": OperationConfig(name="Read item", url_template="/item", policies_xml=POLICY)},
    )


@pytest.fixture
def source():
    return gateway(
        products={
            "shop": ProductConfig(name="Shop", policies_xml=POLICY),
            "partners": ProductConfig(name="Partners", policies_xml=POLICY),
        },
        apis={
            "catalog": api("catalog", ["shop", "partners"], backend="catalog-backend"),
            "billing": api("billing", ["shop"]),
            "status": api("status", []),
        },
        policies_xml=POLICY,
        policies_xml_documents=[OTHER_POLICY],
        policy_fragments={"shared": FRAGMENT},
        named_values={
            "region-label": NamedValueConfig(display_name="RegionLabel", value="source-private-region", secret=True)
        },
        backends={"catalog-backend": BackendConfig(url="http://source-catalog/api")},
    )


@pytest.fixture
def artifact(source):
    with TestClient(create_app(config=source)) as client:
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200, response.text
        return response.json()


def test_service_apply_preserves_target_local_state(artifact, tmp_path, monkeypatch):
    assert set(artifact["products"]) == {"shop", "partners"}
    assert list(artifact["apis"]) == ["catalog", "billing", "status"]
    assert artifact["apis"]["catalog"]["products"] == ["shop", "partners"]
    assert artifact["apis"]["status"]["products"] == []
    assert artifact["gateway_policy"] == POLICY
    assert artifact["gateway_policy_documents"] == [OTHER_POLICY]
    assert artifact["policy_fragments"] == {"shared": FRAGMENT}
    assert artifact["named_values"] == {"region-label": {"display_name": "RegionLabel", "secret": True}}
    assert "source-private-region" not in json.dumps(artifact)
    assert "http://source" not in json.dumps(artifact)
    assert "service-test-key" not in json.dumps(artifact)

    cfg = gateway(
        service={"name": "destination"},
        policy_fragments={"local": "<set-header name='x-local'><value>local</value></set-header>"},
        products={"shop": {"name": "Local shop", "groups": ["local"]}},
        groups={"local": {"id": "local", "name": "Local"}},
        backends={"catalog-backend": {"url": "http://old", "description": "target metadata"}},
        named_values={"region-label": {"display_name": "RegionLabel", "value": "override"}},
    )
    billing = api("billing", ["shop"])
    billing.operations["ReadItem"].policies_xml = (
        "<policies><outbound><set-header name='x-old' exists-action='{{RegionLabel}}'>"
        "<value>old</value></set-header></outbound></policies>"
    )
    cfg.apis["billing"] = billing
    path = tmp_path / "gateway.json"
    path.write_text(cfg.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("APIM_CONFIG_PATH", str(path))
    before = path.read_bytes()
    with TestClient(create_app(config=cfg)) as client:
        body = {"artifact": artifact, "overrides": OVERRIDES}
        plan = client.post(PATH, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        assert plan.json()["overrides"] == {key: sorted(value) for key, value in OVERRIDES.items()}
        assert "target-private-region" not in plan.text
        assert "http://target" not in plan.text
        assert "shared" in plan.json()["diff"]
        assert path.read_bytes() == before
        assert client.put(PATH, headers=HEADERS, json=body).status_code == 428

        match = {**HEADERS, "If-Match": plan.json()["etag"]}
        result = client.put(PATH, headers=match, json=body)
        assert result.status_code == 200, result.text
        assert result.headers["etag"] != plan.json()["etag"]
        assert client.get(PATH, headers=HEADERS).json() == result.json()
        assert set(result.json()["policy_fragments"]) == {"local", "shared"}
        assert client.get("/catalog/item").status_code == 401
        config = json.loads(path.read_bytes())
        assert config["service"]["name"] == "destination"
        assert config["products"]["shop"]["groups"] == ["local"]
        assert config["apis"]["catalog"]["products"] == ["shop", "partners"]
        assert config["apis"]["billing"]["upstream_base_url"] == "http://target-billing/api"
        assert config["backends"]["catalog-backend"]["url"] == "http://target-catalog/api"
        assert config["backends"]["catalog-backend"]["description"] == "target metadata"
        assert config["named_values"]["region-label"]["value"] == "target-private-region"
        assert config["named_values"]["region-label"]["secret"] is True


def test_missing_fragment_and_invalid_override_leave_state_untouched(artifact, tmp_path, monkeypatch):
    cfg = gateway(
        policy_fragments={"shared": FRAGMENT},
        named_values={"region-label": NamedValueConfig(display_name="RegionLabel", value="local-region")},
    )
    path = tmp_path / "gateway.json"
    path.write_text(cfg.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("APIM_CONFIG_PATH", str(path))
    before = path.read_bytes()
    with TestClient(create_app(config=cfg)) as client:
        etag = client.get(PATH, headers=HEADERS).headers["etag"]
        match = {**HEADERS, "If-Match": etag}
        missing = {**artifact, "policy_fragments": {}, "named_values": {}}
        without_named_values = {**OVERRIDES, "named_values": {}}
        invalid = {"artifact": missing, "overrides": without_named_values}
        assert client.post(PATH, headers=HEADERS, json=invalid).status_code == 400
        assert client.put(PATH, headers=match, json=invalid).status_code == 400

        bad_fragment = FRAGMENT.replace("exists-action='override'", "exists-action='{{RegionLabel}}'")
        invalid = {"artifact": {**artifact, "policy_fragments": {"shared": bad_fragment}}, "overrides": OVERRIDES}
        response = client.put(PATH, headers=match, json=invalid)
        assert response.status_code == 400, response.text
        assert "target-private-region" not in response.text
        assert path.read_bytes() == before
        assert client.get(PATH, headers=HEADERS).headers["etag"] == etag


def test_apply_rechecks_etag_after_streaming_request_body(source, artifact):
    with TestClient(create_app(config=source)) as client:
        body = {"artifact": artifact}
        plan = client.post(PATH, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        updated_fragment = FRAGMENT.replace("x-region", "x-region-edited")

        async def apply_while_target_changes():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app), base_url="http://test") as http:

                async def stream():
                    updated = await http.put(FRAGMENT_PATH, headers=HEADERS, json={"xml": updated_fragment})
                    assert updated.status_code == 200, updated.text
                    yield json.dumps(body).encode()

                headers = {**HEADERS, "Content-Type": "application/json", "If-Match": plan.json()["etag"]}
                return await http.put(PATH, headers=headers, content=stream())

        stale = asyncio.run(apply_while_target_changes())
        assert stale.status_code == 412, stale.text
        assert client.get(PATH, headers=HEADERS).json()["policy_fragments"]["shared"] == updated_fragment


def test_bundle_requires_service_access_and_preserves_product_links(source, artifact, monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    source.control_plane = ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False)
    reader = {"Authorization": "Bearer " + token(roles=["reader"])}
    writer = {"Authorization": "Bearer " + token(roles=["contributor"])}
    scoped = {"Authorization": "Bearer " + token(apim_grants=[{"role": "contributor", "api_ids": ["catalog"]}])}
    with TestClient(create_app(config=source)) as client:
        body = {"artifact": artifact}
        assert client.get(PATH, headers=scoped).status_code == 403
        assert client.post(PATH, headers=scoped, json=body).status_code == 403
        assert client.put(PATH, headers=scoped, json=body).status_code == 403
        plan = client.post(PATH, headers=reader, json=body)
        assert plan.status_code == 200, plan.text
        match = {"If-Match": plan.json()["etag"]}
        assert client.put(PATH, headers={**reader, **match}, json=body).status_code == 403
        invalid = {"artifact": artifact, "overrides": {"named_values": ["do-not-echo"]}}
        response = client.post(PATH, headers=reader, json=invalid)
        assert response.status_code == 400
        assert "do-not-echo" not in response.text
        artifact["apis"]["catalog"]["products"].remove("partners")
        assert client.put(PATH, headers={**writer, **match}, json=body).status_code == 400
        assert client.get(PATH, headers=reader).json()["apis"]["catalog"]["products"] == ["shop", "partners"]


def test_legacy_routes_cannot_be_silently_omitted(artifact):
    config = gateway(routes=[RouteConfig(name="legacy", path_prefix="/legacy", upstream_base_url="http://backend")])
    with TestClient(create_app(config=config)) as client:
        assert client.get(PATH, headers=HEADERS).status_code == 400
        response = client.post(PATH, headers=HEADERS, json={"artifact": artifact, "overrides": OVERRIDES})
        assert response.status_code == 400
        assert "legacy" in response.json()["detail"]


def test_service_apply_preserves_reviewed_api_and_product_link_order(source, artifact):
    source.products["local"] = ProductConfig(name="Target product")
    catalog = source.apis["catalog"]
    catalog.products.insert(1, "local")
    source.apis = {"catalog": catalog, "keep": api("keep", []), **source.apis}
    artifact["apis"] = {key: artifact["apis"][key] for key in ("status", "billing", "catalog")}
    artifact["apis"]["catalog"]["products"].reverse()
    with TestClient(create_app(config=source)) as client:
        body = {"artifact": artifact}
        plan = client.post(PATH, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        assert plan.json()["diff"]
        result = client.put(PATH, headers={**HEADERS, "If-Match": plan.json()["etag"]}, json=body)
        assert result.status_code == 200, result.text
        deployed = result.json()
        assert list(deployed["apis"]) == ["status", "keep", "billing", "catalog"]
        assert deployed["apis"]["catalog"]["products"] == ["partners", "local", "shop"]
