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


def test_reviewed_plan_binds_inputs_and_mode_without_baseline(change):
    cfg, body = change
    reviewed = {
        "artifact": body["artifact"],
        "overrides": {"api_upstreams": {"keep": "http://target/one"}},
        "reviewed": True,
    }
    with TestClient(create_app(config=cfg)) as client:
        plan = client.post(PATH, headers=HEADERS, json=reviewed)
        assert plan.status_code == 200, plan.text
        assert plan.json()["reviewed"] is True
        assert "http://target/one" not in plan.text
        changed_artifact = json.loads(json.dumps(reviewed))
        changed_artifact["artifact"]["products"]["keep"]["name"] = "Changed"
        changed_override = {**reviewed, "overrides": {"api_upstreams": {"keep": "http://target/two"}}}
        ordinary = {key: value for key, value in reviewed.items() if key != "reviewed"}
        for changed in (changed_artifact, changed_override, ordinary):
            assert client.put(PATH, headers=etag_headers(plan), json=changed).status_code == 412
        ordinary_plan = client.post(PATH, headers=HEADERS, json=ordinary)
        assert client.put(PATH, headers=etag_headers(ordinary_plan), json=reviewed).status_code == 412
        assert client.put(PATH, headers=etag_headers(plan), json=reviewed).status_code == 200


def test_reviewed_requires_strict_boolean(change):
    cfg, body = change
    with TestClient(create_app(config=cfg)) as client:
        assert client.post(PATH, headers=HEADERS, json={**body, "reviewed": "true"}).status_code == 400


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


