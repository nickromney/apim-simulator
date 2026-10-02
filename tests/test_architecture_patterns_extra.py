from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import httpx
from fastapi import FastAPI

from app.config import GatewayConfig
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "architecture-patterns-extra"


def load_backend_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("architecture_patterns_backend", EXAMPLE_DIR / "backend.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load architecture-patterns-extra backend")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_config() -> GatewayConfig:
    data = json.loads((EXAMPLE_DIR / "apim.json").read_text(encoding="utf-8"))
    return GatewayConfig.model_validate(data)


async def make_client() -> tuple[FastAPI, httpx.AsyncClient, list[httpx.AsyncClient]]:
    module = load_backend_module()
    legacy_app = module.create_legacy_app()
    stamp_a_app = module.create_stamp_app("tenant-a-stamp")
    stamp_b_app = module.create_stamp_app("tenant-b-stamp")
    legacy_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=legacy_app), base_url="http://legacy-orders:8000")
    stamp_a_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=stamp_a_app), base_url="http://stamp-a:8000")
    stamp_b_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=stamp_b_app), base_url="http://stamp-b:8000")

    async def adapter_dispatch(request: httpx.Request) -> httpx.Response:
        return await legacy_http.request(
            request.method, str(request.url), headers=request.headers, content=await request.aread()
        )

    adapter_http = httpx.AsyncClient(
        transport=httpx.MockTransport(adapter_dispatch), base_url="http://adapter-internal"
    )
    adapter_app = module.create_adapter_app(adapter_http)
    adapter_asgi_http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=adapter_app), base_url="http://order-adapter:8000"
    )
    app_clients = {
        "order-adapter": adapter_asgi_http,
        "stamp-a": stamp_a_http,
        "stamp-b": stamp_b_http,
    }

    async def gateway_dispatch(request: httpx.Request) -> httpx.Response:
        backend = app_clients.get(request.url.host)
        if backend is None:
            return httpx.Response(502, request=request)
        return await backend.request(
            request.method, str(request.url), headers=request.headers, content=await request.aread()
        )

    gateway_http = httpx.AsyncClient(transport=httpx.MockTransport(gateway_dispatch), base_url="http://gateway")
    gateway = create_app(config=read_config(), http_client=gateway_http)
    clients = [legacy_http, stamp_a_http, stamp_b_http, adapter_http, adapter_asgi_http, gateway_http]
    return gateway, httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gateway"), clients


def test_anti_corruption_adapter_maps_legacy_order_to_domain_shape() -> None:
    async def scenario() -> None:
        gateway, client, clients = await make_client()
        try:
            async with gateway.router.lifespan_context(gateway):
                response = await client.get("/orders/42")
            assert response.status_code == 200
            assert response.json() == {
                "order_id": "42",
                "account_id": "acct-17",
                "total": {"amount": 12.5, "currency": "GBP"},
                "status": "allocated",
            }
            assert "legacy_internal_flag" not in response.json()
        finally:
            await client.aclose()
            for open_client in clients:
                await open_client.aclose()

    asyncio.run(scenario())


def test_deployment_stamp_route_and_bulkhead_isolate_tenant_pool_failure() -> None:
    async def scenario() -> None:
        gateway, client, clients = await make_client()
        try:
            async with gateway.router.lifespan_context(gateway):
                first_a = await client.get("/tenants/a/catalog")
                first_b = await client.get("/tenants/b/catalog")
                unknown_tenant = await client.get("/tenants/c/catalog")
                assert first_a.status_code == first_b.status_code == 200
                assert unknown_tenant.status_code == 404
                assert first_a.json()["tenant_stamp"] == "tenant-a-stamp"
                assert first_b.json()["tenant_stamp"] == "tenant-b-stamp"

                failed_a = await client.get("/tenants/a/catalog?fail=true")
                isolated_a = await client.get("/tenants/a/catalog")
                still_healthy_b = await client.get("/tenants/b/catalog")

            assert failed_a.status_code == 503
            assert isolated_a.status_code == 503
            assert isolated_a.json() == {"statusCode": 503, "message": "All backend pool members are unavailable"}
            assert still_healthy_b.status_code == 200
            assert still_healthy_b.json()["tenant_stamp"] == "tenant-b-stamp"
        finally:
            await client.aclose()
            for open_client in clients:
                await open_client.aclose()

    asyncio.run(scenario())


def test_adapter_maps_legacy_transport_failure_to_gateway_error() -> None:
    async def scenario() -> None:
        module = load_backend_module()

        async def unavailable(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request)

        legacy_http = httpx.AsyncClient(
            transport=httpx.MockTransport(unavailable), base_url="http://legacy-orders:8000"
        )
        adapter_app = module.create_adapter_app(legacy_http)
        adapter_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=adapter_app), base_url="http://order-adapter:8000"
        )

        async def dispatch(request: httpx.Request) -> httpx.Response:
            return await adapter_http.request(
                request.method, str(request.url), headers=request.headers, content=await request.aread()
            )

        gateway_http = httpx.AsyncClient(transport=httpx.MockTransport(dispatch), base_url="http://gateway")
        gateway = create_app(config=read_config(), http_client=gateway_http)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gateway")
        try:
            async with gateway.router.lifespan_context(gateway):
                response = await client.get("/orders/42")
            assert response.status_code == 502
            assert response.json() == {"detail": "Legacy order service unavailable"}
        finally:
            await client.aclose()
            await gateway_http.aclose()
            await adapter_http.aclose()
            await legacy_http.aclose()

    asyncio.run(scenario())


def test_adapter_maps_malformed_legacy_payload_to_gateway_error() -> None:
    async def scenario() -> None:
        module = load_backend_module()

        async def malformed(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="{not-json")

        legacy_http = httpx.AsyncClient(transport=httpx.MockTransport(malformed), base_url="http://legacy-orders:8000")
        adapter_app = module.create_adapter_app(legacy_http)
        adapter_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=adapter_app), base_url="http://order-adapter:8000"
        )

        async def dispatch(request: httpx.Request) -> httpx.Response:
            return await adapter_http.request(
                request.method, str(request.url), headers=request.headers, content=await request.aread()
            )

        gateway_http = httpx.AsyncClient(transport=httpx.MockTransport(dispatch), base_url="http://gateway")
        gateway = create_app(config=read_config(), http_client=gateway_http)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gateway")
        try:
            async with gateway.router.lifespan_context(gateway):
                response = await client.get("/orders/42")
            assert response.status_code == 502
            assert response.json() == {"detail": "Legacy order service returned invalid order data"}
        finally:
            await client.aclose()
            await gateway_http.aclose()
            await adapter_http.aclose()
            await legacy_http.aclose()

    asyncio.run(scenario())
