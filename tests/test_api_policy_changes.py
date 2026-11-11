import json

import pytest
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import ApiConfig, GatewayConfig, TenantAccessConfig
from app.control_plane import ControlPlaneConfig
from app.effective_policy import EMPTY_POLICY_XML
from app.main import create_app

pytestmark = pytest.mark.contract("MGMT-API-POLICY-PLAN")

ORIGINAL = "<policies>\n  <inbound><base /></inbound>\n</policies>"
EDIT_A = "<policies>\n  <inbound><set-header name='x-a'><value>A</value></set-header></inbound>\n</policies>"
EDIT_B = "<policies>\n  <inbound><set-header name='x-b'><value>B</value></set-header></inbound>\n</policies>"
ENDPOINT = "/apim/management/policies/api/orders"
TENANT = {"X-Apim-Tenant-Key": "tenant"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    config = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        control_plane=ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=True),
        apis={
            name: ApiConfig(name=name, path=name, upstream_base_url="http://backend", policies_xml=ORIGINAL)
            for name in ("orders", "payroll")
        },
    )
    config_path = tmp_path / "apim.json"
    config_path.write_text(json.dumps(config.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    with TestClient(create_app(config=config)) as http:
        yield http


def scoped(role):
    return {"Authorization": "Bearer " + token(apim_grants=[{"role": role, "api_ids": ["orders"]}])}


def test_preview_is_validated_and_does_not_persist(client, tmp_path):
    original_disk = (tmp_path / "apim.json").read_bytes()
    before = client.get(ENDPOINT, headers=TENANT)
    preview = client.post(ENDPOINT, headers=TENANT, json={"xml": EDIT_A})
    assert preview.status_code == 200
    result = preview.json()
    assert result["scope_type"] == "api"
    assert result["scope_name"] == "orders"
    assert result["etag"] == before.headers["etag"]
    assert "-  <inbound><base /></inbound>" in result["diff"]
    assert "+  <inbound><set-header" in result["diff"]
    assert client.get(ENDPOINT, headers=TENANT).json()["xml"] == ORIGINAL
    assert (tmp_path / "apim.json").read_bytes() == original_disk

    noop = client.post(ENDPOINT, headers=TENANT, json={"xml": ORIGINAL})
    assert noop.status_code == 200
    assert noop.json()["diff"] == ""
    assert noop.json()["etag"] == before.headers["etag"]
    invalid = client.post(ENDPOINT, headers=TENANT, json={"xml": "<policies><inbound>"})
    assert invalid.status_code == 400
    assert client.get(ENDPOINT, headers=TENANT).json()["xml"] == ORIGINAL


def test_guarded_apply_rejects_stale_plan_and_unguarded_put_still_works(client, tmp_path):
    plan_a = client.post(ENDPOINT, headers=TENANT, json={"xml": EDIT_A}).json()
    plan_b = client.post(ENDPOINT, headers=TENANT, json={"xml": EDIT_B}).json()
    assert plan_a["etag"] == plan_b["etag"]
    saved = client.put(ENDPOINT, headers={**TENANT, "If-Match": plan_a["etag"]}, json={"xml": EDIT_A})
    assert saved.status_code == 200
    assert saved.headers["etag"] != plan_a["etag"]
    saved_disk = (tmp_path / "apim.json").read_bytes()
    assert json.loads(saved_disk)["apis"]["orders"]["policies_xml"] == EDIT_A
    stale = client.put(ENDPOINT, headers={**TENANT, "If-Match": plan_b["etag"]}, json={"xml": EDIT_B})
    assert stale.status_code == 412
    assert (tmp_path / "apim.json").read_bytes() == saved_disk
    readback = client.get(ENDPOINT, headers=TENANT)
    assert readback.json()["xml"] == EDIT_A
    assert readback.headers["etag"] == saved.headers["etag"]
    unguarded = client.put(ENDPOINT, headers=TENANT, json={"xml": EDIT_B})
    assert unguarded.status_code == 200
    assert client.get(ENDPOINT, headers=TENANT).json()["xml"] == EDIT_B
    assert unguarded.headers["etag"] != saved.headers["etag"]


def test_scoped_reader_can_preview_but_not_write_or_access_other_api(client):
    reader = scoped("reader")
    contributor = scoped("contributor")
    assert client.post(ENDPOINT, headers=reader, json={"xml": EDIT_A}).status_code == 200
    assert client.put(ENDPOINT, headers=reader, json={"xml": EDIT_A}).status_code == 403
    other = "/apim/management/policies/api/payroll"
    assert client.post(other, headers=reader, json={"xml": EDIT_A}).status_code == 403
    assert client.post(other, headers=contributor, json={"xml": EDIT_A}).status_code == 403
    assert client.get(ENDPOINT, headers=reader).json()["xml"] == ORIGINAL


def test_preview_reports_line_ending_only_changes(client):
    preview = client.post(ENDPOINT, headers=TENANT, json={"xml": ORIGINAL.replace("\n", "\r\n")})
    assert preview.status_code == 200
    assert preview.json()["diff"]
    assert client.get(ENDPOINT, headers=TENANT).json()["xml"] == ORIGINAL


def test_etag_is_bound_to_api_id(client):
    other = "/apim/management/policies/api/payroll"
    orders = client.get(ENDPOINT, headers=TENANT)
    payroll = client.get(other, headers=TENANT)
    assert orders.json()["xml"] == payroll.json()["xml"] == ORIGINAL
    assert orders.headers["etag"] != payroll.headers["etag"]
    response = client.put(other, headers={**TENANT, "If-Match": orders.headers["etag"]}, json={"xml": EDIT_A})
    assert response.status_code == 412
    assert client.get(other, headers=TENANT).json()["xml"] == ORIGINAL


def test_absent_and_explicit_empty_policy_have_distinct_plans_and_etags(client):
    client.app.state.gateway_config.apis["orders"].policies_xml = None
    absent = client.get(ENDPOINT, headers=TENANT)
    assert absent.json()["xml"] == EMPTY_POLICY_XML
    plan = client.post(ENDPOINT, headers=TENANT, json={"xml": EMPTY_POLICY_XML}).json()
    assert "+" + EMPTY_POLICY_XML in plan["diff"]
    assert plan["etag"] == absent.headers["etag"]

    saved = client.put(ENDPOINT, headers=TENANT, json={"xml": ""})
    assert saved.status_code == 200
    assert saved.json()["xml"] == EMPTY_POLICY_XML
    assert saved.headers["etag"] != absent.headers["etag"]
    stale = client.put(ENDPOINT, headers={**TENANT, "If-Match": plan["etag"]}, json={"xml": EDIT_A})
    assert stale.status_code == 412
    assert client.post(ENDPOINT, headers=TENANT, json={"xml": EMPTY_POLICY_XML}).json()["diff"] == ""
