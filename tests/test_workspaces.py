"""Workspace isolation, modelled on APIM Premium and v2 workspaces.

https://learn.microsoft.com/en-us/azure/api-management/workspaces-overview
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig, validate_policy_config
from app.control_plane import ScopedGrant, api_visible_in_collection
from app.main import create_app
from app.workspaces import control_plane_with_owned_apis

TENANT = {"X-Apim-Tenant-Key": "tenant-key"}
ORDERS_HOST = "orders-gw.gateway.localhost"
GLOBAL_POLICY = (
    '<policies><inbound><set-header name="x-scope-order" exists-action="append"><value>global</value></set-header>'
    "</inbound><backend><forward-request /></backend><outbound /></policies>"
)


def _append(value: str) -> str:
    step = f'<set-header name="x-scope-order" exists-action="append"><value>{value}</value></set-header>'
    inbound = f"<base />{step}"
    return f"<policies><inbound>{inbound}</inbound><backend><base /></backend><outbound><base /></outbound></policies>"


def _api(path: str, *, workspace: str | None = None, **extra: Any) -> dict[str, Any]:
    api: dict[str, Any] = {
        "name": path,
        "path": path,
        "upstream_base_url": "http://upstream.test",
        "operations": {"get": {"name": "get", "method": "GET", "url_template": "/"}},
        **extra,
    }
    if workspace is not None:
        api["workspace"] = workspace
    return api


def _config(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "allow_anonymous": True,
        "subscription": {"required": False},
        "tenant_access": {"enabled": True, "primary_key": "tenant-key"},
        "policies_xml": GLOBAL_POLICY,
        "workspace_gateways": {"orders-gw": {"display_name": "Orders gateway"}},
        "workspaces": {
            "orders": {
                "display_name": "Orders team",
                "gateways": ["orders-gw"],
                "policies_xml": _append("workspace"),
            },
            "payroll": {"display_name": "Payroll team"},
        },
        "apis": {
            "status": _api("status"),
            "orders": _api("orders", workspace="orders", policies_xml=_append("api")),
            "payroll": _api("payroll", workspace="payroll"),
        },
    }
    payload.update(overrides)
    return payload


def _client(payload: dict[str, Any], seen: list[httpx.Request] | None = None) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"ok": True})

    config = validate_policy_config(GatewayConfig.model_validate(payload))
    return TestClient(create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))


@pytest.mark.contract("WORKSPACE-ISOLATION")
@pytest.mark.parametrize(
    ("path", "host", "status"),
    [
        ("/status/", "localhost", 200),
        ("/status/", ORDERS_HOST, 404),
        ("/orders/", "localhost", 404),
        ("/orders/", ORDERS_HOST, 200),
        ("/orders/", f"{ORDERS_HOST}:8000", 200),
        ("/payroll/", "localhost", 200),
        ("/payroll/", ORDERS_HOST, 404),
    ],
)
def test_each_gateway_serves_only_its_workspaces(path: str, host: str, status: int) -> None:
    """A workspace's APIs answer only on its gateways; a workspace gateway serves nothing else."""
    with _client(_config()) as client:
        assert client.get(path, headers={"Host": host}).status_code == status


@pytest.mark.contract("WORKSPACE-ISOLATION")
def test_workspace_policy_runs_between_global_and_api() -> None:
    seen: list[httpx.Request] = []
    with _client(_config(), seen) as client:
        assert client.get("/orders/", headers={"Host": ORDERS_HOST}).status_code == 200
        assert client.get("/status/").status_code == 200
    orders, status = seen
    assert [item.strip() for item in orders.headers["x-scope-order"].split(",")] == ["global", "workspace", "api"]
    assert status.headers["x-scope-order"] == "global"


def test_policy_expressions_see_workspace_and_gateway() -> None:
    policy = (
        "<policies><inbound><base />"
        '<set-header name="x-workspace" exists-action="override"><value>@(context.Workspace.Name)</value></set-header>'
        '<set-header name="x-gateway" exists-action="override">'
        '<value>@(context.Deployment.Gateway.Id + "/" + context.Deployment.Gateway.IsManaged)</value></set-header>'
        "</inbound><backend><base /></backend><outbound><base /></outbound></policies>"
    )
    payload = _config()
    payload["apis"]["orders"]["policies_xml"] = policy
    payload["apis"]["status"]["policies_xml"] = policy.replace(
        "context.Workspace.Name", '(context.Workspace == null ? "none" : "set")'
    )
    seen: list[httpx.Request] = []
    with _client(payload, seen) as client:
        client.get("/orders/", headers={"Host": ORDERS_HOST})
        client.get("/status/")
    assert seen[0].headers["x-workspace"] == "Orders team"
    assert seen[0].headers["x-gateway"].lower() == "orders-gw/false"
    assert seen[1].headers["x-workspace"] == "none"
    assert seen[1].headers["x-gateway"].lower() == "local/true"


