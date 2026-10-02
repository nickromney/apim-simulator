from __future__ import annotations

import argparse
import os
import time
from uuid import uuid4

import httpx
import jwt

BASE_URL = os.getenv("APIM_BASE_URL", "http://localhost:8000")
JWT_SECRET = "local-patterns-signing-key-32bytes!"
JWT_ISSUER = "http://patterns.local"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def make_token(
    subject: str = "patterns-smoke",
    audience: str = "patterns-client",
    secret: str = JWT_SECRET,
) -> str:
    now = int(time.time())
    claims = {"sub": subject, "iss": JWT_ISSUER, "aud": audience, "iat": now, "exp": now + 300}
    return jwt.encode(claims, secret, algorithm="HS256")


def check(base_url: str) -> None:
    run_id = uuid4().hex
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
        before = client.get("/diagnostics/stats").json().get("catalog", 0)
        offload_first = client.get(
            "/offload/catalog", params={"run": run_id}, headers={"x-client-id": f"cache-smoke-{run_id}"}
        )
        after_first = client.get("/diagnostics/stats").json().get("catalog", 0)
        offloaded = client.get(
            "/offload/catalog", params={"run": run_id}, headers={"x-client-id": f"cache-smoke-{run_id}"}
        )
        after_second = client.get("/diagnostics/stats").json().get("catalog", 0)
        require(offload_first.status_code == 200 and offloaded.status_code == 200, "offloading route failed")
        require(after_first == before + 1, "first offload request should reach the backend once")
        require(after_second == after_first, "cache hit should not reach the backend")
        require(offload_first.headers.get("x-pattern") == "offloading", "outbound transform header missing")
        require(offloaded.headers.get("x-pattern") == "offloading", "cached response lost its outbound transform")
        require(
            offload_first.json().get("gatewayTransform") == "applied", "inbound header transform did not reach backend"
        )

        catalog = client.get("/route/catalog")
        require(catalog.status_code == 200 and catalog.json()["backend"] == "private-services", "catalog route failed")
        orders = client.get("/route/orders")
        require(
            orders.status_code == 200 and orders.json()["backend"] == "order-service", "orders route missed its backend"
        )

        before_denied = client.get("/diagnostics/stats").json().get("catalog", 0)
        denied = client.get("/guard/catalog")
        require(denied.status_code == 401, f"missing JWT should return 401, got {denied.status_code}")
        require(
            client.get("/diagnostics/stats").json().get("catalog", 0) == before_denied, "missing JWT reached backend"
        )
        wrong_audience = client.get(
            "/guard/catalog", headers={"Authorization": f"Bearer {make_token(audience='other')}"}
        )
        require(wrong_audience.status_code == 401, "wrong audience should be rejected")
        invalid_signature = client.get(
            "/guard/catalog",
            headers={"Authorization": f"Bearer {make_token(secret='incorrect-patterns-signing-secret-32bytes')}"},
        )
        require(invalid_signature.status_code == 401, "invalid signature should be rejected")
        token = make_token(subject=f"patterns-{run_id}")
        headers = {"Authorization": f"Bearer {token}"}
        allowed = client.get("/guard/catalog", headers=headers)
        require(allowed.status_code == 200, f"valid JWT should pass, got {allowed.status_code}: {allowed.text}")
        limited = client.get("/guard/catalog", headers=headers)
        require(limited.status_code == 200, "second allowed call should pass")
        blocked = client.get("/guard/catalog", headers=headers)
        require(blocked.status_code == 429, f"third call should be limited, got {blocked.status_code}")
        require(
            client.get("/diagnostics/stats").json().get("catalog", 0) == before_denied + 2,
            "gatekeeper forwarded a rejected request",
        )

        aggregate = client.get("/aggregate/overview", headers={"x-apim-trace": "true"})
        require(aggregate.status_code == 200, f"aggregation failed: {aggregate.status_code} {aggregate.text}")
        data = aggregate.json()
        require(data.get("orders", {}).get("open") == 3, f"orders not aggregated: {data}")
        require(data.get("profile", {}).get("tier") == "gold", f"profile not aggregated: {data}")
        trace_id = aggregate.headers.get("x-apim-trace-id")
        require(bool(trace_id), "trace-enabled gateway did not return a trace id")
        trace = client.get(f"/apim/trace/{trace_id}")
        require(trace.status_code == 200, "aggregate request trace could not be retrieved")
        counts_before_failure = client.get("/diagnostics/stats").json()
        partial = client.get("/aggregate/overview?fail=true")
        require(partial.status_code == 502, f"partial aggregation failure should return 502, got {partial.status_code}")
        counts_after_failure = client.get("/diagnostics/stats").json()
        require(
            counts_after_failure.get("orders-fail", 0) == counts_before_failure.get("orders-fail", 0) + 1,
            "failed source was not called exactly once",
        )
        require(
            counts_after_failure.get("catalog", 0) == counts_before_failure.get("catalog", 0),
            "failed aggregate was incorrectly forwarded to the catalog backend",
        )
        transport = client.get("/aggregate/overview?fail=transport")
        require(
            transport.status_code == 502, f"callout transport failure should return 502, got {transport.status_code}"
        )
        counts_after_transport = client.get("/diagnostics/stats").json()
        require(
            counts_after_transport.get("profile", 0) == counts_after_failure.get("profile", 0),
            "profile callout ran after the first callout transport error",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test local architecture-pattern routes through APIM.")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    check(args.base_url)
    print("Architecture patterns smoke passed: routing, cache/transform, JWT/rate gatekeeper, and aggregation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
