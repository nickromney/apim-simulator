from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException

SERVICE_KEY = "bff-internal-demo-key"
DEFAULT_DOMAIN_BASE_URL = "http://shared-backend:8000/api"
CATALOG = [
    {
        "id": "tea",
        "name": "Tea",
        "description": "Loose leaf breakfast tea",
        "price": 8.5,
    },
    {
        "id": "coffee",
        "name": "Coffee",
        "description": "Whole bean medium roast coffee",
        "price": 12.0,
    },
]


async def _fetch_catalog(client: httpx.AsyncClient | None, url: str) -> httpx.Response:
    headers = {"X-Service-Key": SERVICE_KEY}
    if client is not None:
        return await client.get(url, headers=headers)
    async with httpx.AsyncClient(timeout=5.0) as request_client:
        return await request_client.get(url, headers=headers)


def _present_catalog(response: httpx.Response, frontend: str) -> list[dict[str, Any]]:
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Catalog service returned an error")
    try:
        items = response.json()
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("catalog response must be a list of objects")
        if frontend == "mobile":
            return [{"id": item["id"], "name": item["name"]} for item in items]
        return items
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="Catalog service returned an invalid response") from exc


def create_backend_app() -> FastAPI:
    app = FastAPI(title="Shared Catalog Backend")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "shared-backend"}

    @app.get("/api/catalog")
    async def catalog() -> list[dict[str, Any]]:
        return [item.copy() for item in CATALOG]

    return app


def create_bff_app(
    client: httpx.AsyncClient | None = None,
    frontend: str | None = None,
    domain_base_url: str | None = None,
) -> FastAPI:
    selected_frontend = (frontend or os.getenv("BFF_SERVICE_ROLE", "web")).lower()
    if selected_frontend not in {"web", "mobile"}:
        raise ValueError("frontend must be 'web' or 'mobile'")

    base_url = (domain_base_url or os.getenv("DOMAIN_BASE_URL") or DEFAULT_DOMAIN_BASE_URL).rstrip("/")
    app = FastAPI(title=f"{selected_frontend.title()} Catalog BFF")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": f"{selected_frontend}-bff"}

    @app.get("/api/catalog")
    async def catalog() -> list[dict[str, Any]]:
        try:
            response = await _fetch_catalog(client, f"{base_url}/catalog")
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Catalog service is unavailable") from exc
        return _present_catalog(response, selected_frontend)

    return app


_role = os.getenv("BFF_SERVICE_ROLE", "backend").lower()
if _role == "backend":
    app = create_backend_app()
elif _role in {"web", "mobile"}:
    app = create_bff_app(frontend=_role)
else:
    raise ValueError("BFF_SERVICE_ROLE must be 'backend', 'web', or 'mobile'")
