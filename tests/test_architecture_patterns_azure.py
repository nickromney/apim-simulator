from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "examples" / "architecture-patterns" / "azure_compare.py"
SPEC = importlib.util.spec_from_file_location("architecture_patterns_azure_compare", MODULE_PATH)
assert SPEC and SPEC.loader
azure_compare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = azure_compare
SPEC.loader.exec_module(azure_compare)


@pytest.fixture
def fixture_data() -> dict:
    return json.loads(azure_compare.FIXTURE_PATH.read_text(encoding="utf-8"))


def test_pattern_configs_have_well_formed_policy_xml(fixture_data: dict) -> None:
    assert azure_compare.validate_fixture(fixture_data) == list(azure_compare.PATTERNS)
    configs = (
        azure_compare.FIXTURE_PATH,
        ROOT / "examples" / "architecture-patterns-extra" / "apim.json",
        ROOT / "examples" / "bff" / "apim.json",
        ROOT / "examples" / "bff" / "apim.internal.json",
    )
    outcomes = [azure_compare.validate_config(path) for path in configs]
    assert [item["xml_well_formed"] for item in outcomes] == [5, 1, 2, 1]
    assert all(item["file"] for item in outcomes)


@pytest.mark.parametrize("relative_path", ["examples/architecture-patterns/apim.json", "examples/bff/apim.json"])
def test_demo_jwt_policies_follow_azure_child_order(relative_path: str) -> None:
    # Azure rejected the previous order even though the simulator parsed it.
    data = json.loads((ROOT / relative_path).read_text())
    for api in data["apis"].values():
        root = azure_compare.ET.fromstring(api["policies_xml"])
        for jwt_policy in root.findall(".//validate-jwt"):
            assert [child.tag for child in jwt_policy] == ["issuer-signing-keys", "audiences", "issuers"]


def test_loopback_rewrite_keeps_mock_routes_and_host_header(fixture_data: dict) -> None:
    source = fixture_data["apis"]["aggregation"]["policies_xml"]
    fixture_api_id = "aps-fixture-123"
    rewritten = azure_compare.inject_loopback(source, "apim.internal.azure-api.net", fixture_api_id)
    assert "http://pattern-backends:8000" not in rewritten
    assert "https://127.0.0.1/aps-fixture-123/api/orders-fail" in rewritten
    assert rewritten.count('name="Host"') == 4
    assert "apim.internal.azure-api.net" in rewritten


def test_mock_policy_contains_only_public_deterministic_fixture() -> None:
    status, body = azure_compare.MOCKS["catalog"]
    xml = azure_compare.fixture_policy(status, body)
    assert '<set-status code="200"' in xml
    assert "<return-response>" in xml
    assert "gatewayTransform" in xml
    assert "context.Request.Headers.GetValueOrDefault" in xml
    assert "context.RequestId.ToString()" in xml


def test_expected_error_status_is_required_for_a_match() -> None:
    response = httpx.Response(401, json={"detail": "unauthorized"})
    assert not azure_compare.compare_response(response, 200)["passed"]
    assert azure_compare.compare_response(response, 401)["passed"]


def test_management_write_accepts_xml_but_ownership_get_requires_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        azure_compare.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="<policies />", stderr=""),
    )
    assert azure_compare.management_request("put", "https://management.azure.com/policy") is None
    with pytest.raises(azure_compare.AzureCliError):
        azure_compare.management_request("get", "https://management.azure.com/api")


def test_management_404_with_cli_prefix_is_recognized() -> None:
    stderr = 'ERROR: Not Found({"error":{"code":"ResourceNotFound","message":"Missing API"}})'
    assert azure_compare._azure_error_code(stderr) == "ResourceNotFound"


def test_collision_does_not_register_or_delete_existing_api(monkeypatch: pytest.MonkeyPatch) -> None:
    target = azure_compare.Target("sub", "rg", "service", "https://service.azure-api.net")
    owned = {}
    methods = []

    def existing(method, url, body=None, **kwargs):
        methods.append(method)
        return {"properties": {"description": "same-owner"}}

    monkeypatch.setattr(azure_compare, "management_request", existing)
    with pytest.raises(azure_compare.AzureCliError):
        azure_compare.deploy_api(target, "existing", {}, "same-owner", owned)
    assert owned == {}
    assert azure_compare.cleanup_owned(target, owned) == []
    assert methods == ["get"]


