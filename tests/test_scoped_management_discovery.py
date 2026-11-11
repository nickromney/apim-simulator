import pytest
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.control_plane import ControlPlaneConfig
from app.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    cfg = GatewayConfig(
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
        control_plane=ControlPlaneConfig(
            enabled=True, allow_legacy_tenant_keys=True, workspace_apis={"team": ["orders", "inventory"]}
        ),
        apis={
            name: ApiConfig(
                name=name,
                path=name,
                upstream_base_url="https://backend.example.test",
                operations={"get": OperationConfig(name="get", method="GET", url_template="/")},
            )
            for name in ("orders", "inventory", "payroll")
        },
    )
    with TestClient(create_app(config=cfg)) as http:
        yield http


def auth(**claims):
    return {"Authorization": "Bearer " + token(**claims)}


def ids(response):
    assert response.status_code == 200
    return {api["id"] for api in response.json()}


@pytest.mark.parametrize("role", ["reader", "operator", "contributor"])
def test_scoped_discovery_intersects_workspace_and_api_grants(client, role):
    headers = auth(apim_grants=[{"role": role, "workspace_id": "team", "api_ids": ["orders", "payroll"]}])
    response = client.get("/apim/management/apis", headers=headers)
    assert ids(response) == {"orders"}
    assert "payroll" not in response.text and "inventory" not in response.text
    assert client.get("/apim/management/apis/payroll", headers=headers).status_code == 403
    assert client.get("/apim/management/products", headers=headers).status_code == 403


def test_discovery_unions_grants_and_handles_empty_and_unknown_scope(client):
    grants = [
        {"role": "reader", "workspace_id": "team", "api_ids": ["orders", "payroll"]},
        {"role": "operator", "api_ids": ["payroll", "missing"]},
    ]
    assert ids(client.get("/apim/management/apis", headers=auth(apim_grants=grants))) == {"orders", "payroll"}
    for grant in (
        {"role": "reader", "workspace_id": "missing"},
        {"role": "reader", "api_ids": ["missing"]},
        {"role": "reader", "workspace_id": "team", "api_ids": ["payroll"]},
    ):
        assert ids(client.get("/apim/management/apis", headers=auth(apim_grants=[grant]))) == set()


def test_global_and_legacy_credentials_keep_full_inventory(client):
    for headers in (auth(roles=["reader"]), auth(roles=["contributor"]), {"X-Apim-Tenant-Key": "tenant"}):
        assert ids(client.get("/apim/management/apis", headers=headers)) == {"orders", "inventory", "payroll"}
    assert client.get("/apim/management/apis").status_code == 403
    assert client.get("/apim/management/apis", headers=auth(roles=["content-editor"])).status_code == 403
    assert client.get("/apim/management/apis", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert (
        client.get(
            "/apim/management/apis", headers={"Authorization": "Bearer invalid", "X-Apim-Tenant-Key": "tenant"}
        ).status_code
        == 401
    )


def test_reader_and_contributor_policy_and_operation_permissions(client):
    reader = auth(apim_grants=[{"role": "reader", "api_ids": ["orders"]}])
    contributor = auth(apim_grants=[{"role": "contributor", "api_ids": ["orders"]}])
    policy = {"xml": "<policies><inbound><base /></inbound></policies>"}
    operation = {"name": "Created", "method": "GET", "url_template": "/created"}
    own_policy = "/apim/management/policies/api/orders"
    other_policy = "/apim/management/policies/api/payroll"
    own_operation = "/apim/management/apis/orders/operations/created"
    other_operation = "/apim/management/apis/payroll/operations/created"
    assert client.get(own_policy, headers=reader).status_code == 200
    assert client.get(other_policy, headers=reader).status_code == 403
    assert client.put(own_policy, headers=reader, json=policy).status_code == 403
    assert client.put(own_operation, headers=reader, json=operation).status_code == 403
    assert client.put(other_policy, headers=contributor, json=policy).status_code == 403
    assert client.put(other_operation, headers=contributor, json=operation).status_code == 403
    assert client.put("/apim/management/policies/global/service", headers=contributor, json=policy).status_code == 403
    assert client.put(own_policy, headers=contributor, json=policy).status_code == 200
    assert client.put(own_operation, headers=contributor, json=operation).status_code == 200
    assert client.get(own_policy, headers=reader).json()["xml"] == policy["xml"]
    assert client.get(own_operation, headers=reader).status_code == 200
    assert client.get(other_operation, headers={"X-Apim-Tenant-Key": "tenant"}).status_code == 404


def test_exact_api_ids_and_operation_policy_scope_over_asgi(monkeypatch):
    import asyncio
    from urllib.parse import quote

    import httpx

    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    monkeypatch.delenv("APIM_CONFIG_PATH", raising=False)
    other_api_ids = ("%6frders", "orders;rev=2", "orders?other", "orders#other", "orders\t")
    cfg = GatewayConfig(
        control_plane=ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False),
        apis={
            name: ApiConfig(
                name=name,
                path="path-" + str(index),
                upstream_base_url="https://backend.example.test",
                operations={"op": OperationConfig(name="op", method="GET", url_template="/")},
            )
            for index, name in enumerate(("orders", *other_api_ids))
        },
    )
    observed_paths = []
    app = create_app(config=cfg)

    async def observe(scope, receive, send):
        if scope["type"] == "http":
            observed_paths.append(scope["path"])
        await app(scope, receive, send)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observe), base_url="http://test") as http:
            scoped = auth(apim_grants=[{"role": "contributor", "api_ids": ["orders"]}])
            global_headers = auth(roles=["contributor"])
            assert ids(await http.get("/apim/management/apis", headers=scoped)) == {"orders"}
            policy = {"xml": "<policies><inbound><base /></inbound></policies>"}
            own = "/apim/management/policies/operation/orders:op"
            assert (await http.get(own, headers=scoped)).status_code == 200
            assert (await http.put(own, headers=scoped, json=policy)).status_code == 200
            assert (await http.get(own, headers=scoped)).json()["xml"] == policy["xml"]
            for literal in other_api_ids:
                encoded = quote(literal, safe="")
                resource = "/apim/management/apis/" + encoded
                assert (await http.get(resource, headers=global_headers)).status_code == 200
                assert observed_paths[-1] == "/apim/management/apis/" + literal
                assert (await http.get(resource, headers=scoped)).status_code == 403
                for scope in ("api/" + encoded, "operation/" + encoded + ":op"):
                    path = "/apim/management/policies/" + scope
                    assert (await http.get(path, headers=global_headers)).status_code == 200
                    assert (await http.get(path, headers=scoped)).status_code == 403
                    assert (await http.put(path, headers=scoped, json=policy)).status_code == 403

    async def run():
        async with app.router.lifespan_context(app):
            await scenario()

    asyncio.run(run())


