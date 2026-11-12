import json

import pytest
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.control_plane import ControlPlaneConfig
from app.main import create_app

pytestmark = pytest.mark.contract("MGMT-API-ARTIFACT-PROMOTION")

XML = "<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>"
HEADERS = {"X-Apim-Tenant-Key": "tenant"}


def test_artifact_scoped_access_uses_decoded_api_id(monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    api = ApiConfig(name="Orders", path="orders", upstream_base_url="http://backend")
    cfg = GatewayConfig(
        control_plane=ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False),
        apis={"orders#private": api, "orders": api.model_copy(deep=True)},
    )

    def scoped(role):
        grant = {"role": role, "api_ids": ["orders#private"]}
        return {"Authorization": "Bearer " + token(apim_grants=[grant])}

    with TestClient(create_app(config=cfg)) as client:
        own = "/apim/management/apis/orders%23private/artifacts"
        other = "/apim/management/apis/orders/artifacts"
        reader = scoped("reader")
        contributor = scoped("contributor")
        extracted = client.get(own, headers=reader)
        assert extracted.status_code == 200
        body = {"artifact": extracted.json()}
        plan = client.post(own, headers=reader, json=body)
        assert plan.status_code == 200
        etag = plan.json()["etag"]
        assert client.put(own, headers={**reader, "If-Match": etag}, json=body).status_code == 403
        for headers in (reader, contributor):
            assert client.get(other, headers=headers).status_code == 403
            assert client.post(other, headers=headers, json=body).status_code == 403
            assert client.put(other, headers={**headers, "If-Match": etag}, json=body).status_code == 403
        assert client.put(own, headers={**contributor, "If-Match": etag}, json=body).status_code == 200


