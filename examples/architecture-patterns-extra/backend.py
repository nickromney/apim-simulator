from __future__ import annotations

import os

import httpx
from fastapi import FastAPI, HTTPException


def create_legacy_app() -> FastAPI:
    app = FastAPI(title="Legacy order API")

    @app.get("/api/orders/{order_id}")
    def get_legacy_order(order_id: str) -> dict[str, object]:
        return {
            "legacy_order_no": order_id,
            "accountCode": "acct-17",
            "totalCents": 1250,
            "statusLabel": "ALLOCATED",
            "legacy_internal_flag": True,
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_adapter_app(client: httpx.AsyncClient | None = None) -> FastAPI:
    app = FastAPI(title="Order domain adapter")
    app.state.client = client

    @app.get("/api/orders/{order_id}")
    async def get_order(order_id: str) -> dict[str, object]:
        http_client = app.state.client
        owns_client = http_client is None
        if owns_client:
            http_client = httpx.AsyncClient()
        try:
            response = await http_client.get(
                f"{os.getenv('LEGACY_ORDERS_URL', 'http://legacy-orders:8000')}/api/orders/{order_id}"
            )
            response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Legacy order service unavailable") from exc
        finally:
            if owns_client:
                await http_client.aclose()

        try:
            legacy = response.json()
            if not isinstance(legacy, dict):
                raise TypeError("legacy order payload must be an object")
            return {
                "order_id": str(legacy["legacy_order_no"]),
                "account_id": str(legacy["accountCode"]),
                "total": {"amount": legacy["totalCents"] / 100, "currency": "GBP"},
                "status": str(legacy["statusLabel"]).lower(),
            }
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=502, detail="Legacy order service returned invalid order data") from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_stamp_app(stamp_name: str | None = None) -> FastAPI:
    app = FastAPI(title="Tenant deployment stamp")
    selected_stamp = stamp_name or os.getenv("PATTERN_STAMP_NAME", "stamp-unknown")

    @app.get("/api/catalog")
    def catalog(fail: bool = False) -> dict[str, str]:
        if fail:
            raise HTTPException(status_code=503, detail=f"{selected_stamp} unavailable")
        return {"tenant_stamp": selected_stamp, "catalog": "standard"}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "stamp": selected_stamp}

    return app


def app_for_role() -> FastAPI:
    role = os.getenv("PATTERN_SERVICE_ROLE", "stamp")
    if role == "legacy":
        return create_legacy_app()
    if role == "adapter":
        return create_adapter_app()
    return create_stamp_app()


app = app_for_role()
