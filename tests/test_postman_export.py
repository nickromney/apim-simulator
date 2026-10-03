from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import (
    ApiConfig,
    ApiVersionSetConfig,
    GatewayConfig,
    NamedValueConfig,
    OperationConfig,
    OperationParameterConfig,
    OperationRequestMetadataConfig,
    TenantAccessConfig,
)
from app.control_plane import ControlPlaneConfig
from app.openapi_export import build_openapi_export_router
from app.postman_export import SCHEMA, build_postman


def api():
    return ApiConfig(
        name="Widgets",
        path="widgets",
        upstream_base_url="http://private-backend.example.test",
        operations={
            "save": OperationConfig(
                name="Save widget",
                method="POST",
                url_template="/{id}?mode=save",
                template_parameters=[{"name": "id", "type": "string", "required": True}],
                request={
                    "query_parameters": [{"name": "region", "type": "string", "required": True, "default_value": "eu"}],
                    "headers": [
                        {
                            "name": "Authorization",
                            "type": "string",
                            "required": False,
                            "default_value": "Bearer private-token",
                        }
                    ],
                    "representations": [
                        {
                            "content_type": "application/json",
                            "examples": [{"name": "widget", "value": {"name": "sample"}}],
                        }
                    ],
                },
            )
        },
    )


def client(cfg):
    app = FastAPI()
    app.state.gateway_config = cfg
    app.include_router(build_openapi_export_router())
    return TestClient(app)


@pytest.mark.contract("EXPORT-POSTMAN")
def test_postman_preserves_contract_and_is_deterministic():
    value = api()
    collection = build_postman(value)
    assert collection == build_postman(value)
    assert collection["info"]["schema"] == SCHEMA
    request = collection["item"][0]["request"]
    assert request["url"]["raw"] == "{{base_url}}/widgets/:id?mode=save&region=eu"
    assert request["url"]["variable"] == [{"key": "id", "value": ""}]
    assert request["method"] == "POST"
    assert json.loads(request["body"]["raw"]) == {}
    assert {"key": "Authorization", "value": "Bearer {{bearer_token}}"} in request["header"]
    assert "private-backend" not in json.dumps(collection)
    assert "private-token" not in json.dumps(collection)
    assert all(variable["value"] == "" for variable in collection["variable"][1:])


@pytest.mark.parametrize(
    "scheme,settings,location",
    [
        ("Segment", {}, "path"),
        ("Query", {"version_query_name": "api-version"}, "query"),
        ("Header", {"version_header_name": "X-Version"}, "header"),
    ],
)
def test_postman_version_selectors(scheme, settings, location):
    value = api().model_copy(update={"api_version": "v2"})
    versions = ApiVersionSetConfig(display_name="Versions", versioning_scheme=scheme, **settings)
    request = build_postman(value, version_set=versions)["item"][0]["request"]
    if location == "path":
        assert "/widgets/v2/:id" in request["url"]["raw"]
    elif location == "query":
        assert {"key": "api-version", "value": "v2", "disabled": False} in request["url"]["query"]
    else:
        assert {"key": "X-Version", "value": "v2"} in request["header"]


def test_query_discriminated_operations_are_not_collapsed():
    value = api()
    value.operations["other"] = OperationConfig(name="Other", method="POST", url_template="/{id}?mode=other")
    assert len(build_postman(value)["item"]) == 2


def test_endpoint_legacy_auth_redacts_configured_secrets_and_preserves_openapi():
    value = api()
    value.operations["save"].description = "synthetic-admin synthetic-tenant synthetic-named"
    cfg = GatewayConfig(
        admin_token="synthetic-admin",
        tenant_access=TenantAccessConfig(enabled=True, primary_key="synthetic-tenant"),
        named_values={"secret": NamedValueConfig(value="synthetic-named", secret=True)},
        apis={"widgets": value},
    )
    with client(cfg) as http:
        path = "/apim/management/apis/widgets/export"
        assert http.get(path + "?format=postman").status_code == 403
        headers = {"X-Apim-Tenant-Key": "synthetic-tenant"}
        result = http.get(path + "?format=postman", headers=headers)
        assert result.status_code == 200
        assert all(secret not in result.text for secret in ["synthetic-admin", "synthetic-tenant", "synthetic-named"])
        assert http.get(path, headers=headers).json()["openapi"] == "3.0.3"
        assert http.get(path + "?format=unknown", headers=headers).status_code == 400
        assert http.get("/apim/management/apis/missing/export?format=postman", headers=headers).status_code == 404


