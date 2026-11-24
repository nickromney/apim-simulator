"""Management-gateway mode: a router instance in front of a serving instance."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from app.config import GatewayConfig
from app.main import _create_process_app, create_app
from app.management_gateway import (
    ManagementGatewaySettings,
    create_management_gateway_app,
)

SERVING_URL = "http://apim-serving:8000"
TENANT_HEADERS = {"X-Apim-Tenant-Key": "tenant-key"}


def _serving_config() -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "allow_anonymous": True,
            "trace_enabled": True,
            "trace_allow_unauthenticated": True,
            "tenant_access": {"enabled": True, "primary_key": "tenant-key"},
            "products": {"default": {"name": "Default", "require_subscription": False}},
            "apis": {
                "default": {
                    "name": "Default",
                    "path": "api",
                    "upstream_base_url": "http://backend.test",
                    "upstream_path_prefix": "/api",
                    "products": ["default"],
                    "operations": {"echo": {"name": "Echo", "method": "GET", "url_template": "/echo"}},
                }
            },
        }
    )


def _backend(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"served_by": "backend", "path": request.url.raw_path.decode()})


Scenario = Callable[[httpx.AsyncClient, httpx.AsyncClient, Any], Awaitable[None]]


def _run_with_serving(scenario: Scenario) -> None:
    """Router -> real serving simulator -> mock backend, all in process."""

    async def run() -> None:
        serving = create_app(
            config=_serving_config(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(_backend))
        )
        serving_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=serving), base_url=SERVING_URL)
        router = create_management_gateway_app(
            ManagementGatewaySettings(upstream=SERVING_URL), http_client=serving_client
        )
        router_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=router), base_url="http://router.test")
        async with serving.router.lifespan_context(serving), router.router.lifespan_context(router):
            async with router_client, serving_client:
                await scenario(router_client, serving_client, serving)

    asyncio.run(run())


def _run_with_upstream(handler: Callable[[httpx.Request], httpx.Response], scenario) -> None:
    """Router -> a mock upstream, for header and failure handling."""

    async def run() -> None:
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router = create_management_gateway_app(ManagementGatewaySettings(upstream=SERVING_URL), http_client=upstream)
        async with router.router.lifespan_context(router):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=router), base_url="http://router.test"
            ) as client:
                await scenario(client)

    asyncio.run(run())


def test_flag_off_means_no_router() -> None:
    assert ManagementGatewaySettings.from_env({}) is None
    assert ManagementGatewaySettings.from_env({"APIM_MANAGEMENT_GATEWAY": "false"}) is None


def test_flag_on_reads_upstream_and_name() -> None:
    settings = ManagementGatewaySettings.from_env(
        {"APIM_MANAGEMENT_GATEWAY": "true", "APIM_MANAGEMENT_GATEWAY_UPSTREAM": "http://apim-serving:8000/"}
    )
    assert settings == ManagementGatewaySettings(upstream="http://apim-serving:8000", gateway_name="self-hosted")
    named = ManagementGatewaySettings.from_env(
        {
            "APIM_MANAGEMENT_GATEWAY": "1",
            "APIM_MANAGEMENT_GATEWAY_UPSTREAM": "https://gw.test",
            "APIM_MANAGEMENT_GATEWAY_NAME": "branch-office",
        }
    )
    assert named is not None and named.gateway_name == "branch-office"


@pytest.mark.parametrize("upstream", ["", "apim-serving:8000", "ftp://gw.test", "http://gw.test/base"])
def test_flag_on_rejects_a_bad_upstream(upstream: str) -> None:
    with pytest.raises(ValueError, match="APIM_MANAGEMENT_GATEWAY_UPSTREAM"):
        ManagementGatewaySettings.from_env(
            {"APIM_MANAGEMENT_GATEWAY": "true", "APIM_MANAGEMENT_GATEWAY_UPSTREAM": upstream}
        )


def test_process_app_is_the_router_when_the_flag_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APIM_MANAGEMENT_GATEWAY", "true")
    monkeypatch.setenv("APIM_MANAGEMENT_GATEWAY_UPSTREAM", SERVING_URL)
    app = _create_process_app()
    assert app.state.management_gateway == ManagementGatewaySettings(upstream=SERVING_URL)


def test_gateway_request_is_served_by_the_serving_instance() -> None:
    async def scenario(router: httpx.AsyncClient, _serving: httpx.AsyncClient, _app: Any) -> None:
        response = await router.get("/api/echo?greeting=hi")
        assert response.status_code == 200
        assert response.json() == {"served_by": "backend", "path": "/api/echo?greeting=hi"}

    _run_with_serving(scenario)


def test_management_write_through_the_router_lands_on_the_serving_instance() -> None:
    async def scenario(router: httpx.AsyncClient, serving: httpx.AsyncClient, serving_app: Any) -> None:
        put = await router.put(
            "/apim/management/named-values/routed",
            headers=TENANT_HEADERS,
            json={"display_name": "routed", "value": "from-router"},
        )
        assert put.status_code == 200, put.text
        assert serving_app.state.gateway_config.named_values["routed"].value == "from-router"
        direct = await serving.get("/apim/management/named-values/routed", headers=TENANT_HEADERS)
        assert direct.json()["value"] == "from-router"

    _run_with_serving(scenario)


def test_trace_for_a_routed_request_is_stored_on_the_serving_instance() -> None:
    async def scenario(router: httpx.AsyncClient, serving: httpx.AsyncClient, _app: Any) -> None:
        traced = await router.get("/api/echo", headers={"x-apim-trace": "true"})
        trace_id = traced.headers["x-apim-trace-id"]
        assert (await serving.get(f"/apim/trace/{trace_id}")).status_code == 200
        assert (await router.get(f"/apim/trace/{trace_id}")).status_code == 200

    _run_with_serving(scenario)


def test_status_reports_the_serving_instance_ready() -> None:
    async def scenario(router: httpx.AsyncClient, _serving: httpx.AsyncClient, _app: Any) -> None:
        body = (await router.get("/apim/management-gateway/status")).json()
        assert body["mode"] == "management-gateway"
        assert body["serves_apis_natively"] is False
        assert body["serving_gateway"] == {"status": "ready", "http_status": 200}

    _run_with_serving(scenario)


def test_router_drops_hop_by_hop_headers_and_keeps_host() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = request.content
        seen["url"] = str(request.url)
        return httpx.Response(
            201, content=b"created", headers={"connection": "x-upstream-hop", "x-upstream-hop": "1", "x-kept": "yes"}
        )

    async def scenario(client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/items?a=1",
            content=b"payload",
            headers={"connection": "x-client-hop", "x-client-hop": "secret", "x-forwarded-for": "203.0.113.9"},
        )
        assert response.status_code == 201 and response.content == b"created"
        assert response.headers["x-kept"] == "yes"
        assert "x-upstream-hop" not in response.headers

    _run_with_upstream(handler, scenario)
    headers = seen["headers"]
    assert seen["url"] == f"{SERVING_URL}/api/items?a=1"
    assert seen["body"] == b"payload"
    assert headers["host"] == "router.test"
    assert headers["content-length"] == "7"
    assert "x-client-hop" not in headers
    assert headers["x-forwarded-for"] == "203.0.113.9, 127.0.0.1"


def test_router_answers_its_own_probes_without_the_serving_instance() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def scenario(client: httpx.AsyncClient) -> None:
        assert (await client.get("/apim/health")).json() == {"status": "healthy", "mode": "management-gateway"}
        status = (await client.get("/apim/management-gateway/status")).json()
        assert status["serving_gateway"]["status"] == "unreachable"

    _run_with_upstream(handler, scenario)


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(httpx.ConnectError("refused"), 502), (httpx.ReadTimeout("slow"), 504)],
)
def test_router_does_not_serve_management_natively(error: httpx.HTTPError, status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        error.request = request
        raise error

    async def scenario(client: httpx.AsyncClient) -> None:
        response = await client.get("/apim/management/status", headers=TENANT_HEADERS)
        assert response.status_code == status_code
        assert response.json()["statusCode"] == status_code

    _run_with_upstream(handler, scenario)
