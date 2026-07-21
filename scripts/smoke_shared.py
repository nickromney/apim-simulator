from __future__ import annotations

import os
import time
from collections.abc import Callable

import httpx

GATEWAY_BASE_URL = os.getenv("SMOKE_SHARED_BASE_URL", "http://127.0.0.1:8000")
KEYCLOAK_BASE_URL = os.getenv("SMOKE_SHARED_KEYCLOAK_BASE_URL", "http://localhost:8180")
DEFAULT_ATTEMPTS = int(os.getenv("SMOKE_SHARED_ATTEMPTS", "60"))
DEFAULT_DELAY_SECONDS = float(os.getenv("SMOKE_SHARED_RETRY_DELAY_SECONDS", "2"))

WORKLOADS = {
    "team-a": ("workload-team-a", os.getenv("SMOKE_SHARED_TEAM_A_SECRET", "workload-team-a-demo-secret")),
    "team-b": ("workload-team-b", os.getenv("SMOKE_SHARED_TEAM_B_SECRET", "workload-team-b-demo-secret")),
}


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


def fetch_workload_token(client_id: str, client_secret: str) -> str:
    token_url = f"{KEYCLOAK_BASE_URL}/realms/subnet-calculator/protocol/openid-connect/token"
    form = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    with httpx.Client(timeout=20.0) as client:
        response = client.post(token_url, data=form)
        response.raise_for_status()
        return response.json()["access_token"]


def gateway_get(path: str, *, token: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=10.0) as client:
        return client.get(f"{GATEWAY_BASE_URL}{path}", headers=headers)


def expect(path: str, *, token: str | None, status_code: int, label: str) -> httpx.Response:
    response = gateway_get(path, token=token)
    require(
        response.status_code == status_code,
        f"{label}: expected {path} to return {status_code}, got {response.status_code}: {response.text}",
    )
    return response


def check_gateway_health() -> None:
    with httpx.Client(timeout=10.0) as client:
        client.get(f"{GATEWAY_BASE_URL}/apim/health").raise_for_status()


def main() -> int:
    retry_call(check_gateway_health)

    team_a_token = retry_call(lambda: fetch_workload_token(*WORKLOADS["team-a"]))
    team_b_token = retry_call(lambda: fetch_workload_token(*WORKLOADS["team-b"]))

    expect("/team-a/echo", token=None, status_code=401, label="anonymous")

    retry_call(lambda: expect("/team-a/echo", token=team_a_token, status_code=200, label="team-a token"))
    expect("/team-b/echo", token=team_a_token, status_code=403, label="team-a token")
    expect("/shared/echo", token=team_a_token, status_code=200, label="team-a token")

    expect("/team-b/echo", token=team_b_token, status_code=200, label="team-b token")
    expect("/team-a/echo", token=team_b_token, status_code=403, label="team-b token")
    expect("/shared/echo", token=team_b_token, status_code=200, label="team-b token")

    print("shared gateway smoke passed")
    print("- anonymous request: 401")
    print("- team-a workload: /team-a 200, /team-b 403, /shared 200")
    print("- team-b workload: /team-b 200, /team-a 403, /shared 200")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