@pytest.mark.parametrize("format", ["postman", "openapi"])
@pytest.mark.contract("EXPORT-POSTMAN")
def test_export_scoped_readers_cannot_export_other_apis(monkeypatch, format):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    cfg = GatewayConfig(
        control_plane=ControlPlaneConfig(
            enabled=True, allow_legacy_tenant_keys=False, workspace_apis={"team": ["widgets"]}
        ),
        apis={"widgets": api(), "other": api()},
    )
    with client(cfg) as http:
        for grant in [{"role": "reader", "api_ids": ["widgets"]}, {"role": "reader", "workspace_id": "team"}]:
            headers = {"Authorization": "Bearer " + token(apim_grants=[grant])}
            assert http.get(f"/apim/management/apis/widgets/export?format={format}", headers=headers).status_code == 200
            assert http.get(f"/apim/management/apis/other/export?format={format}", headers=headers).status_code == 403
            assert http.get(f"/apim/management/apis/unknown/export?format={format}", headers=headers).status_code == 403
        assert http.get(f"/apim/management/apis/widgets/export?format={format}").status_code == 401


def test_export_omits_body_examples_and_credential_parameter_defaults():
    value = api()
    operation = value.operations["save"]
    operation.request.representations[0].examples[0].value = {"private_key": "unclassified-secret-example"}
    operation.request.query_parameters.append(
        OperationParameterConfig(
            **{"name": "access_token", "type": "string", "required": True, "default_value": "unclassified-query-token"}
        )
    )
    # Validate assigned metadata through the model, as config loading does.
    value = ApiConfig.model_validate(value.model_dump())
    encoded = json.dumps(build_postman(value))
    assert "unclassified-secret-example" not in encoded
    assert "unclassified-query-token" not in encoded


def test_form_fields_and_content_length_use_local_client_values():
    value = api()
    operation = value.operations["save"]
    operation.request = OperationRequestMetadataConfig.model_validate(
        {
            "headers": [{"name": "Content-Length", "required": False, "type": "integer", "default_value": "999"}],
            "representations": [
                {
                    "content_type": "multipart/form-data",
                    "form_parameters": [{"name": "file", "required": True, "type": "string"}],
                }
            ],
        }
    )
    value = ApiConfig.model_validate(value.model_dump())
    request = build_postman(value)["item"][0]["request"]
    assert request["body"] == {"mode": "formdata", "formdata": [{"key": "file", "value": "", "type": "text"}]}
    assert request["header"] == []


@pytest.mark.parametrize("selector,expected", [("special", "special"), ("{kind}", "{{kind}}")])
def test_query_template_discriminators_survive_parameter_metadata(selector, expected):
    value = api()
    value.operations = {
        "get": OperationConfig(
            name="Get",
            method="GET",
            url_template="/items?kind=" + selector,
            template_parameters=[{"name": "kind", "type": "string", "required": True}],
        )
    }
    request = build_postman(value)["item"][0]["request"]
    assert request["url"]["query"] == [{"key": "kind", "value": expected, "disabled": False}]
    assert request["url"]["raw"] == "{{base_url}}/widgets/items?kind=" + expected


def test_short_credentials_do_not_corrupt_collection_structure_or_public_routes():
    value = api()
    value.operations["save"].description = "test title; secret token: t"
    cfg = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="t"),
        named_values={"short": NamedValueConfig(value="i", secret=True)},
        apis={"widgets": value},
    )
    with client(cfg) as http:
        response = http.get("/apim/management/apis/widgets/export?format=postman", headers={"X-Apim-Tenant-Key": "t"})
    assert response.status_code == 200
    collection = response.json()
    assert collection["info"] == {"name": "Widgets", "schema": SCHEMA}
    assert collection["auth"]["type"] == "apikey"
    assert collection["variable"] == [
        {"key": "base_url", "value": "http://localhost:8000", "type": "string"},
        {"key": "subscription_key", "value": "", "type": "string"},
        {"key": "bearer_token", "value": "", "type": "string"},
    ]
    request = collection["item"][0]["request"]
    assert request["method"] == "POST"
    assert request["url"]["raw"] == "{{base_url}}/widgets/:id?mode=save&region=eu"
    assert request["description"] == "test title; secret token: [redacted]"
    assert {"key": "Content-Type", "value": "application/json"} in request["header"]
