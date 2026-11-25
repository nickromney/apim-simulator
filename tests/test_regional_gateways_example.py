"""In-process checks for examples/regional-gateways.

Two gateways load one config and differ only in their region, as Azure's
regional gateways share replicated configuration. `context.Deployment.Region`
selects the regional backend and `<retry>` fails over to the other region.
https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-deploy-multi-region
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from app.config import GatewayConfig
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "regional-gateways"


def _backend_module():
    spec = importlib.util.spec_from_file_location("regional_gateways_backend", EXAMPLE_DIR / "backend.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gateway(region: str):
    data = json.loads((EXAMPLE_DIR / "apim.json").read_text(encoding="utf-8"))
    data.setdefault("service", {})["region"] = region
    module = _backend_module()
    backends = {
        "orders-westeurope": httpx.ASGITransport(app=module.create_app("West Europe")),
        "orders-eastus": httpx.ASGITransport(app=module.create_app("East US")),
    }

    async def dispatch(request: httpx.Request) -> httpx.Response:
        return await backends[request.url.host].handle_async_request(request)

    return create_app(
        config=GatewayConfig.model_validate(data),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(dispatch)),
    )


async def _get(region: str, headers: dict[str, str] | None = None) -> httpx.Response:
    app = _gateway(region)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client,
    ):
        return await client.get("/orders/42", headers=headers or {})


@pytest.mark.parametrize("region", ["West Europe", "East US"])
def test_each_regional_gateway_uses_its_own_regional_backend(region: str) -> None:
    response = asyncio.run(_get(region))
    assert response.status_code == 200
    assert response.json() == {"order_id": "42", "backend_region": region}
    assert response.headers["x-apim-region"] == region
    assert response.headers["x-apim-attempts"] == "1"


@pytest.mark.contract("POLICY-RETRY")
def test_retry_fails_over_to_the_other_region_when_the_home_backend_is_down() -> None:
    response = asyncio.run(_get("West Europe", {"x-simulate-outage": "West Europe"}))
    assert response.status_code == 200
    assert response.json()["backend_region"] == "East US"
    assert response.headers["x-apim-region"] == "West Europe"
    assert response.headers["x-apim-attempts"] == "2"


def test_east_us_gateway_fails_over_to_west_europe() -> None:
    response = asyncio.run(_get("East US", {"x-simulate-outage": "East US"}))
    assert response.status_code == 200
    assert response.json()["backend_region"] == "West Europe"
    assert response.headers["x-apim-region"] == "East US"
