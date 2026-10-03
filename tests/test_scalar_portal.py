from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from app.config import ApiConfig, ApiVersionSetConfig, OperationConfig, PortalConfig
from app.scalar_portal import ASSET_DIR
from app.urls import http_url
from tests.test_portal import _client, _portal_config

HEADERS = {"X-Apim-Portal-User": "dev-1"}


def test_openapi_requires_portal_identity_and_visible_product():
    cfg = _portal_config()
    cfg.apis["private"] = cfg.apis["hello"].model_copy(update={"products": ["partner"]})
    cfg.apis["unpublished"] = cfg.apis["hello"].model_copy(update={"products": ["internal"]})
    with _client(cfg) as client:
        assert client.get("/apim/portal/apis/hello/openapi").status_code == 401
        for api_id in ("missing", "private", "unpublished"):
            assert client.get(f"/apim/portal/apis/{api_id}/openapi", headers=HEADERS).status_code == 404
        response = client.get("/apim/portal/apis/hello/openapi", headers=HEADERS)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["servers"] == [{"url": "/hello"}]
        assert "upstream" not in response.text
        assert "t1" not in response.text
        assert (
            client.get("/apim/portal/apis/private/openapi", headers={"X-Apim-Portal-User": "dev-2"}).status_code == 200
        )


@pytest.mark.parametrize("scheme", ["Segment", "Header", "Query"])
def test_live_contract_versions_custom_keys_and_request_body(scheme):
    cfg = _portal_config()
    cfg.subscription.required = True
    cfg.api_version_sets["versions"] = ApiVersionSetConfig(
        id="versions",
        display_name="Greeting versions",
        versioning_scheme=scheme,
        version_header_name="X-Version",
        version_query_name="api-version",
    )
    cfg.apis["hello"] = ApiConfig(
        name="Hello",
        path="hello",
        upstream_base_url=http_url("private-backend"),
        products=["starter"],
        api_version_set="versions",
        api_version="v2",
        subscription_header_names=["X-Hello-Key"],
        operations={
            "greet": OperationConfig(
                name="greet",
                method="POST",
                url_template="/greet/{name}",
                request={
                    "representations": [
                        {
                            "content_type": "application/json",
                            "examples": [{"name": "greeting", "value": {"message": "hello"}}],
                        }
                    ]
                },
            )
        },
    )
    with _client(cfg) as client:
        document = client.get("/apim/portal/apis/hello/openapi", headers=HEADERS).json()
        operation = document["paths"]["/greet/{name}"]["post"]
        assert operation["requestBody"]["content"]["application/json"]["examples"]["greeting"]["value"] == {
            "message": "hello"
        }
        assert document["components"]["securitySchemes"]["subscription_0"] == {
            "type": "apiKey",
            "in": "header",
            "name": "X-Hello-Key",
        }
        assert operation["security"] == [{"subscription_0": []}]
        if scheme == "Segment":
            assert document["servers"] == [{"url": "/hello/v2"}]
        else:
            selector = next(p for p in operation["parameters"] if p["in"] == scheme.lower())
            assert selector["schema"]["default"] == "v2"
        # No cached or copied schema: changes appear on the next fetch.
        client.app.state.gateway_config.apis["hello"].operations["new"] = OperationConfig(
            name="new",
            method="GET",
            url_template="/new",
        )
        assert "/new" in client.get("/apim/portal/apis/hello/openapi", headers=HEADERS).json()["paths"]


def test_scalar_assets_are_local_and_disabled_with_the_portal():
    with _client(_portal_config()) as client:
        page = client.get("/apim/portal/reference")
        assert page.status_code == 200
        assert "connect-src 'self'" in page.headers["content-security-policy"]
        bundle = client.get("/apim/portal/assets/scalar.js")
        manifest = json.loads((ASSET_DIR / "manifest.json").read_text())
        assert hashlib.sha256(bundle.content).hexdigest() == manifest["sha256"]
        plain = client.get("/apim/portal/assets/scalar.js", headers={"Accept-Encoding": "identity"})
        assert plain.content == bundle.content
        assert "content-encoding" not in plain.headers
        assert client.get("/apim/portal/assets/theme.js").status_code == 200
        assert client.get("/apim/portal/assets/unknown").status_code == 404
    with _client(_portal_config(portal=PortalConfig(enabled=False))) as client:
        for path in ("reference", "assets/scalar.js", "apis/hello/openapi"):
            assert client.get("/apim/portal/" + path, headers=HEADERS).status_code == 404


def test_openapi_describes_optional_keys_when_subscription_enforcement_is_off():
    with _client(_portal_config()) as client:
        document = client.get("/apim/portal/apis/hello/openapi", headers=HEADERS).json()
        assert document["paths"]["/greet"]["get"]["security"] == [{"subscription_0": []}, {}]


@pytest.mark.repo
def test_vendored_bundle_provenance_and_release_packaging():
    manifest = json.loads((ASSET_DIR / "manifest.json").read_text())
    assert hashlib.sha256(gzip.decompress((ASSET_DIR / "scalar.js.gz").read_bytes())).hexdigest() == manifest["sha256"]
    assert "Copyright (c) 2023-present Scalar" in (ASSET_DIR / "LICENSE").read_text()
    assert "COPY --chown=${APP_UID}:${APP_GID} app ./app" in (Path(__file__).parents[1] / "Dockerfile").read_text()
