from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from scripts.verify_azure import _compare_case, _load_cases


def test_same_wrong_status_cannot_pass_parity():
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404, text="not found")))
    failure = _compare_case(
        client,
        {"path": "/fixture", "expected_status": 200},
        simulator_base_url="https://local.test",
        azure_base_url="https://azure.test",
    )
    assert failure and "expected 200" in failure


def test_each_target_uses_its_own_credential_and_shared_input():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, text="hello")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert (
            _compare_case(
                client,
                {
                    "path": "/fixture",
                    "expected_status": 200,
                    "expected_body": "hello",
                    "headers": {"X-Fixture": "shared"},
                },
                simulator_base_url="https://local.test",
                azure_base_url="https://azure.test",
                simulator_headers={"Ocp-Apim-Subscription-Key": "local"},
                azure_headers={"Ocp-Apim-Subscription-Key": "azure"},
            )
            is None
        )
    assert [r.headers["Ocp-Apim-Subscription-Key"] for r in requests] == ["local", "azure"]
    assert all(r.headers["X-Fixture"] == "shared" for r in requests)


@pytest.mark.parametrize("payload", [[], ["bad"], [{"path": "//other.test"}]])
def test_empty_or_invalid_runs_are_rejected(tmp_path, payload):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        _load_cases(str(path))


def test_deterministic_policy_fixtures_satisfy_expected_outcomes():
    path = Path(__file__).parents[1] / "examples/azure-validation/fixtures.py"
    spec = importlib.util.spec_from_file_location("parity_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config, cases = module.fixtures("sim-parity-test")
    config["subscription"]["subscriptions"] = {
        "fixture": {
            "id": "fixture",
            "name": "fixture",
            "api_id": "sim-parity-test",
            "keys": {"primary": "fixture-key", "secondary": "fixture-secondary"},
        }
    }
    with TestClient(create_app(config=GatewayConfig.model_validate(config))) as client:
        for case in cases:
            response = client.request(
                case["method"],
                case["path"],
                params=case.get("query"),
                headers={"Ocp-Apim-Subscription-Key": "fixture-key", **case.get("headers", {})},
                content=case.get("body_text"),
            )
            assert response.status_code == case["expected_status"], case["name"]
            assert response.text == case["expected_body"], case["name"]