def _subscribed(scope: dict[str, Any], *, workspace: str | None) -> dict[str, Any]:
    subscription: dict[str, Any] = {
        "id": "team-key",
        "name": "team",
        "keys": {"primary": "k1", "secondary": "k2"},
        **scope,
    }
    if workspace is not None:
        subscription["workspace"] = workspace
    payload = _config()
    payload["subscription"] = {"required": True, "subscriptions": {"team-key": subscription}}
    payload["workspaces"]["orders"]["gateways"] = ["managed"]
    return payload


@pytest.mark.contract("WORKSPACE-ISOLATION")
def test_workspace_all_apis_subscription_covers_only_its_workspace() -> None:
    with _client(_subscribed({"all_apis": True}, workspace="orders")) as client:
        key = {"Ocp-Apim-Subscription-Key": "k1"}
        assert client.get("/orders/", headers=key).status_code == 200
        assert client.get("/payroll/", headers=key).status_code == 401
        assert client.get("/status/", headers=key).status_code == 401


def test_service_all_apis_subscription_does_not_reach_workspace_apis() -> None:
    with _client(_subscribed({"all_apis": True}, workspace=None)) as client:
        key = {"Ocp-Apim-Subscription-Key": "k1"}
        assert client.get("/status/", headers=key).status_code == 200
        assert client.get("/orders/", headers=key).status_code == 401


@pytest.mark.contract("WORKSPACE-ISOLATION")
@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda p: p["apis"]["status"].update(workspace="missing"), "unknown workspace missing"),
        (lambda p: p["workspaces"]["payroll"].update(gateways=["nowhere"]), "unknown gateway nowhere"),
        (
            lambda p: (
                p.update(products={"shared": {"name": "Shared"}}),
                p["apis"]["orders"].update(products=["shared"]),
            ),
            "API orders in workspace orders references product shared in the service level",
        ),
        (
            lambda p: (
                p.update(named_values={"region": {"value": "uksouth"}}),
                p["workspaces"]["orders"].update(policies_xml=_append("{{region}}")),
            ),
            "workspace orders policy in workspace orders references named value region in the service level",
        ),
        (
            lambda p: (
                p.update(backends={"payroll-be": {"url": "http://payroll.test", "workspace": "payroll"}}),
                p["apis"]["orders"].update(backend="payroll-be"),
            ),
            "references backend payroll-be in workspace payroll",
        ),
        (
            lambda p: (
                p.update(
                    policy_fragments={
                        "payroll-frag": '<set-header name="x" exists-action="override"><value>1</value></set-header>'
                    }
                ),
                p["workspaces"]["payroll"].update(policy_fragments=["payroll-frag"]),
                p["apis"]["orders"].update(
                    policies_xml='<policies><inbound><base /><include-fragment fragment-id="payroll-frag" /></inbound>'
                    "<backend><base /></backend><outbound><base /></outbound></policies>"
                ),
            ),
            "references policy fragment payroll-frag in workspace payroll",
        ),
        (
            lambda p: p["workspace_gateways"].update(managed={}),
            "reserved for the managed gateway",
        ),
    ],
)
def test_cross_workspace_references_are_rejected(change: Any, message: str) -> None:
    payload = _config()
    change(payload)
    with pytest.raises(ValueError, match="Workspace isolation") as raised:
        validate_policy_config(GatewayConfig.model_validate(payload))
    assert message in str(raised.value)


def test_same_workspace_references_are_accepted() -> None:
    payload = _config(
        products={"orders-product": {"name": "Orders", "workspace": "orders", "require_subscription": False}},
        named_values={"orders-region": {"value": "uksouth", "workspace": "orders"}},
        backends={"orders-be": {"url": "http://orders.test", "workspace": "orders"}},
        policy_fragments={
            "orders-frag": '<set-header name="x-region" exists-action="override"><value>{{orders-region}}</value></set-header>'
        },
    )
    payload["workspaces"]["orders"]["policy_fragments"] = ["orders-frag"]
    payload["apis"]["orders"].update(
        products=["orders-product"],
        backend="orders-be",
        policies_xml='<policies><inbound><base /><include-fragment fragment-id="orders-frag" /></inbound>'
        "<backend><base /></backend><outbound><base /></outbound></policies>",
    )
    seen: list[httpx.Request] = []
    with _client(payload, seen) as client:
        assert client.get("/orders/", headers={"Host": ORDERS_HOST}).status_code == 200
    assert seen[0].headers["x-region"] == "uksouth"
    assert seen[0].url.host == "orders.test"


def test_expression_chosen_backend_in_another_workspace_is_unknown() -> None:
    payload = _config(backends={"payroll-be": {"url": "http://payroll.test", "workspace": "payroll"}})
    payload["apis"]["orders"]["policies_xml"] = (
        '<policies><inbound><base /><set-backend-service backend-id="@(&quot;payroll-be&quot;)" /></inbound>'
        "<backend><base /></backend><outbound><base /></outbound></policies>"
    )
    payload["apis"]["payroll"]["policies_xml"] = payload["apis"]["orders"]["policies_xml"]
    seen: list[httpx.Request] = []
    with _client(payload, seen) as client:
        assert client.get("/orders/", headers={"Host": ORDERS_HOST}).status_code == 500
        assert client.get("/payroll/").status_code == 200
    assert [request.url.host for request in seen] == ["payroll.test"]


