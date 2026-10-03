from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from test_control_plane_identity import KEY, token

from app.config import ApiConfig, GatewayConfig, OperationConfig, SubscriptionConfig
from app.control_plane import ControlPlaneConfig
from app.main import create_app

DEBUG = "/apim/management/gateways/managed/listDebugCredentials"
TRACE = "/apim/management/gateways/managed/listTrace"


def headers(**claims):
    return {"Authorization": "Bearer " + token(**claims)}


def debug_client(monkeypatch):
    monkeypatch.setenv("APIM_CONTROL_PLANE_SIGNING_KEY", KEY)
    cfg = GatewayConfig(
        allow_anonymous=True,
        trace_enabled=True,
        subscription=SubscriptionConfig(required=False),
        control_plane=ControlPlaneConfig(
            enabled=True, allow_legacy_tenant_keys=False, workspace_apis={"team-a": ["first"]}
        ),
        apis={
            name: ApiConfig(
                name=name,
                path=name,
                upstream_base_url="https://backend.example.test",
                operations={"echo": OperationConfig(name="Echo", method="POST", url_template="/echo")},
            )
            for name in ("first", "second")
        },
    )

    async def echo(request):
        return httpx.Response(200, content=await request.aread(), headers={"Content-Type": "application/json"})

    return TestClient(create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(echo))))


@pytest.mark.parametrize("scope", ["api", "workspace"])
def test_scoped_operator_mints_and_reads_only_owned_api_traces(monkeypatch, scope):
    grant = {"role": "operator", **({"api_ids": ["first"]} if scope == "api" else {"workspace_id": "team-a"})}
    scoped = headers(apim_grants=[grant])
    global_operator = headers(roles=["operator"])
    with debug_client(monkeypatch) as client:
        minted = client.post(DEBUG, headers=scoped, json={"apiId": "service/local/apis/first;rev=1"})
        assert minted.status_code == 200
        assert client.post(DEBUG, headers=scoped, json={"apiId": "second"}).status_code == 403
        assert client.post(DEBUG, headers=scoped, json={"apiId": "unknown"}).status_code == 403
        assert client.post(DEBUG, headers=global_operator, json={"apiId": "unknown"}).status_code == 404
        traced = client.post(
            "/first/echo", json={"message": "preserved"}, headers={"Apim-Debug-Authorization": minted.json()["token"]}
        )
        assert traced.status_code == 200 and traced.json() == {"message": "preserved"}
        own_id = traced.headers["Apim-Trace-Id"]
        own = client.post(TRACE, headers=scoped, json={"traceId": own_id})
        assert own.status_code == 200 and own.json()["api_id"] == "first"
        assert client.get("/apim/trace/" + own_id, headers=scoped).status_code == 200
        other_token = client.post(DEBUG, headers=global_operator, json={"apiId": "second"}).json()["token"]
        other = client.post(
            "/second/echo",
            json={"message": "other-api-private-body"},
            headers={"Apim-Debug-Authorization": other_token},
        )
        other_id = other.headers["Apim-Trace-Id"]
        # Policy variables cannot override the server-created ownership field.
        client.app.state.trace_store[other_id]["variables"] = {"api_id": "first"}
        denied = client.post(TRACE, headers=scoped, json={"traceId": other_id})
        assert denied.status_code == 403 and "other-api-private-body" not in denied.text
        assert client.get("/apim/trace/" + other_id, headers=scoped).status_code == 403
        assert client.post(TRACE, headers=global_operator, json={"traceId": other_id}).status_code == 200


def test_unknown_or_unscoped_trace_ids_still_require_authorization(monkeypatch):
    scoped = headers(apim_grants=[{"role": "operator", "api_ids": ["first"]}])
    with debug_client(monkeypatch) as client:
        for supplied, status in [
            ({}, 401),
            (scoped, 403),
            (headers(roles=["reader"]), 403),
            (headers(roles=["operator"]), 404),
        ]:
            assert client.post(TRACE, headers=supplied, json={"traceId": "unknown"}).status_code == status
            assert client.get("/apim/trace/unknown", headers=supplied).status_code == status
        client.app.state.trace_store["legacy-unscoped"] = {"status": 200, "variables": {"api_id": "first"}}
        assert client.post(TRACE, headers=scoped, json={"traceId": "legacy-unscoped"}).status_code == 403
        assert client.get("/apim/trace/legacy-unscoped", headers=scoped).status_code == 403


def test_secret_redaction_cannot_change_trace_authorization_ownership(monkeypatch):
    from app.config import NamedValueConfig

    with debug_client(monkeypatch) as client:
        cfg = client.app.state.gateway_config
        cfg.named_values["synthetic-collision"] = NamedValueConfig(value="second", secret=True)
        global_operator = headers(roles=["operator"])
        debug = client.post(DEBUG, headers=global_operator, json={"apiId": "second"}).json()["token"]
        response = client.post(
            "/second/echo", json={"message": "ordinary body"}, headers={"Apim-Debug-Authorization": debug}
        )
        assert response.status_code == 200
        trace_id = response.headers["Apim-Trace-Id"]
        assert client.app.state.trace_store[trace_id]["api_id"] == "second"
        own = headers(apim_grants=[{"role": "operator", "api_ids": ["second"]}])
        other = headers(apim_grants=[{"role": "operator", "api_ids": ["first"]}])
        assert client.post(TRACE, headers=own, json={"traceId": trace_id}).status_code == 200
        assert client.get("/apim/trace/" + trace_id, headers=own).status_code == 200
        assert client.post(TRACE, headers=other, json={"traceId": trace_id}).status_code == 403
        assert client.get("/apim/trace/" + trace_id, headers=other).status_code == 403
