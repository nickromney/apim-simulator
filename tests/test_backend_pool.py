from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import (
    BackendCircuitBreakerConfig,
    BackendConfig,
    BackendPoolMemberConfig,
    GatewayConfig,
    RouteConfig,
)
from app.main import create_app
from app.urls import http_url


def _pool_config(
    *,
    members: list[BackendPoolMemberConfig],
    circuit_breaker: BackendCircuitBreakerConfig | None = None,
    proxy_max_attempts: int = 1,
) -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        proxy_max_attempts=proxy_max_attempts,
        proxy_retry_statuses=[500, 502, 503, 504],
        backends={
            "backend-a": BackendConfig(url=http_url("backend-a")),
            "backend-b": BackendConfig(url=http_url("backend-b")),
            "llm-pool": BackendConfig(type="pool", pool=members, circuit_breaker=circuit_breaker),
        },
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                upstream_base_url=http_url("unused"),
                upstream_path_prefix="/api",
                backend="llm-pool",
            )
        ],
    )


def _host_echo_client(config: GatewayConfig, *, failing_hosts: set[str] | None = None) -> TestClient:
    failing = failing_hosts or set()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host in failing:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"host": req.url.host})

    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return TestClient(app)


@pytest.mark.contract("BACKEND-POOL")
def test_pool_round_robin_alternates_members() -> None:
    config = _pool_config(
        members=[
            BackendPoolMemberConfig(backend_id="backend-a"),
            BackendPoolMemberConfig(backend_id="backend-b"),
        ]
    )
    with _host_echo_client(config) as client:
        hosts = []
        backend_ids = []
        for _ in range(4):
            resp = client.get("/api/health")
            assert resp.status_code == 200
            assert resp.headers["x-apim-backend-pool"] == "llm-pool"
            hosts.append(resp.json()["host"])
            backend_ids.append(resp.headers["x-apim-backend-id"])
    assert hosts == ["backend-a", "backend-b", "backend-a", "backend-b"]
    assert backend_ids == ["backend-a", "backend-b", "backend-a", "backend-b"]


@pytest.mark.contract("BACKEND-POOL")
def test_pool_weights_bias_the_rotation() -> None:
    config = _pool_config(
        members=[
            BackendPoolMemberConfig(backend_id="backend-a", weight=2),
            BackendPoolMemberConfig(backend_id="backend-b", weight=1),
        ]
    )
    with _host_echo_client(config) as client:
        hosts = [client.get("/api/health").json()["host"] for _ in range(6)]
    assert hosts.count("backend-a") == 4
    assert hosts.count("backend-b") == 2


@pytest.mark.contract("BACKEND-POOL")
def test_pool_fails_over_and_opens_circuit() -> None:
    config = _pool_config(
        members=[
            BackendPoolMemberConfig(backend_id="backend-a"),
            BackendPoolMemberConfig(backend_id="backend-b"),
        ],
        circuit_breaker=BackendCircuitBreakerConfig(failure_count=1, trip_duration_seconds=60.0),
        proxy_max_attempts=2,
    )
    with _host_echo_client(config, failing_hosts={"backend-a"}) as client:
        first = client.get("/api/health")
        assert first.status_code == 200
        assert first.json()["host"] == "backend-b"
        assert first.headers["x-apim-backend-id"] == "backend-b"

        # backend-a's circuit is now open, so the rotation sticks to backend-b.
        for _ in range(3):
            resp = client.get("/api/health")
            assert resp.status_code == 200
            assert resp.json()["host"] == "backend-b"


@pytest.mark.contract("BACKEND-POOL")
def test_pool_returns_503_when_all_members_are_open() -> None:
    config = _pool_config(
        members=[
            BackendPoolMemberConfig(backend_id="backend-a"),
            BackendPoolMemberConfig(backend_id="backend-b"),
        ],
        circuit_breaker=BackendCircuitBreakerConfig(failure_count=1, trip_duration_seconds=60.0),
        proxy_max_attempts=2,
    )
    with _host_echo_client(config, failing_hosts={"backend-a", "backend-b"}) as client:
        first = client.get("/api/health")
        assert first.status_code == 500

        second = client.get("/api/health")
        assert second.status_code == 503
        assert second.json()["detail"] == "All backend pool members are unavailable"


@pytest.mark.contract("BACKEND-POOL")
def test_pool_member_recovers_after_trip_duration() -> None:
    config = _pool_config(
        members=[BackendPoolMemberConfig(backend_id="backend-a")],
        circuit_breaker=BackendCircuitBreakerConfig(failure_count=1, trip_duration_seconds=0.05),
        proxy_max_attempts=1,
    )
    failing: set[str] = {"backend-a"}
    with _host_echo_client(config, failing_hosts=failing) as client:
        assert client.get("/api/health").status_code == 500
        assert client.get("/api/health").status_code == 503

        failing.clear()
        time.sleep(0.1)
        recovered = client.get("/api/health")
        assert recovered.status_code == 200
        assert recovered.json()["host"] == "backend-a"


@pytest.mark.contract("BACKEND-POOL")
def test_pool_prefers_lower_priority_group() -> None:
    config = _pool_config(
        members=[
            BackendPoolMemberConfig(backend_id="backend-a", priority=1),
            BackendPoolMemberConfig(backend_id="backend-b", priority=2),
        ],
        circuit_breaker=BackendCircuitBreakerConfig(failure_count=1, trip_duration_seconds=60.0),
        proxy_max_attempts=2,
    )
    with _host_echo_client(config) as client:
        hosts = [client.get("/api/health").json()["host"] for _ in range(3)]
    assert hosts == ["backend-a", "backend-a", "backend-a"]

    with _host_echo_client(config, failing_hosts={"backend-a"}) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["host"] == "backend-b"


def test_pool_backend_requires_members() -> None:
    with pytest.raises(ValueError):
        BackendConfig(type="pool")
    with pytest.raises(ValueError):
        BackendConfig()