def test_interrupt_still_cleans_temporary_api(fixture_data: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    target = azure_compare.Target("sub", "rg", "service", "https://service.azure-api.net")
    resources = {}

    def fake_request(method, url, body=None, **kwargs):
        api_id = url.split("/apis/", 1)[1].split("?", 1)[0].split("/", 1)[0]
        if method == "get":
            return resources.get(api_id)
        if method == "put":
            resources[api_id] = body
        if method == "delete":
            resources.pop(api_id)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(azure_compare, "management_request", fake_request)
    monkeypatch.setattr(azure_compare, "fixture_operations", interrupt)
    with pytest.raises(KeyboardInterrupt):
        azure_compare.run_live(target, fixture_data, 1, validate_only=True)
    assert resources == {}


def test_delete_refuses_an_api_without_the_run_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    target = azure_compare.Target("sub", "rg", "service", "https://service.azure-api.net")
    requests = []

    def fake_request(method: str, url: str, body=None, *, missing_ok=False):
        requests.append(method)
        if method == "get":
            return {"properties": {"description": "someone-elses-api"}}
        return None

    monkeypatch.setattr(azure_compare, "management_request", fake_request)
    assert not azure_compare.delete_owned_api(target, "existing-api", "this-run")
    assert requests == ["get"]


def test_policy_acceptance_cleans_only_apis_created_by_this_run(
    fixture_data: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = azure_compare.Target("sub", "rg", "service", "https://service.azure-api.net")
    resources: dict[str, dict] = {}
    calls: list[tuple[str, str, dict | None]] = []

    def fake_request(method: str, url: str, body=None, *, missing_ok=False):
        calls.append((method, url, body))
        api_id = url.split("/apis/", maxsplit=1)[1].split("?", maxsplit=1)[0].split("/", maxsplit=1)[0]
        if url.endswith("/policies/policy?api-version=2022-08-01") and "/apis/aps-offloading-" in url:
            raise azure_compare.AzureCliError("Policy rejected")
        if method == "get":
            return resources.get(api_id)
        if method == "put" and "/apis/" in url and "/operations/" not in url and "/policies/" not in url:
            resources[api_id] = body
        if method == "delete":
            resources.pop(api_id, None)
        return None

    monkeypatch.setattr(azure_compare, "management_request", fake_request)
    report = azure_compare.run_live(target, fixture_data, 0.1, validate_only=True)
    assert report["status"] == "incomplete"
    assert report["mode"] == "azure-policy-acceptance"
    assert len(report["cleanup"]) == 3
    assert all(item["deleted"] for item in report["cleanup"])
    deletes = [url for method, url, _ in calls if method == "delete"]
    assert len(deletes) == 3
    assert not resources
    assert all("/apis/aps-" in url for url in deletes)


def test_validate_target_requires_https_gateway_origin() -> None:
    args = azure_compare.parse_args(
        [
            "--subscription-id",
            "sub",
            "--resource-group",
            "rg",
            "--service-name",
            "service",
            "--gateway-url",
            "http://service",
        ]
    )
    with pytest.raises(ValueError, match="HTTPS gateway origin"):
        azure_compare.validate_target(args)


@pytest.mark.parametrize("probe_fails", [False, True])
def test_private_runtime_probe_uses_same_cleanup_path(fixture_data: dict, monkeypatch, probe_fails: bool) -> None:
    target = azure_compare.Target(
        "example-subscription", "example-group", "private-example-apim", "https://private-example-apim.azure-api.net"
    )
    owned_seen = []

    def deploy_pattern(target, fixture, run_id, fixture_path, owner, owned):
        owned["aps-routing-123456abcdef"] = owner
        return {"routing:catalog": "test-api/catalog"}, []

    def probe(root, endpoints, fixture, timeout):
        assert root == target.gateway_url
        assert endpoints["routing:catalog"] == "test-api/catalog"
        if probe_fails:
            raise azure_compare.AzureCliError("ValidationError", f"Rejected policy on {target.gateway_url}")
        return [{"name": "routing:catalog", "passed": True}]

    def cleanup(target, owned):
        owned_seen.extend(owned)
        return [{"api": key, "deleted": True} for key in owned]

    monkeypatch.setattr(azure_compare, "deploy_api", lambda *args: None)
    monkeypatch.setattr(azure_compare, "fixture_operations", lambda *args: None)
    monkeypatch.setattr(azure_compare, "deploy_pattern_apis", deploy_pattern)
    monkeypatch.setattr(azure_compare, "cleanup_owned", cleanup)
    report = azure_compare.execute_comparison(target, fixture_data, 15, False, runtime_probe=probe)
    assert report["status"] == ("incomplete" if probe_fails else "passed")
    assert owned_seen == ["aps-routing-123456abcdef"]
    assert report["cleanup"] == [{"api": "aps-routing-<run-id>", "deleted": True}]
    assert report["target"] == "configured-apim-service"
    serialized = json.dumps(report)
    assert "123456abcdef" not in serialized
    assert target.service not in serialized
    assert target.subscription not in serialized
    assert target.resource_group not in serialized