def test_config_without_workspaces_serializes_unchanged() -> None:
    plain = GatewayConfig.model_validate({"apis": {"status": _api("status")}})
    dumped = plain.model_dump(mode="json")
    assert "workspaces" not in dumped
    assert "workspace_gateways" not in dumped
    assert "workspace" not in dumped["apis"]["status"]
    owned = GatewayConfig.model_validate(_config()).model_dump(mode="json")
    assert owned["apis"]["orders"]["workspace"] == "orders"
    assert owned["workspaces"]["orders"]["gateways"] == ["orders-gw"]


def test_workspace_grants_cover_owned_apis() -> None:
    cfg = GatewayConfig.model_validate(_config())
    grant = ScopedGrant(role="reader", workspace_id="orders")
    control_plane = control_plane_with_owned_apis(cfg)
    assert api_visible_in_collection((grant,), "orders", control_plane)
    assert not api_visible_in_collection((grant,), "payroll", control_plane)
    assert not api_visible_in_collection((grant,), "status", control_plane)


@pytest.mark.contract("WORKSPACE-ISOLATION")
def test_management_api_manages_workspaces_end_to_end() -> None:
    payload = _config(workspaces={}, workspace_gateways={}, apis={"status": _api("status")})
    with _client(payload) as client:
        gateway = client.put(
            "/apim/management/workspace-gateways/team-gw", json={"host_names": ["team.example.test"]}, headers=TENANT
        )
        assert gateway.status_code == 200, gateway.text
        created = client.put(
            "/apim/management/workspaces/team",
            json={"display_name": "Team", "gateways": ["team-gw"]},
            headers=TENANT,
        )
        assert created.status_code == 200, created.text
        assert created.json()["policy_scope"] == {"scope_type": "workspace", "scope_name": "team"}

        api = client.put(
            "/apim/management/apis/team-api",
            json={"path": "team", "upstream_base_url": "http://upstream.test", "workspace": "team"},
            headers=TENANT,
        )
        assert api.status_code == 200, api.text
        assert api.json()["workspace"] == "team"
        operation = client.put(
            "/apim/management/apis/team-api/operations/get",
            json={"method": "GET", "url_template": "/"},
            headers=TENANT,
        )
        assert operation.status_code == 200, operation.text

        # A PUT that leaves the owner out keeps it.
        kept = client.put(
            "/apim/management/apis/team-api",
            json={"path": "team", "upstream_base_url": "http://upstream.test"},
            headers=TENANT,
        )
        assert kept.json()["workspace"] == "team"

        policy = client.put(
            "/apim/management/policies/workspace/team", json={"xml": _append("workspace")}, headers=TENANT
        )
        assert policy.status_code == 200, policy.text
        effective = client.get(
            "/apim/management/policies/api/team-api", params={"effective": True}, headers=TENANT
        ).json()["xml"]
        assert effective.index("global") < effective.index("workspace")

        assert client.get("/team/", headers={"Host": "team.example.test"}).status_code == 200
        assert client.get("/team/").status_code == 404

        shared = client.put("/apim/management/products/shared", json={"name": "Shared"}, headers=TENANT)
        assert shared.status_code == 200
        crossing = client.put("/apim/management/products/shared/apis/team-api", headers=TENANT)
        assert crossing.status_code == 400
        assert "Workspace isolation" in crossing.text

        listed = client.get("/apim/management/workspaces/team", headers=TENANT).json()
        assert listed["resources"]["apis"] == ["team-api"]
        assert client.get("/apim/management/workspace-gateways/team-gw", headers=TENANT).json()["workspaces"] == [
            "team"
        ]

        assert client.delete("/apim/management/workspace-gateways/team-gw", headers=TENANT).status_code == 409
        refused = client.delete("/apim/management/workspaces/team", headers=TENANT)
        assert refused.status_code == 409
        assert "1 apis" in refused.text
        deleted = client.delete("/apim/management/workspaces/team", params={"delete_resources": True}, headers=TENANT)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["deleted_resources"]["apis"] == ["team-api"]
        assert client.get("/apim/management/apis/team-api", headers=TENANT).status_code == 404
        assert client.delete("/apim/management/workspace-gateways/team-gw", headers=TENANT).status_code == 200


def test_reserved_workspace_id_is_rejected() -> None:
    with _client(_config()) as client:
        response = client.put("/apim/management/workspaces/managed", json={}, headers=TENANT)
    assert response.status_code == 400


def test_revision_selector_keeps_the_api_workspace() -> None:
    """A revision snapshot taken before the API joined a workspace is still served only on its gateway."""
    payload = _config()
    payload["apis"]["orders"]["revisions"] = {"2": {"revision": "2", "is_online": True, "definition": _api("orders")}}
    with _client(payload) as client:
        assert client.get("/orders;rev=2/", headers={"Host": ORDERS_HOST}).status_code == 200
        assert client.get("/orders;rev=2/").status_code == 404
