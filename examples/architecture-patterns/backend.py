from __future__ import annotations

import os
from collections import Counter
from threading import Lock

from fastapi import FastAPI, Request

app = FastAPI(title="Architecture pattern private backends")
_counts: Counter[str] = Counter()
_lock = Lock()
SERVICE = os.getenv("PATTERN_BACKEND_NAME", "private-services")


def result(name: str) -> None:
    with _lock:
        _counts[name] += 1


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog")
def catalog(request: Request) -> dict[str, object]:
    result("catalog")
    return {
        "id": "tea",
        "name": "Tea",
        "price": "8.50",
        "internal": "private",
        "backend": SERVICE,
        "gatewayTransform": request.headers.get("x-gateway-transform", ""),
    }


@app.get("/api/orders")
def orders() -> dict[str, object]:
    result("orders")
    return {"open": 3, "backend": SERVICE}


@app.get("/api/profile")
def profile() -> dict[str, object]:
    result("profile")
    return {"tier": "gold", "backend": SERVICE}


@app.get("/api/orders-fail")
def orders_fail() -> dict[str, str]:
    from fastapi import HTTPException

    with _lock:
        _counts["orders-fail"] += 1
    raise HTTPException(status_code=503, detail="demo upstream failure")


@app.get("/api/counts")
def counts() -> dict[str, int]:
    with _lock:
        return dict(_counts)


@app.get("/api/stats")
def stats() -> dict[str, int]:
    return counts()


@app.post("/api/reset")
def reset() -> dict[str, str]:
    with _lock:
        _counts.clear()
    return {"status": "reset"}
