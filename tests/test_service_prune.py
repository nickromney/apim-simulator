import json

import pytest
from fastapi.testclient import TestClient

from app.config import (
    ApiConfig,
    ApiRevisionConfig,
    BackendConfig,
    GatewayConfig,
    NamedValueConfig,
    OperationConfig,
    ProductConfig,
)
from app.main import create_app

pytestmark = pytest.mark.contract("MGMT-SERVICE-ARTIFACT-PROMOTION")

PATH = "/apim/management/service/artifacts"
HEADERS = {"X-Apim-Tenant-Key": "prune-test-key"}
RETIRE_FRAGMENT = "<set-header name='x-retire'><value>old</value></set-header>"
RETIRE_POLICY = "<policies><outbound><base /><include-fragment fragment-id='retire' /></outbound></policies>"
LOCAL_FRAGMENT = "<set-header name='x-local'><value>local</value></set-header>"
DELETIONS = {"apis": ["retire"], "products": ["retire"], "policy_fragments": ["retire"]}
EMPTY_DELETIONS = {key: [] for key in DELETIONS}


def api(name, products, xml=None):
    return ApiConfig(
        name=name,
        path=name,
        products=products,
        upstream_base_url=f"http://backend/{name}",
        policies_xml=xml,
        operations={"item": OperationConfig(name="Read item", url_template="/item")},
    )


def config():
    return GatewayConfig.model_validate(
        {
            "allow_anonymous": True,
            "tenant_access": {"enabled": True, "primary_key": "prune-test-key"},
            "products": {"keep": ProductConfig(name="Keep"), "retire": ProductConfig(name="Retire")},
            "apis": {
                "keep": api("keep", ["keep"]),
                "secondary": api("secondary", []),
                "retire": api("retire", ["retire"], RETIRE_POLICY),
            },
            "policy_fragments": {"retire": RETIRE_FRAGMENT},
        }
    )


def artifact(cfg):
    with TestClient(create_app(config=cfg)) as client:
        response = client.get(PATH, headers=HEADERS)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.fixture
def change():
    source = config()
    baseline = artifact(source)
    desired = json.loads(json.dumps(baseline))
    for key in DELETIONS:
        del desired[key]["retire"]
    return source, {"artifact": desired, "baseline": baseline}


def etag_headers(plan):
    return {**HEADERS, "If-Match": plan.json()["etag"]}


