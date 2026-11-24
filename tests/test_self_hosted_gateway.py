from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app
from app.self_hosted_gateway import SelfHostedGatewaySettings

TENANT = {"X-Apim-Tenant-Key": "tenant-key"}
TOKEN = {"X-Apim-Gateway-Token": "gateway-token"}


def _api(name: str, gateway_ids: list[str], *, policy: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "path": name,
        "upstream_base_url": "http://backend.test",
        "gateways": gateway_ids,
        "policies_xml": policy,
        "operations": {"get": {"name": "get", "method": "GET", "url_template": "/"}},
    }


def _config(**apis: Any) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "allow_anonymous": True,
            "subscription": {"required": False},
            "tenant_access": {"enabled": True, "primary_key": "tenant-key"},
            "gateways": {"remote": {"location": {"name": "remote"}, "access_token": "gateway-token"}},
            "apis": apis,
        }
    )


def _backend_transport(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True}, request=request)


@pytest.mark.contract("GW-SELF-HOSTED")
def test_configuration_snapshot_auth_and_etag() -> None:
    config = _config(remote=_api("remote", ["remote"]))
    with TestClient(create_app(config=config)) as client:
        assert client.get("/apim/management/gateways/remote/configuration").status_code == 401
        assert (
            client.get(
                "/apim/management/gateways/remote/configuration", headers={"Authorization": "Bearer bad"}
            ).status_code
            == 403
        )
        response = client.get("/apim/management/gateways/remote/configuration", headers=TOKEN)
        assert response.status_code == 200
        assert response.headers["etag"]
        assert "gateway-token" not in response.text
        assert (
            client.get(
                "/apim/management/gateways/remote/configuration",
                headers={**TOKEN, "If-None-Match": response.headers["etag"]},
            ).status_code
            == 304
        )

    expired = _config(remote=_api("remote", ["remote"]))
    expired.gateways["remote"].access_token_expires_at = "2020-01-01T00:00:00+00:00"
    with TestClient(create_app(config=expired)) as client:
        assert client.get("/apim/management/gateways/remote/configuration", headers=TOKEN).status_code == 401


@pytest.mark.contract("GW-SELF-HOSTED")
def test_api_assignments_and_deployment_gateway_identity() -> None:
    policy = (
        '<policies><inbound><set-header name="x-gateway" exists-action="override">'
        '<value>@(context.Deployment.Gateway.Id + "/" + context.Deployment.Gateway.IsManaged)</value>'
        "</set-header><base /></inbound><backend><base /></backend><outbound><base /></outbound></policies>"
    )
    config = _config(
        managed=_api("managed", ["managed"]),
        remote=_api("remote", ["remote"], policy=policy),
    )
    seen: list[httpx.Request] = []

    def backend(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _backend_transport(request)

    with TestClient(
        create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    ) as managed:
        assert managed.get("/remote/").status_code == 404
        assert managed.get("/managed/").status_code == 200

    with TestClient(
        create_app(
            config=config,
            gateway_id="remote",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)),
        )
    ) as self_hosted:
        assert self_hosted.get("/managed/").status_code == 404
        response = self_hosted.get("/remote/")
        assert response.status_code == 200
    assert seen[-1].headers["x-gateway"].lower() == "remote/false"


@pytest.mark.contract("GW-SELF-HOSTED")
def test_self_hosted_sync_keeps_last_known_good_on_fetch_failure() -> None:
    config = _config(remote=_api("remote", ["remote"]))
    snapshot = config.model_dump(mode="json")
    calls = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/configuration"):
            calls += 1
            if calls == 1:
                return httpx.Response(200, json=snapshot, headers={"ETag": '"etag-1"'}, request=request)
            raise httpx.ConnectError("managed is down", request=request)
        return _backend_transport(request)

    settings = SelfHostedGatewaySettings(
        gateway_id="remote",
        config_url="http://managed/apim/management/gateways/remote/configuration",
        token="gateway-token",
        poll_interval=0.05,
    )
    with TestClient(
        create_app(
            config=GatewayConfig.model_validate({"allow_anonymous": True, "subscription": {"required": False}}),
            gateway_id="remote",
            self_hosted_settings=settings,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        )
    ) as client:
        assert client.get("/remote/").status_code == 200
        deadline = time.monotonic() + 2
        while client.get("/apim/self-hosted-gateway/status").json()["last_error"] is None:
            if time.monotonic() >= deadline:
                raise AssertionError("sync failure was not recorded")
            time.sleep(0.05)
        assert client.get("/remote/").status_code == 200


@pytest.mark.contract("GW-SELF-HOSTED")
def test_managed_and_self_hosted_apps_sync_over_asgi_transport() -> None:
    config = _config(remote=_api("remote", ["remote"]))
    managed_app = create_app(config=config)
    managed_transport = httpx.ASGITransport(app=managed_app)

    async def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/configuration"):
            return await managed_transport.handle_async_request(request)
        return _backend_transport(request)

    settings = SelfHostedGatewaySettings(
        gateway_id="remote",
        config_url="http://managed/apim/management/gateways/remote/configuration",
        token="gateway-token",
        poll_interval=30,
    )
    with TestClient(managed_app):
        with TestClient(
            create_app(
                config=GatewayConfig.model_validate({"allow_anonymous": True, "subscription": {"required": False}}),
                gateway_id="remote",
                self_hosted_settings=settings,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
            )
        ) as self_hosted:
            assert self_hosted.get("/remote/").status_code == 200


@pytest.mark.contract("GW-SELF-HOSTED")
def test_self_hosted_management_writes_are_refused() -> None:
    settings = SelfHostedGatewaySettings(
        gateway_id="remote",
        config_url="http://managed/configuration",
        token="gateway-token",
    )
    config = _config(remote=_api("remote", ["remote"]))
    with TestClient(
        create_app(
            config=config,
            gateway_id="remote",
            self_hosted_settings=settings,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(503, request=request))
            ),
        )
    ) as client:
        response = client.put(
            "/apim/management/apis/remote",
            headers={**TENANT, "Content-Type": "application/json"},
            json={"path": "remote", "upstream_base_url": "http://backend.test"},
        )
        assert response.status_code == 405


def test_snapshot_never_carries_management_credentials_and_records_a_heartbeat() -> None:
    """The self-hosted gateway serves traffic; it must not be able to administer the managed instance."""
    config = _config(remote=_api("remote", ["remote"]))
    config.admin_token = "admin-secret"
    with TestClient(create_app(config=config)) as client:
        response = client.get("/apim/management/gateways/remote/configuration", headers=TOKEN)
        assert response.status_code == 200
        snapshot = response.json()
        assert "tenant-key" not in response.text
        assert "admin-secret" not in response.text
        assert {"tenant_access", "control_plane", "admin_token"}.isdisjoint(snapshot)
        listed = client.get("/apim/management/gateways", headers=TENANT).json()
        assert listed[0]["id"] == "remote"
        assert listed[0]["last_heartbeat"] is not None


def test_unassigned_api_gateways_are_not_serialized() -> None:
    config = GatewayConfig.model_validate(
        {"apis": {"plain": {"name": "plain", "path": "plain", "upstream_base_url": "http://backend.test"}}}
    )
    assert config.apis["plain"].gateways is None
    assert "gateways" not in config.model_dump(mode="json")["apis"]["plain"]
