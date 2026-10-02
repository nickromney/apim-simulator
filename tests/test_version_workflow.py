from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import (
    ApiConfig,
    ApiVersionSetConfig,
    GatewayConfig,
    OperationConfig,
    PortalConfig,
    ProductConfig,
    Subscription,
    SubscriptionConfig,
    SubscriptionKeyPair,
    TenantAccessConfig,
    UserConfig,
)
from app.main import create_app
from app.management_service import ManagementService
from app.version_workflow import VersionRequest, build_version_workflow_router, create_version

TENANT = {"X-Apim-Tenant-Key": "versions-test"}
SUBSCRIPTION = {"Ocp-Apim-Subscription-Key": "version-primary"}


def _reply(body: str) -> str:
    return f'<policies><inbound><return-response><set-status code="200" reason="OK" /><set-body>{body}</set-body></return-response></inbound></policies>'


def _config() -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="versions-test"),
        portal=PortalConfig(enabled=True),
        users={"developer": UserConfig(id="developer", name="Developer")},
        products={"starter": ProductConfig(name="Starter"), "premium": ProductConfig(name="Premium")},
        subscription=SubscriptionConfig(
            subscriptions={
                "test": Subscription(
                    id="test",
                    name="Versions",
                    products=["starter", "premium"],
                    keys=SubscriptionKeyPair(primary="version-primary", secondary="version-secondary"),
                )
            },
        ),
        apis={
            "hello": ApiConfig(
                name="Hello",
                path="hello",
                upstream_base_url="http://backend",
                products=["starter"],
                policies_xml="<policies><inbound><base /></inbound></policies>",
                operations={
                    "greet": OperationConfig(
                        name="Greet", method="GET", url_template="/greet", policies_xml=_reply("original")
                    )
                },
            )
        },
    )


def _client(config: GatewayConfig) -> TestClient:
    app = create_app(config=config)
    manager = ManagementService(
        app=app,
        serialize_gateway_config=lambda cfg: json.dumps(cfg.model_dump(mode="json")),
        build_oidc_verifiers=lambda cfg: {},
    )
    app.router.routes[:0] = build_version_workflow_router(require_management_plane=lambda: manager).routes
    return TestClient(app)


@pytest.mark.parametrize(
    ("scheme", "settings", "version_url", "version_headers"),
    [
        ("Path", {}, "/hello/v2/greet", {}),
        ("Header", {"version_header_name": "X-Api-Version"}, "/hello/greet", {"X-Api-Version": "v2"}),
        ("Query", {"version_query_name": "version"}, "/hello/greet?version=v2", {}),
    ],
)
def test_create_version_preserves_original_clones_independently_and_routes_every_scheme(
    scheme, settings, version_url, version_headers, monkeypatch
) -> None:
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    with _client(_config()) as client:
        path = "/apim/management/apis/hello/versions"
        request = {
            "version_id": "hello-v2",
            "api_version": "v2",
            "version_set_id": "hello-versions",
            "versioning_scheme": scheme,
            **settings,
        }
        assert client.post(path, json=request).status_code == 403
        created = client.post(path, headers=TENANT, json=request)
        assert created.status_code == 201
        cfg = client.app.state.gateway_config
        original, version = cfg.apis["hello"], cfg.apis["hello-v2"]
        assert original.api_version is None
        assert original.version_description == "Original"
        assert original.api_version_set == version.api_version_set == "hello-versions"
        assert version.api_version == "v2"
        assert version.products == ["starter"]
        assert version.policies_xml == original.policies_xml
        assert version.operations["greet"] is not original.operations["greet"]
        assert version.revisions["1"].definition["api_version"] == "v2"
        assert original.revisions["1"].definition["api_version"] is None
        edited = client.put(
            "/apim/management/apis/hello-v2/operations/greet",
            headers=TENANT,
            json={
                "name": "Version greeting",
                "method": "GET",
                "url_template": "/greet",
                "policies_xml": _reply("version"),
            },
        )
        assert edited.status_code == 200
        assert client.get("/hello/greet", headers=SUBSCRIPTION).text == "original"
        assert client.get(version_url, headers={**SUBSCRIPTION, **version_headers}).text == "version"
        catalog = client.get("/apim/portal/catalog", headers={"X-Apim-Portal-User": "developer"}).json()
        starter = next(product for product in catalog["products"] if product["id"] == "starter")
        assert {api["id"] for api in starter["apis"]} == {"hello", "hello-v2"}


def test_version_product_selection_can_differ_without_changing_original() -> None:
    cfg = _config()
    clone = create_version(
        cfg,
        "hello",
        VersionRequest(version_id="v2", api_version="v2", version_set_id="hello-versions", products=["premium"]),
    )
    assert clone.products == ["premium"]
    assert cfg.apis["hello"].products == ["starter"]
    clone.operations["greet"].description = "Changed"
    assert cfg.apis["hello"].operations["greet"].description is None


@pytest.mark.parametrize("conflict", ["api_id", "scheme", "version", "products"])
def test_version_conflicts_do_not_modify_config(conflict) -> None:
    cfg = _config()
    body = {"version_id": "v2", "api_version": "v2", "version_set_id": "hello-versions", "versioning_scheme": "Path"}
    if conflict == "api_id":
        body["version_id"] = "hello"
    elif conflict == "scheme":
        cfg.api_version_sets["hello-versions"] = ApiVersionSetConfig(
            display_name="Existing", versioning_scheme="Header", version_header_name="X-Version"
        )
    elif conflict == "version":
        cfg.apis["existing"] = cfg.apis["hello"].model_copy(
            deep=True, update={"api_version": "v2", "api_version_set": "hello-versions"}
        )
    else:
        body["products"] = ["missing"]
    before = cfg.model_dump(mode="json")
    with pytest.raises(HTTPException) as error:
        create_version(cfg, "hello", VersionRequest(**body))
    assert error.value.status_code == (400 if conflict == "products" else 409)
    assert cfg.model_dump(mode="json") == before