def test_preview_reports_drift_and_default_apply_keeps_resources(change, tmp_path, monkeypatch):
    cfg, body = change
    cfg.apis["local"] = api("local", ["keep"])
    cfg.policy_fragments["local"] = LOCAL_FRAGMENT
    cfg.products["keep"].name = "Destination edit"
    cfg.policies_xml = "<policies><backend><forward-request /></backend></policies>"
    cfg.backends["stack-backend"] = BackendConfig(url="http://stack")
    cfg.named_values["stack-only"] = NamedValueConfig(value="local")
    body["overrides"] = {"api_upstreams": {"keep": "http://target/keep"}}
    path = tmp_path / "gateway.json"
    path.write_text(cfg.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("APIM_CONFIG_PATH", str(path))
    before = path.read_bytes()
    with TestClient(create_app(config=cfg)) as client:
        plan = client.post(PATH, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        preview = plan.json()
        assert preview["deletions"] == DELETIONS
        assert preview["prune"] is False
        assert preview["drift"]["apis"]["target_only"] == ["local"]
        assert preview["drift"]["products"]["update"] == ["keep"]
        assert preview["drift"]["policy_fragments"]["target_only"] == ["local"]
        assert preview["drift"]["gateway_policy"] is True
        assert preview["drift"]["overrides"]["api_upstreams"] == ["keep"]
        assert "http://target/keep" not in plan.text
        assert path.read_bytes() == before

        result = client.put(PATH, headers=etag_headers(plan), json=body)
        assert result.status_code == 200, result.text
        assert {"retire", "local"} <= result.json()["apis"].keys()
        prune_body = {**body, "prune": True}
        plan = client.post(PATH, headers=HEADERS, json=prune_body)
        assert plan.status_code == 200, plan.text
        assert plan.json()["drift"]["overrides"]["api_upstreams"] == []
        assert plan.json()["deletions"] == DELETIONS

        result = client.put(PATH, headers=etag_headers(plan), json=prune_body)
        assert result.status_code == 200, result.text
        applied = result.json()
        assert set(applied["apis"]) == {"keep", "secondary", "local"}
        assert set(applied["products"]) == {"keep"}
        assert applied["policy_fragments"] == {"local": LOCAL_FRAGMENT}
        assert applied["apis"]["local"]["products"] == ["keep"]
        saved = json.loads(path.read_bytes())
        assert "stack-backend" in saved["backends"]
        assert "stack-only" in saved["named_values"]
        repeat = client.post(PATH, headers=HEADERS, json=prune_body)
        assert repeat.json()["deletions"] == EMPTY_DELETIONS
        assert repeat.json()["diff"] == ""


def test_prune_tag_requires_preview_and_binds_order_and_live_state(change):
    cfg, body = change
    with TestClient(create_app(config=cfg)) as client:
        ordinary = client.post(PATH, headers=HEADERS, json=body)
        prune_body = {**body, "prune": True}
        assert client.put(PATH, headers=etag_headers(ordinary), json=prune_body).status_code == 412
        plan = client.post(PATH, headers=HEADERS, json=prune_body)
        assert plan.status_code == 200, plan.text
        reordered = {
            **prune_body,
            "artifact": {**body["artifact"], "apis": dict(reversed(list(body["artifact"]["apis"].items())))},
        }
        assert client.put(PATH, headers=etag_headers(plan), json=reordered).status_code == 412
        changed = client.put("/apim/management/policy-fragments/local", headers=HEADERS, json={"xml": LOCAL_FRAGMENT})
        assert changed.status_code == 200
        assert client.put(PATH, headers=etag_headers(plan), json=prune_body).status_code == 412
        current = client.get(PATH, headers=HEADERS).json()
        assert set(current["apis"]) == {"keep", "secondary", "retire"}
        assert current["policy_fragments"]["local"] == LOCAL_FRAGMENT


@pytest.mark.parametrize("blocker", ["subscription", "link", "fragment", "revision_fragment", "api_reference", "lock"])
def test_prune_previews_blocked_removals_but_rejects_destructive_plan(change, blocker):
    cfg, body = change
    if blocker == "subscription":
        data = {
            "id": "local",
            "name": "Local subscription",
            "keys": {"primary": "local-primary", "secondary": "local-secondary"},
            "products": ["retire"],
        }
        cfg.subscription = type(cfg.subscription).model_validate({"subscriptions": {"local": data}})
    elif blocker == "link":
        cfg.apis["keep"].products.append("retire")
    elif blocker == "fragment":
        cfg.apis["local"] = api("local", [], RETIRE_POLICY)
    elif blocker == "revision_fragment":
        definition = cfg.apis["keep"].model_dump(mode="json", exclude={"revisions", "releases"})
        definition["policies_xml"] = RETIRE_POLICY
        cfg.apis["keep"].revisions["2"] = ApiRevisionConfig(revision="2", is_current=False, definition=definition)
    elif blocker == "api_reference":
        cfg.apis["keep"].source_api_id = "service/destination/apis/retire;rev=2"
    else:
        cfg.security_governance.delete_locks = {"apis": ["retire"]}

    with TestClient(create_app(config=cfg)) as client:
        baseline = client.get(PATH, headers=HEADERS)
        ordinary = client.post(PATH, headers=HEADERS, json=body)
        assert ordinary.status_code == 200, ordinary.text
        assert ordinary.json()["deletions"] == DELETIONS
        prune = client.post(PATH, headers=HEADERS, json={**body, "prune": True})
        assert prune.status_code == (409 if blocker == "lock" else 400), prune.text
        assert client.get(PATH, headers=HEADERS).headers["etag"] == baseline.headers["etag"]


def test_pruning_last_api_removes_materialized_routes(change):
    cfg, body = change
    cfg.apis = {"retire": cfg.apis["retire"]}
    cfg.products = {"retire": cfg.products["retire"]}
    body["artifact"]["apis"] = {}
    body["artifact"]["products"] = {}
    with TestClient(create_app(config=cfg)) as client:
        assert client.get("/retire/item").status_code == 401
        request = {**body, "prune": True}
        plan = client.post(PATH, headers=HEADERS, json=request)
        assert plan.status_code == 200, plan.text
        applied = client.put(PATH, headers=etag_headers(plan), json=request)
        assert applied.status_code == 200, applied.text
        assert client.get("/retire/item").status_code == 404
        assert applied.json()["apis"] == {}