def test_reconciliation_classifies_changes_without_values(change):
    cfg, body = change
    cfg.products["keep"].name = "live-secret-value"
    body["artifact"]["products"]["keep"]["name"] = "git-secret-value"
    cfg.policy_fragments["local"] = LOCAL_FRAGMENT
    body["artifact"]["policy_fragments"]["new"] = "<set-header name='x-new'><value>new</value></set-header>"
    cfg.policies_xml = "<policies><backend><forward-request /></backend></policies>"
    with TestClient(create_app(config=cfg)) as client:
        response = client.post(PATH, headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        report = response.json()["reconciliation"]
        assert report["products"]["conflicts"] == ["keep"]
        assert report["products"]["git_only"] == ["retire"]
        assert report["policy_fragments"]["live_only"] == ["local"]
        assert report["policy_fragments"]["git_only"] == ["new", "retire"]
        assert report["gateway_policy"] == "live_only"
        assert "secret-value" not in json.dumps(report)
        no_baseline = client.post(PATH, headers=HEADERS, json={"artifact": artifact(config())})
        assert no_baseline.status_code == 200, no_baseline.text
        assert "reconciliation" not in no_baseline.json()


def test_reconciliation_matching_changes_conflicts_and_order():
    from app.service_artifacts import _reconciliation

    baseline = artifact(config())
    desired = json.loads(json.dumps(baseline))
    live = json.loads(json.dumps(baseline))
    desired["products"]["keep"]["name"] = live["products"]["keep"]["name"] = "same"
    desired["policy_fragments"]["retire"] = "desired"
    live["policy_fragments"]["retire"] = "live"
    desired["gateway_policy"] = live["gateway_policy"] = "same"
    live["apis"]["local"] = live["apis"]["keep"]
    report = _reconciliation(live, desired, baseline)
    assert report["products"]["matching"] == ["keep"]
    assert report["policy_fragments"]["conflicts"] == ["retire"]
    assert report["gateway_policy"] == "matching"
    assert report["apis"]["live_only"] == ["local"]
    assert report["api_order"] == "unchanged"
    desired["apis"] = dict(reversed(list(desired["apis"].items())))
    assert _reconciliation(live, desired, baseline)["api_order"] == "git_only"


def test_invalid_baseline_product_is_rejected(change):
    cfg, body = change
    body["baseline"]["products"]["keep"]["state"] = "invalid"
    with TestClient(create_app(config=cfg)) as client:
        response = client.post(PATH, headers=HEADERS, json=body)
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid baseline product metadata"


def test_reconciliation_detects_operation_order_without_metadata_order(change):
    from app.service_artifacts import _reconciliation

    cfg, _ = change
    cfg.apis["keep"].operations["other"] = OperationConfig(name="Other", url_template="/other")
    baseline = artifact(cfg)
    desired = json.loads(json.dumps(baseline))
    live = json.loads(json.dumps(baseline))
    paths = desired["apis"]["keep"]["artifact"]["openapi"]["paths"]
    desired["apis"]["keep"]["artifact"]["openapi"]["paths"] = dict(reversed(list(paths.items())))
    assert _reconciliation(live, desired, baseline)["apis"]["git_only"] == ["keep"]
    with TestClient(create_app(config=cfg)) as client:
        plan = client.post(PATH, headers=HEADERS, json={"artifact": desired, "baseline": baseline})
        assert plan.status_code == 200, plan.text
        assert plan.json()["drift"]["apis"]["update"] == ["keep"]
    desired = json.loads(json.dumps(baseline))
    info = desired["apis"]["keep"]["artifact"]["openapi"]["info"]
    desired["apis"]["keep"]["artifact"]["openapi"]["info"] = dict(reversed(list(info.items())))
    assert _reconciliation(live, desired, baseline)["apis"]["git_only"] == []


def test_reconciliation_ignores_target_only_product_links():
    cfg = config()
    baseline = artifact(cfg)
    cfg.products["local"] = ProductConfig(name="Local")
    cfg.apis["keep"].products.append("local")
    desired = json.loads(json.dumps(baseline))
    desired["apis"]["keep"]["artifact"]["openapi"]["info"]["title"] = "Git edit"
    with TestClient(create_app(config=cfg)) as client:
        plan = client.post(PATH, headers=HEADERS, json={"artifact": desired, "baseline": baseline})
        assert plan.status_code == 200, plan.text
        report = plan.json()["reconciliation"]
        assert report["apis"]["git_only"] == ["keep"]
        assert report["apis"]["conflicts"] == []
        assert report["products"]["live_only"] == ["local"]


def test_reviewed_unlinks_preserve_local_links_and_allow_atomic_product_deletion():
    cfg = config()
    cfg.apis["keep"].products = ["keep", "retire"]
    baseline = artifact(cfg)
    desired = json.loads(json.dumps(baseline))
    desired["apis"]["keep"]["products"] = ["keep"]
    del desired["apis"]["retire"]
    del desired["products"]["retire"]
    del desired["policy_fragments"]["retire"]
    cfg.products["local"] = ProductConfig(name="Local")
    cfg.apis["keep"].products.append("local")
    body = {"artifact": desired, "baseline": baseline}
    with TestClient(create_app(config=cfg)) as client:
        ordinary = client.post(PATH, headers=HEADERS, json=body)
        assert ordinary.status_code == 200, ordinary.text
        assert ordinary.json()["unlinks"] == {"keep": ["retire"]}
        applied = client.put(PATH, headers=etag_headers(ordinary), json=body)
        assert applied.status_code == 200, applied.text
        assert set(applied.json()["apis"]["keep"]["products"]) == {"keep", "retire", "local"}
        request = {**body, "prune": True}
        plan = client.post(PATH, headers=HEADERS, json=request)
        assert plan.status_code == 200, plan.text
        assert plan.json()["unlinks"] == {"keep": ["retire"]}
        applied = client.put(PATH, headers=etag_headers(plan), json=request)
        assert applied.status_code == 200, applied.text
        assert set(applied.json()["apis"]["keep"]["products"]) == {"keep", "local"}
        assert "retire" not in applied.json()["products"]


def test_prune_rejects_last_subscription_product_and_unreviewed_removal():
    cfg = config()
    baseline = artifact(cfg)
    desired = json.loads(json.dumps(baseline))
    desired["apis"]["keep"]["products"] = []
    body = {"artifact": desired, "baseline": baseline}
    with TestClient(create_app(config=cfg)) as client:
        ordinary = client.post(PATH, headers=HEADERS, json=body)
        assert ordinary.status_code == 200, ordinary.text
        assert ordinary.json()["unlinks"] == {"keep": ["keep"]}
        prune = client.post(PATH, headers=HEADERS, json={**body, "prune": True})
        assert prune.status_code == 400
        assert "last subscription-enforcing product" in prune.text
        unreviewed = {"artifact": desired}
        assert client.post(PATH, headers=HEADERS, json=unreviewed).status_code == 400


def test_unlink_from_already_open_api_is_allowed():
    cfg = config()
    cfg.products["open"] = ProductConfig(name="Open", require_subscription=False)
    cfg.apis["keep"].products = ["keep", "open"]
    baseline = artifact(cfg)
    desired = json.loads(json.dumps(baseline))
    desired["apis"]["keep"]["products"] = ["open"]
    request = {"artifact": desired, "baseline": baseline, "prune": True}
    with TestClient(create_app(config=cfg)) as client:
        plan = client.post(PATH, headers=HEADERS, json=request)
        assert plan.status_code == 200, plan.text
        applied = client.put(PATH, headers=etag_headers(plan), json=request)
        assert applied.status_code == 200, applied.text
        assert applied.json()["apis"]["keep"]["products"] == ["open"]


def test_unlink_cannot_replace_protected_product_with_open_product():
    cfg = config()
    cfg.products["open"] = ProductConfig(name="Open", require_subscription=False)
    baseline = artifact(cfg)
    desired = json.loads(json.dumps(baseline))
    desired["apis"]["keep"]["products"] = ["open"]
    request = {"artifact": desired, "baseline": baseline, "prune": True}
    with TestClient(create_app(config=cfg)) as client:
        plan = client.post(PATH, headers=HEADERS, json=request)
        assert plan.status_code == 400
        assert "subscription-enforcing product" in plan.text