def test_artifact_plan_and_stale_apply_preserve_disk(monkeypatch, tmp_path):
    api = ApiConfig(
        name="Orders",
        path="orders",
        upstream_base_url="http://backend",
        policies_xml=XML,
        operations={"item": OperationConfig(name="Item", url_template="/item", policies_xml=XML)},
    )
    cfg = GatewayConfig(tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis={"orders": api})
    config_path = tmp_path / "apim.json"
    config_path.write_text(json.dumps(cfg.model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    with TestClient(create_app(config=cfg)) as client:
        url = "/apim/management/apis/orders/artifacts"
        body = {"artifact": client.get(url, headers=HEADERS).json()}
        before = config_path.read_bytes()
        plan = client.post(url, headers=HEADERS, json=body)
        assert plan.status_code == 200
        assert config_path.read_bytes() == before
        edited = "<policies><inbound><set-header name='x-edit'><value>new</value></set-header></inbound></policies>"
        response = client.put("/apim/management/policies/operation/orders:item", headers=HEADERS, json={"xml": edited})
        assert response.status_code == 200
        saved = config_path.read_bytes()
        assert json.loads(saved)["apis"]["orders"]["operations"]["item"]["policies_xml"] == edited
        stale = client.put(url, headers={**HEADERS, "If-Match": plan.json()["etag"]}, json=body)
        assert stale.status_code == 412
        assert config_path.read_bytes() == saved
        assert client.get(url, headers=HEADERS).json()["operation_policies"]["item"] == edited


def test_artifact_promotion_and_preconditions():
    api = ApiConfig(
        name="Example",
        path="example",
        upstream_base_url="http://source",
        policies_xml=XML,
        operations={
            "GetThing": OperationConfig(name="Get thing", method="GET", url_template="/things", policies_xml=XML)
        },
    )
    cfg = GatewayConfig(
        allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis={"source": api}
    )
    with TestClient(create_app(config=cfg)) as client:
        source = client.get("/apim/management/apis/source/artifacts", headers=HEADERS)
        assert source.status_code == 200
        artifact = source.json()
        assert artifact["openapi"]["servers"] == [{"url": "/example"}]
        assert artifact["operation_policies"] == {"GetThing": XML}
        body = {"artifact": artifact, "upstream_base_url": "http://target"}
        url = "/apim/management/apis/target/artifacts"
        plan = client.post(url, json=body, headers=HEADERS)
        assert plan.status_code == 200, plan.text
        assert client.put(url, json=body, headers=HEADERS).status_code == 428
        assert client.put(url, json=body, headers={**HEADERS, "If-Match": '"stale"'}).status_code == 412
        applied = client.put(url, json=body, headers={**HEADERS, "If-Match": plan.json()["etag"]})
        assert applied.status_code == 200, applied.text
        assert applied.json() == artifact
        assert client.put(url, json=body, headers={**HEADERS, "If-Match": plan.json()["etag"]}).status_code == 412
        artifact["api_policy"] = None
        artifact["operation_policies"] = {}
        body = {"artifact": artifact}
        plan = client.post(url, json=body, headers=HEADERS)
        assert plan.status_code == 200, plan.text
        result = client.put(url, json=body, headers={**HEADERS, "If-Match": plan.json()["etag"]})
        assert result.status_code == 200, result.text
        assert result.json()["operation_policies"] == {}
        assert client.app.state.gateway_config.apis["target"].upstream_base_url == "http://target"


@pytest.mark.parametrize(
    ("info", "message"),
    [({"title": "Example"}, "Invalid policies XML"), ({"title": 123}, "info.title")],
)
def test_invalid_artifact_preserves_target(info, message):
    cfg = GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"))
    with TestClient(create_app(config=cfg)) as client:
        body = {
            "artifact": {
                "schema_version": 1,
                "openapi": {"openapi": "3.0.3", "info": info, "servers": [{"url": "/x"}], "paths": {}},
                "api_policy": "<bad>",
                "operation_policies": {},
            },
            "upstream_base_url": "http://target",
        }
        response = client.post("/apim/management/apis/x/artifacts", headers=HEADERS, json=body)
        assert response.status_code == 400
        assert message in response.json()["detail"]
        assert "x" not in client.app.state.gateway_config.apis


def test_root_path_and_invalid_upstream():
    api = ApiConfig(
        name="Root",
        path="",
        upstream_base_url="http://source",
        operations={"op": OperationConfig(name="Op", method="GET", url_template="/item")},
    )
    cfg = GatewayConfig(
        allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis={"root": api}
    )
    with TestClient(create_app(config=cfg)) as client:
        artifact = client.get("/apim/management/apis/root/artifacts", headers=HEADERS).json()
        url = "/apim/management/apis/new/artifacts"
        for upstream in ["", "ftp://example", "http://user:pass@example", "http://example?x=1", "http://example#x"]:
            response = client.post(url, headers=HEADERS, json={"artifact": artifact, "upstream_base_url": upstream})
            assert response.status_code == 400
        body = {"artifact": artifact, "upstream_base_url": "http://example/base"}
        plan = client.post(url, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        result = client.put(url, headers={**HEADERS, "If-Match": plan.json()["etag"]}, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["openapi"]["servers"] == [{"url": "/"}]
        assert client.app.state.gateway_config.apis["new"].path == ""


def test_graphql_artifacts_are_rejected():
    from app.config import GraphQLApiConfig

    api = ApiConfig(
        name="Graph",
        path="graph",
        upstream_base_url="http://source",
        graphql=GraphQLApiConfig(schema_document="type Query { hello: String }"),
    )
    cfg = GatewayConfig(
        allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis={"graph": api}
    )
    with TestClient(create_app(config=cfg)) as client:
        response = client.get("/apim/management/apis/graph/artifacts", headers=HEADERS)
        assert response.status_code == 400
        assert "GraphQL" in response.json()["detail"]
        artifact = {
            "schema_version": 1,
            "openapi": {"openapi": "3.0.3", "servers": [{"url": "/graph"}], "paths": {}},
            "api_policy": None,
            "operation_policies": {},
        }
        response = client.post("/apim/management/apis/graph/artifacts", headers=HEADERS, json={"artifact": artifact})
        assert response.status_code == 400
        assert "GraphQL" in response.json()["detail"]


def test_existing_query_mode_retains_required_query_without_template():
    from app.config import OperationParameterConfig, OperationRequestMetadataConfig

    operation = OperationConfig(
        name="Find",
        method="GET",
        url_template="/find",
        request=OperationRequestMetadataConfig(
            query_parameters=[OperationParameterConfig(name="q", required=True, type="string")]
        ),
    )
    api = ApiConfig(
        name="Query",
        path="query",
        upstream_base_url="http://source",
        translate_required_query_parameters="query",
        operations={"FindThing": operation},
    )
    cfg = GatewayConfig(
        allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis={"query": api}
    )
    with TestClient(create_app(config=cfg)) as client:
        url = "/apim/management/apis/query/artifacts"
        artifact = client.get(url, headers=HEADERS).json()
        body = {"artifact": artifact}
        plan = client.post(url, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        response = client.put(url, headers={**HEADERS, "If-Match": plan.json()["etag"]}, json=body)
        assert response.status_code == 200, response.text
        updated = client.app.state.gateway_config.apis["query"]
        assert updated.translate_required_query_parameters == "query"
        assert updated.operations["FindThing"].url_template == "/find"
        assert updated.operations["FindThing"].request.query_parameters[0].required
        normalized = response.json()
        next_plan = client.post(url, headers=HEADERS, json={"artifact": normalized})
        assert next_plan.status_code == 200
        assert next_plan.json()["diff"] == ""


def test_export_rejects_known_secret_literal_and_unsafe_operation_order():
    from app.config import NamedValueConfig

    operations = {
        "first": OperationConfig(name="First", method="GET", url_template="/a"),
        "middle": OperationConfig(name="Middle", method="GET", url_template="/b"),
        "last": OperationConfig(name="Last", method="POST", url_template="/a"),
    }
    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        named_values={"secret": NamedValueConfig(value="known-secret-value", secret=True)},
        apis={
            "unsafe": ApiConfig(name="Unsafe", path="unsafe", upstream_base_url="http://source", operations=operations),
            "secret": ApiConfig(name="known-secret-value", path="secret", upstream_base_url="http://source"),
        },
    )
    with TestClient(create_app(config=cfg)) as client:
        order = client.get("/apim/management/apis/unsafe/artifacts", headers=HEADERS)
        assert order.status_code == 400 and "order" in order.json()["detail"]
        secret = client.get("/apim/management/apis/secret/artifacts", headers=HEADERS)
        assert secret.status_code == 400 and "known secret" in secret.json()["detail"]
        assert "known-secret-value" not in secret.text


def test_export_rejects_unportable_query_and_operation_authz():
    from app.config import RouteAuthzConfig

    apis = {
        "fixed": ApiConfig(
            name="Fixed",
            path="fixed",
            upstream_base_url="http://source",
            operations={"item": OperationConfig(name="Item", url_template="/item?kind=book")},
        ),
        "auth": ApiConfig(
            name="Auth",
            path="auth",
            upstream_base_url="http://source",
            operations={
                "item": OperationConfig(
                    name="Item", url_template="/item", authz=RouteAuthzConfig(required_scopes=["read"])
                )
            },
        ),
    }
    cfg = GatewayConfig(
        allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis=apis
    )
    with TestClient(create_app(config=cfg)) as client:
        for api_id, message in [("fixed", "query constraints"), ("auth", "authorization")]:
            response = client.get(f"/apim/management/apis/{api_id}/artifacts", headers=HEADERS)
            assert response.status_code == 400
            assert message in response.json()["detail"]


def test_query_mode_promotes_to_new_api():
    from app.config import OperationParameterConfig, OperationRequestMetadataConfig

    operation = OperationConfig(
        name="Find",
        url_template="/find",
        request=OperationRequestMetadataConfig(
            query_parameters=[OperationParameterConfig(name="q", required=True, type="string")]
        ),
    )
    api = ApiConfig(
        name="Query",
        path="query",
        upstream_base_url="http://source",
        translate_required_query_parameters="query",
        operations={"find": operation},
    )
    cfg = GatewayConfig(
        allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis={"source": api}
    )
    with TestClient(create_app(config=cfg)) as client:
        artifact = client.get("/apim/management/apis/source/artifacts", headers=HEADERS).json()
        body = {"artifact": artifact, "upstream_base_url": "http://target"}
        url = "/apim/management/apis/target/artifacts"
        plan = client.post(url, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        result = client.put(url, headers={**HEADERS, "If-Match": plan.json()["etag"]}, json=body)
        assert result.status_code == 200, result.text
        target = client.app.state.gateway_config.apis["target"]
        assert target.translate_required_query_parameters == "query"
        assert target.operations["find"].url_template == "/find"


def test_operation_order_change_is_visible_and_invalidates_etag():
    operations = {
        "first": OperationConfig(name="First", url_template="/first"),
        "second": OperationConfig(name="Second", url_template="/second"),
    }
    api = ApiConfig(name="Order", path="order", upstream_base_url="http://source", operations=operations)
    cfg = GatewayConfig(
        allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"), apis={"order": api}
    )
    with TestClient(create_app(config=cfg)) as client:
        url = "/apim/management/apis/order/artifacts"
        artifact = client.get(url, headers=HEADERS).json()
        paths = artifact["openapi"]["paths"]
        artifact["openapi"]["paths"] = dict(reversed(list(paths.items())))
        body = {"artifact": artifact}
        plan = client.post(url, headers=HEADERS, json=body)
        assert plan.status_code == 200, plan.text
        assert "/first" in plan.json()["diff"] and "/second" in plan.json()["diff"]
        result = client.put(url, headers={**HEADERS, "If-Match": plan.json()["etag"]}, json=body)
        assert result.status_code == 200, result.text
        assert result.headers["etag"] != plan.json()["etag"]
        assert list(client.app.state.gateway_config.apis["order"].operations) == ["second", "first"]
