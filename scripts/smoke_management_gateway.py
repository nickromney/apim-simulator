"""Prove the management gateway routes to the serving gateway, through published ports.

Run against `make up-management-gateway`. The management gateway (router) and
the serving gateway are both published on localhost; every check below reaches
them that way, never from inside a container.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable

import httpx

ROUTER_BASE_URL = os.getenv("SMOKE_MANAGEMENT_GATEWAY_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SERVING_BASE_URL = os.getenv("SMOKE_SERVING_GATEWAY_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
TENANT_KEY = os.getenv("SMOKE_MANAGEMENT_GATEWAY_TENANT_KEY", "local-dev-tenant-key")
ATTEMPTS = int(os.getenv("SMOKE_MANAGEMENT_GATEWAY_ATTEMPTS", "30"))
DELAY_SECONDS = float(os.getenv("SMOKE_MANAGEMENT_GATEWAY_RETRY_DELAY_SECONDS", "1"))
TENANT_HEADERS = {"X-Apim-Tenant-Key": TENANT_KEY}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def retry[T](operation: Callable[[], T]) -> T:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return operation()
        except Exception:  # noqa: BLE001 - retried until the stack is ready
            if attempt == ATTEMPTS:
                raise
            time.sleep(DELAY_SECONDS)
    raise RuntimeError("retry exhausted without running the operation")


def expect(response: httpx.Response, status_code: int, what: str) -> httpx.Response:
    require(
        response.status_code == status_code,
        f"{what}: expected {status_code}, got {response.status_code}: {response.text[:300]}",
    )
    return response


def check_router_status(client: httpx.Client) -> None:
    body = expect(client.get(f"{ROUTER_BASE_URL}/apim/management-gateway/status"), 200, "router status").json()
    require(body.get("mode") == "management-gateway", f"router is not in management-gateway mode: {body}")
    require(body.get("serves_apis_natively") is False, f"router claims to serve APIs natively: {body}")
    require(body.get("serving_gateway", {}).get("status") == "ready", f"serving gateway is not ready: {body}")
    print(f"ok  router at {ROUTER_BASE_URL} is a management gateway for '{body['gateway']}'")


def check_gateway_traffic(client: httpx.Client) -> None:
    routed = expect(client.get(f"{ROUTER_BASE_URL}/api/echo"), 200, "GET /api/echo via router")
    direct = expect(client.get(f"{SERVING_BASE_URL}/api/echo"), 200, "GET /api/echo on serving gateway")
    require(
        routed.json().get("path") == direct.json().get("path"), "router and serving gateway reached different backends"
    )
    print("ok  GET /api/echo through the router reached the serving gateway's backend")


def check_trace_recorded_on_serving(client: httpx.Client) -> None:
    routed = expect(
        client.get(f"{ROUTER_BASE_URL}/api/echo", headers={"x-apim-trace": "true"}), 200, "traced request via router"
    )
    trace_id = routed.headers.get("x-apim-trace-id")
    require(bool(trace_id), "traced request via router returned no x-apim-trace-id")
    expect(client.get(f"{SERVING_BASE_URL}/apim/trace/{trace_id}"), 200, "trace lookup on serving gateway")
    print(f"ok  trace {trace_id} for the routed request is stored on the serving gateway")


def check_management_write_lands_on_serving(client: httpx.Client) -> None:
    named_value_id = f"mgmt-gw-smoke-{uuid.uuid4().hex[:8]}"
    url = f"/apim/management/named-values/{named_value_id}"
    payload = {"display_name": named_value_id, "value": "routed-write"}
    expect(
        client.put(f"{ROUTER_BASE_URL}{url}", headers=TENANT_HEADERS, json=payload), 200, "management PUT via router"
    )
    try:
        stored = expect(client.get(f"{SERVING_BASE_URL}{url}", headers=TENANT_HEADERS), 200, "named value on serving")
        require(stored.json().get("value") == "routed-write", f"serving gateway holds a different value: {stored.text}")
        print(f"ok  management write {named_value_id} via the router is stored on the serving gateway")
    finally:
        client.delete(f"{ROUTER_BASE_URL}{url}", headers=TENANT_HEADERS)


def main() -> None:
    with httpx.Client(timeout=10.0, trust_env=False) as client:
        retry(lambda: check_router_status(client))
        check_gateway_traffic(client)
        check_trace_recorded_on_serving(client)
        check_management_write_lands_on_serving(client)
    print("management gateway smoke passed")


if __name__ == "__main__":
    main()
