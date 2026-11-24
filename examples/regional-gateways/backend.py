"""A regional order backend for the regional-gateways example.

Each container reports the region it runs in. A request carrying
`x-simulate-outage: <region>` makes the matching backend answer 503, so the
gateway's retry policy can fail over to the other region.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request


def create_app(region: str) -> FastAPI:
    app = FastAPI(title=f"Orders backend ({region})")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "region": region}

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: str, request: Request) -> dict[str, str]:
        if request.headers.get("x-simulate-outage", "").strip().lower() == region.lower():
            raise HTTPException(status_code=503, detail=f"{region} backend unavailable")
        return {"order_id": order_id, "backend_region": region}

    return app


app = create_app(os.getenv("BACKEND_REGION", "local"))
