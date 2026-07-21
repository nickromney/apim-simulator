from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx

GATEWAY_BASE_URL = os.getenv("SMOKE_AWS_BASE_URL", "http://127.0.0.1:4566")
REST_API_ID = os.getenv("SMOKE_AWS_REST_API_ID", "apimsim")
STAGE_NAME = os.getenv("SMOKE_AWS_STAGE_NAME", "local")
DEFAULT_ATTEMPTS = int(os.getenv("SMOKE_AWS_ATTEMPTS", "30"))
DEFAULT_DELAY_SECONDS = float(os.getenv("SMOKE_AWS_RETRY_DELAY_SECONDS", "1"))


def retry_call[T](
    operation: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("retry_call exhausted without executing operation")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def localstack_get(path: str) -> httpx.Response:
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{GATEWAY_BASE_URL}{path}")


def check_localstack_health() -> dict[str, Any]:
    response = localstack_get("/_localstack/health")
    require(
        response.status_code == 200,
        f"expected /_localstack/health to return 200, got {response.status_code}: {response.text}",
    )
    payload = response.json()
    services = payload.get("services") or {}
    apigateway_status = services.get("apigateway")
    require(
        apigateway_status in ("available", "running"),
        f"expected apigateway service available/running, got {apigateway_status!r}: {payload}",
    )
    return payload


def check_echo_route() -> dict[str, Any]:
    response = localstack_get(f"/restapis/{REST_API_ID}/{STAGE_NAME}/_user_request_/echo")
    require(
        response.status_code == 200,
        f"expected the echo route to return 200, got {response.status_code}: {response.text}",
    )
    payload = response.json()
    require(payload.get("ok") is True, f"unexpected echo payload from mock backend: {payload}")
    require(
        payload.get("path", "").endswith("/echo"),
        f"expected mock backend to see a path ending in /echo, got {payload.get('path')!r}",
    )
    return payload


def main() -> int:
    health = retry_call(check_localstack_health)
    echo = retry_call(check_echo_route)

    print("aws api gateway smoke passed")
    print(f"- /_localstack/health: apigateway={health['services']['apigateway']}")
    print(f"- /restapis/{REST_API_ID}/{STAGE_NAME}/_user_request_/echo: 200, backend path={echo['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
