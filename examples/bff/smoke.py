from __future__ import annotations

import argparse
import os
import time

import httpx
import jwt

DEFAULT_BASE_URL = os.getenv("APIM_BASE_URL", "http://localhost:8000")
JWT_SECRET = "local-bff-demo-signing-key-32bytes!!"
JWT_ISSUER = "http://bff-demo.local"


def make_token(audience: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": "bff-smoke", "iss": JWT_ISSUER, "aud": audience, "iat": now, "exp": now + 300},
        JWT_SECRET,
        algorithm="HS256",
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def check(base_url: str) -> None:
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=10.0) as client:
        for audience, path, expected_keys in (
            ("bff-web", "/web/catalog", {"id", "name", "description", "price"}),
            ("bff-mobile", "/mobile/catalog", {"id", "name"}),
        ):
            token = make_token(audience)
            response = client.get(
                path,
                headers={"Authorization": f"Bearer {token}", "x-apim-trace": "true"},
            )
            require(response.status_code == 200, f"{path}: expected 200, got {response.status_code}: {response.text}")
            items = response.json()
            require(isinstance(items, list) and bool(items), f"{path}: expected a non-empty items list")
            require(set(items[0]) == expected_keys, f"{path}: unexpected item fields: {sorted(items[0])}")
            trace_id = response.headers.get("x-apim-trace-id")
            require(bool(trace_id), f"{path}: expected x-apim-trace-id when tracing is enabled")
            trace = client.get(f"/apim/trace/{trace_id}")
            require(trace.status_code == 200, f"trace {trace_id}: expected 200, got {trace.status_code}")

        missing = client.get("/web/catalog")
        require(missing.status_code == 401, f"missing token: expected 401, got {missing.status_code}")

        wrong_audience = client.get(
            "/web/catalog",
            headers={"Authorization": f"Bearer {make_token('bff-mobile')}"},
        )
        require(
            wrong_audience.status_code == 401,
            f"wrong audience: expected 401, got {wrong_audience.status_code}: {wrong_audience.text}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the BFF example through APIM.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="APIM base URL")
    args = parser.parse_args()
    check(args.base_url)
    print("BFF smoke passed: web/mobile shaping, JWT authorization, and APIM trace retrieval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
