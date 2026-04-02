#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

import httpx


KEYCLOAK_BASE_URL = "http://localhost:8180"
REALM = "subnet-calculator"
CLIENT_ID = "frontend-app"
GATEWAY_BASE_URL = "http://localhost:8000"


def fetch_token(username: str, password: str) -> str:
    token_url = f"{KEYCLOAK_BASE_URL}/realms/{REALM}/protocol/openid-connect/token"
    form = {
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "username": username,
        "password": password,
    }
    with httpx.Client(timeout=20.0) as client:
        response = client.post(token_url, data=form)
        response.raise_for_status()
        return response.json()["access_token"]


def gateway_get(path: str, *, token: str, subscription_key: str) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": subscription_key,
    }
    with httpx.Client(timeout=20.0) as client:
        return client.get(f"{GATEWAY_BASE_URL}{path}", headers=headers)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    try:
        with httpx.Client(timeout=10.0) as client:
            health = client.get(f"{GATEWAY_BASE_URL}/apim/health")
            health.raise_for_status()

        user_token = fetch_token("demo@dev.test", "demo-password")
        admin_token = fetch_token("demo@admin.test", "demo-password")

        user_resp = gateway_get("/api/echo", token=user_token, subscription_key="oidc-demo-key")
        require(user_resp.status_code == 200, f"/api/echo expected 200, got {user_resp.status_code}: {user_resp.text}")
        user_payload = user_resp.json()
        require(user_payload["path"] == "/api/echo", f"unexpected proxied path: {json.dumps(user_payload)}")

        denied_resp = gateway_get("/admin/api/echo", token=user_token, subscription_key="oidc-demo-key")
        require(
            denied_resp.status_code == 403,
            f"/admin/api/echo for demo user expected 403, got {denied_resp.status_code}: {denied_resp.text}",
        )

        admin_resp = gateway_get("/admin/api/echo", token=admin_token, subscription_key="oidc-admin-key")
        require(
            admin_resp.status_code == 200,
            f"/admin/api/echo for admin user expected 200, got {admin_resp.status_code}: {admin_resp.text}",
        )

        print("OIDC smoke passed")
        print("- user route: 200")
        print("- admin route with user token: 403")
        print("- admin route with admin token: 200")
        return 0
    except Exception as exc:
        sys.stderr.write(f"OIDC smoke failed: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
