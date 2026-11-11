from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.main import create_app
from app.management_service import ManagementService


def test_targeted_authoring_preserves_secret_metadata_and_policy(monkeypatch):
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    policy = '<policies><inbound><set-header name="x-secret" exists-action="override"><value>hidden-value</value></set-header></inbound></policies>'
    cfg = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        named_values={"secret": {"display_name": "secret", "value": "hidden-value", "secret": True}},
        apis={
            "a": ApiConfig(
                name="a",
                path="a",
                upstream_base_url="http://backend",
                operations={
                    "o": OperationConfig(
                        name="o",
                        method="GET",
                        url_template="/o",
                        description="hidden-value",
                        policies_xml=policy,
                        responses=[
                            {
                                "status_code": 201,
                                "representations": [
                                    {
                                        "content_type": "application/json",
                                        "examples": [{"name": "other", "value": {"secret": "hidden-value"}}],
                                    }
                                ],
                            }
                        ],
                    )
                },
            )
        },
    )
    app = create_app(config=cfg)
    path = "/apim/management/apis/a/operations/o"
    with TestClient(app) as client:
        body = {"status_code": 200, "content_type": "application/json", "value": {"ok": True}}
        assert client.put(path + "/response-example", json=body).status_code == 403
        assert (
            client.put(path + "/response-example", headers={"X-Apim-Tenant-Key": "tenant"}, json=body).status_code
            == 200
        )
        assert (
            client.put(
                path + "/mock-response",
                headers={"X-Apim-Tenant-Key": "tenant"},
                json={"enabled": True, "status_code": 200, "content_type": "application/json"},
            ).status_code
            == 200
        )
        operation = app.state.gateway_config.apis["a"].operations["o"]
        assert operation.description == "hidden-value"
        assert "hidden-value" in operation.policies_xml
        assert "mock-response" in operation.policies_xml
        assert operation.responses[0].representations[0].examples[0].value == {"secret": "hidden-value"}
        assert operation.responses[1].representations[0].examples[0].value == {"ok": True}


def test_failure_paths_do_not_change_runtime_config(monkeypatch):
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    cfg = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        named_values={"secret": {"value": "hidden-value", "secret": True}},
        apis={
            "a": ApiConfig(
                name="a",
                path="a",
                upstream_base_url="http://backend",
                operations={
                    "o": OperationConfig(
                        name="o",
                        method="GET",
                        url_template="/o",
                        policies_xml='<policies><inbound><choose><when condition="@(true)"><mock-response status-code="200" /></when></choose></inbound></policies>',
                        responses=[
                            {
                                "status_code": 200,
                                "representations": [
                                    {
                                        "content_type": "application/json",
                                        "examples": [{"name": "secret", "value": {"secret": "hidden-value"}}],
                                    }
                                ],
                            }
                        ],
                    )
                },
            )
        },
    )
    app = create_app(config=cfg)
    path = "/apim/management/apis/a/operations/o"
    headers = {"X-Apim-Tenant-Key": "tenant"}
    with TestClient(app) as client:
        before = app.state.gateway_config.model_dump(mode="json")
        assert (
            client.put(
                path + "/response-example",
                headers=headers,
                json={
                    "status_code": 200,
                    "content_type": "application/json",
                    "example_name": "secret",
                    "value": {"secret": "***"},
                },
            ).status_code
            == 409
        )
        assert (
            client.put(
                path + "/response-example",
                headers=headers,
                json={"status_code": 999, "content_type": "application/json", "value": 1},
            ).status_code
            == 422
        )
        assert (
            client.put(
                path + "/mock-response",
                headers=headers,
                json={"enabled": True, "status_code": 200, "content_type": "application/json"},
            ).status_code
            == 409
        )
        assert app.state.gateway_config.model_dump(mode="json") == before

        def fail(_self, _cfg):
            from fastapi import HTTPException

            raise HTTPException(500, "Persistence failed")

        monkeypatch.setattr(ManagementService, "persist_or_apply_config", fail)
        assert (
            client.put(
                path + "/response-example",
                headers=headers,
                json={"status_code": 201, "content_type": "application/json", "value": 1},
            ).status_code
            == 500
        )
        assert app.state.gateway_config.model_dump(mode="json") == before


def test_mock_json_strings_round_trip_without_changing_legacy_examples(monkeypatch, tmp_path):
    import json

    config_path = tmp_path / "runtime.json"
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        apis={
            "a": ApiConfig(
                name="a",
                path="a",
                upstream_base_url="http://backend",
                operations={"o": OperationConfig(name="o", method="GET", url_template="/o")},
            )
        },
    )
    app = create_app(config=cfg)
    path = "/apim/management/apis/a/operations/o"
    headers = {"X-Apim-Tenant-Key": "tenant"}
    with TestClient(app) as client:
        for index, value in enumerate(("hello", "true", '{"x":1}')):
            body = {"status_code": 200, "content_type": "application/json", "value": value}
            if index:
                body["example_name"] = "example"
            assert client.put(path + "/response-example", headers=headers, json=body).status_code == 200
            assert (
                client.put(
                    path + "/mock-response",
                    headers=headers,
                    json={"enabled": True, "status_code": 200, "content_type": "application/json"},
                ).status_code
                == 200
            )
            assert client.get("/a/o").json() == value
            exported = client.get("/apim/management/apis/a/export", headers=headers).json()
            sample = exported["paths"]["/o"]["get"]["responses"]["200"]["content"]["application/json"]["examples"][
                "example"
            ]
            assert sample["value"] == value and "value_format" not in sample
            reloaded = GatewayConfig.model_validate(json.loads(config_path.read_text()))
            assert reloaded.apis["a"].operations["o"].responses[0].representations[0].examples[0].value_format == "json"
        operation = app.state.gateway_config.apis["a"].operations["o"]
        example = operation.responses[0].representations[0].examples[0]
        example.value, example.value_format = '{"legacy":1}', "auto"
        assert client.get("/a/o").json() == {"legacy": 1}


def test_mock_rejects_unreachable_direct_return(monkeypatch):
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    policy = '<policies><inbound><return-response><set-status code="200" reason="OK" /></return-response></inbound></policies>'
    cfg = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        apis={
            "a": ApiConfig(
                name="a",
                path="a",
                upstream_base_url="http://backend",
                operations={"o": OperationConfig(name="o", method="GET", url_template="/o", policies_xml=policy)},
            )
        },
    )
    app = create_app(config=cfg)
    with TestClient(app) as client:
        result = client.put(
            "/apim/management/apis/a/operations/o/mock-response",
            headers={"X-Apim-Tenant-Key": "tenant"},
            json={"enabled": True, "status_code": 200, "content_type": "application/json"},
        )
        assert result.status_code == 409
        assert app.state.gateway_config.apis["a"].operations["o"].policies_xml == policy
