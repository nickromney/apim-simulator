from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType

import httpx
import jwt
import pytest
from fastapi import FastAPI

from app.config import GatewayConfig
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
BFF_DIR = ROOT / "examples" / "bff"
JWT_SECRET = "local-bff-demo-signing-key-32bytes!!"
JWT_ISSUER = "http://bff-demo.local"
SERVICE_KEY = "bff-internal-demo-key"


def load_bff_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bff_example_main", BFF_DIR / "main.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load BFF example module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def token(audience: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": "test-user", "iss": JWT_ISSUER, "aud": audience, "iat": now, "exp": now + 300},
        JWT_SECRET,
        algorithm="HS256",
    )


def read_gateway_config(name: str) -> GatewayConfig:
    # Load the shipped JSON as the source of truth so these tests exercise the
    # real APIM example policies and routes.
    data = json.loads((BFF_DIR / name).read_text(encoding="utf-8"))
    return GatewayConfig.model_validate(data)


async def make_gateway_client(
    config_name: str,
    *,
    web_bff: FastAPI | None = None,
    mobile_bff: FastAPI | None = None,
    backend: FastAPI | None = None,
) -> tuple[FastAPI, httpx.AsyncClient, list[httpx.AsyncClient]]:
    apps = {"web-bff": web_bff, "mobile-bff": mobile_bff, "shared-backend": backend}
    clients: list[httpx.AsyncClient] = []
    app_clients: dict[str, httpx.AsyncClient] = {}
    for hostname, asgi_app in apps.items():
        if asgi_app is not None:
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi_app), base_url=f"http://{hostname}:8000")
            clients.append(client)
            app_clients[hostname] = client

    async def dispatch(request: httpx.Request) -> httpx.Response:
        client = app_clients.get(request.url.host)
        if client is None:
            return httpx.Response(502, request=request)
        return await client.request(
            request.method,
            str(request.url),
            headers=request.headers,
            content=await request.aread(),
        )

    gateway_http = httpx.AsyncClient(transport=httpx.MockTransport(dispatch), base_url="http://gateway")
    clients.append(gateway_http)
    gateway = create_app(config=read_gateway_config(config_name), http_client=gateway_http)
    return gateway, httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url="http://gateway"), clients


@pytest.mark.parametrize("domain_hop", ["direct", "series"])
def test_gateway_authenticates_and_shapes_web_and_mobile_catalogs(domain_hop: str) -> None:
    async def scenario() -> None:
        module = load_bff_module()
        backend = module.create_backend_app()
        backend_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=backend), base_url="http://shared-backend:8000"
        )
        domain_client = backend_client
        domain_base_url = "http://shared-backend:8000/api"
        internal_gateway: FastAPI | None = None
        internal_client: httpx.AsyncClient | None = None
        if domain_hop == "series":
            internal_gateway = create_app(config=read_gateway_config("apim.internal.json"), http_client=backend_client)
            internal_client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=internal_gateway), base_url="http://internal-apim:8000"
            )
            domain_client = internal_client
            domain_base_url = "http://internal-apim:8000/domain"
        web = module.create_bff_app(client=domain_client, frontend="web", domain_base_url=domain_base_url)
        mobile = module.create_bff_app(client=domain_client, frontend="mobile", domain_base_url=domain_base_url)
        gateway, client, clients = await make_gateway_client("apim.json", web_bff=web, mobile_bff=mobile)
        try:
            if internal_gateway is not None:
                async with internal_gateway.router.lifespan_context(internal_gateway):
                    async with gateway.router.lifespan_context(gateway):
                        await assert_public_bff_paths(client)
            else:
                async with gateway.router.lifespan_context(gateway):
                    await assert_public_bff_paths(client)
        finally:
            await client.aclose()
            for open_client in clients:
                await open_client.aclose()
            if internal_client is not None:
                await internal_client.aclose()
            await backend_client.aclose()

    asyncio.run(scenario())


async def assert_public_bff_paths(client: httpx.AsyncClient) -> None:
    web_response = await client.get("/web/catalog", headers={"Authorization": f"Bearer {token('bff-web')}"})
    mobile_response = await client.get("/mobile/catalog", headers={"Authorization": f"Bearer {token('bff-mobile')}"})
    assert web_response.status_code == mobile_response.status_code == 200
    web_item = web_response.json()[0]
    mobile_item = mobile_response.json()[0]
    assert set(web_item) == {"id", "name", "description", "price"}
    assert set(mobile_item) == {"id", "name"}
    assert mobile_item == {key: web_item[key] for key in ("id", "name")}
    missing = await client.get("/web/catalog")
    cross_audience = await client.get("/web/catalog", headers={"Authorization": f"Bearer {token('bff-mobile')}"})
    reverse_audience = await client.get("/mobile/catalog", headers={"Authorization": f"Bearer {token('bff-web')}"})
    assert missing.status_code == 401
    assert cross_audience.status_code == 401
    assert reverse_audience.status_code == 401


def test_internal_domain_route_uses_shipped_config_and_requires_service_key() -> None:
    async def scenario() -> None:
        module = load_bff_module()
        backend = module.create_backend_app()
        gateway, client, clients = await make_gateway_client("apim.internal.json", backend=backend)
        try:
            async with gateway.router.lifespan_context(gateway):
                rejected = await client.get("/domain/catalog")
                allowed = await client.get("/domain/catalog", headers={"X-Service-Key": SERVICE_KEY})
                assert rejected.status_code == 401
                assert allowed.status_code == 200
                assert {"id", "name", "description", "price"} <= set(allowed.json()[0])
        finally:
            await client.aclose()
            for open_client in clients:
                await open_client.aclose()

    asyncio.run(scenario())


def test_bff_maps_domain_service_error_to_502() -> None:
    async def scenario() -> None:
        module = load_bff_module()

        fail_transport = False

        async def unavailable(request: httpx.Request) -> httpx.Response:
            if fail_transport:
                raise httpx.ConnectError("domain is unavailable", request=request)
            return httpx.Response(503, json={"detail": "unavailable"})

        domain_client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
        bff = module.create_bff_app(client=domain_client, frontend="web", domain_base_url="http://domain/api")
        bff_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=bff), base_url="http://bff")
        try:
            response = await bff_client.get("/api/catalog")
            assert response.status_code == 502
            assert response.json() == {"detail": "Catalog service returned an error"}
            fail_transport = True
            transport_error = await bff_client.get("/api/catalog")
            assert transport_error.status_code == 502
            assert transport_error.json() == {"detail": "Catalog service is unavailable"}
        finally:
            await bff_client.aclose()
            await domain_client.aclose()

    asyncio.run(scenario())