def test_cli_signed_reader_and_contributor_against_real_management(monkeypatch, tmp_path, capsys):
    import json

    import httpx

    from app.cli import main

    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    monkeypatch.delenv("APIM_TENANT_KEY", raising=False)

    def return_body(body):
        return (
            '<policies><inbound><return-response><set-status code="200" reason="OK" />'
            f"<set-body>{body}</set-body></return-response></inbound></policies>"
        )

    cfg = GatewayConfig(
        allow_anonymous=True,
        control_plane=ControlPlaneConfig(enabled=True, allow_legacy_tenant_keys=False),
        apis={
            name: ApiConfig(
                name=name,
                path=name,
                upstream_base_url="https://backend.example.test",
                policies_xml=return_body(name),
                operations={"get": OperationConfig(name="get", method="GET", url_template="/check")},
            )
            for name in ("orders", "payroll")
        },
    )
    config_path = tmp_path / "gateway.json"
    config_path.write_text(cfg.model_dump_json())
    monkeypatch.setenv("APIM_CONFIG_PATH", str(config_path))
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    reader = token(subject="reader-agent", apim_grants=[{"role": "reader", "api_ids": ["orders"]}])
    contributor = token(subject="writer-agent", apim_grants=[{"role": "contributor", "api_ids": ["orders"]}])
    policy = return_body("updated orders")
    policy_file = tmp_path / "policy.xml"
    policy_file.write_text(policy)

    def invoke(bearer, *args):
        monkeypatch.setenv("APIM_BEARER_TOKEN", bearer)
        result = main(["--base-url", "http://test", *args], transport=transport)
        return result, capsys.readouterr()

    with TestClient(app) as gateway:
        assert gateway.get("/orders/check").text == "orders"
        assert gateway.get("/payroll/check").text == "payroll"
        for bearer in (reader, contributor):
            code, output = invoke(bearer, "apis")
            assert code == 0
            assert [api["id"] for api in json.loads(output.out)] == ["orders"]
        code, output = invoke(reader, "set-policy", "api", "orders", "--file", str(policy_file))
        assert code == 1 and "403" in output.err
        code, output = invoke(contributor, "set-policy", "api", "payroll", "--file", str(policy_file))
        assert code == 1 and "403" in output.err
        assert gateway.get("/orders/check").text == "orders"
        assert gateway.get("/payroll/check").text == "payroll"
        code, output = invoke(contributor, "set-policy", "api", "orders", "--file", str(policy_file))
        assert code == 0 and json.loads(output.out)["xml"] == policy
        code, output = invoke(reader, "policy", "api", "orders")
        assert code == 0 and json.loads(output.out)["xml"] == policy
        assert gateway.get("/orders/check").text == "updated orders"
        assert gateway.get("/payroll/check").text == "payroll"

    with TestClient(create_app()) as restarted:
        assert restarted.get("/orders/check").text == "updated orders"
        assert restarted.get("/payroll/check").text == "payroll"
        assert ids(restarted.get("/apim/management/apis", headers={"Authorization": f"Bearer {reader}"})) == {"orders"}
