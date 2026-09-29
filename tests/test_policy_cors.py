"""Gateway-level tests for the ``cors`` policy.

Spec: https://learn.microsoft.com/en-us/azure/api-management/cors-policy
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig, RouteConfig
from app.main import create_app

ORIGIN = "http://app.example.test:8080"

CORS_FULL = """\
<policies><inbound>
  <cors allow-credentials="true">
    <allowed-origins><origin>http://app.example.test:8080/</origin></allowed-origins>
    <allowed-methods preflight-result-max-age="300"><method>GET</method><method>PATCH</method></allowed-methods>
    <allowed-headers><header>content-type</header><header>x-custom</header></allowed-headers>
    <expose-headers><header>x-exposed</header></expose-headers>
  </cors>
</inbound><backend><forward-request /></backend><outbound /><on-error /></policies>
"""


def _client(
    policies: list[str] | str,
    *,
    methods: list[str] | None = None,
    allowed_origins: list[str] | None = None,
    require_subscription: bool = False,
) -> tuple[TestClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True}, headers={"x-exposed": "yes"})

    docs = [policies] if isinstance(policies, str) else policies
    config = GatewayConfig(
        allow_anonymous=not require_subscription,
        allowed_origins=allowed_origins or ["http://other.example.test"],
        routes=[
            RouteConfig(
                name="r1",
                path_prefix="/api",
                methods=methods,
                upstream_base_url="http://upstream.example.test",
                upstream_path_prefix="/api",
                policies_xml_documents=docs,
            )
        ],
    )
    app = create_app(config=config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return TestClient(app), seen


def _preflight(client: TestClient, origin: str = ORIGIN, method: str = "PATCH", headers: str = "content-type"):
    return client.options(
        "/api/items",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


def test_preflight_matching_origin_is_answered_by_policy_without_reaching_backend() -> None:
    """Preflight is answered from the cors policy alone (no subscription, no backend call)."""
    client, seen = _client(CORS_FULL, methods=["GET", "PATCH"], require_subscription=True)
    with client:
        resp = _preflight(client)
    assert resp.status_code == 200
    assert resp.content == b""
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-methods"] == "GET, PATCH"
    assert resp.headers["access-control-allow-headers"] == "content-type, x-custom"
    assert resp.headers["access-control-max-age"] == "300"
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert seen == []


def test_preflight_defaults_to_get_and_post_and_max_age_zero() -> None:
    """Without allowed-methods, GET and POST are supported; max-age defaults to 0."""
    policy = (
        "<policies><inbound><cors><allowed-origins><origin>http://app.example.test:8080</origin>"
        "</allowed-origins><allowed-headers><header>*</header></allowed-headers></cors></inbound></policies>"
    )
    client, _ = _client(policy, methods=["GET"])
    with client:
        resp = _preflight(client, method="GET", headers="x-anything")
    assert resp.headers["access-control-allow-methods"] == "GET, POST"
    assert resp.headers["access-control-max-age"] == "0"
    assert resp.headers["access-control-allow-headers"] == "x-anything"
    assert "access-control-allow-credentials" not in resp.headers


def test_preflight_unmatched_origin_terminates_with_empty_200() -> None:
    client, seen = _client(CORS_FULL, methods=["GET", "PATCH"])
    with client:
        resp = _preflight(client, origin="http://evil.example.test")
    assert resp.status_code == 200
    assert resp.content == b""
    assert "access-control-allow-origin" not in resp.headers
    assert seen == []


def test_preflight_unmatched_with_terminate_false_uses_next_in_scope_cors_policy() -> None:
    operation = (
        '<policies><inbound><cors terminate-unmatched-request="false"><allowed-origins>'
        "<origin>http://nope.example.test</origin></allowed-origins><allowed-headers><header>*</header>"
        "</allowed-headers></cors><base /></inbound></policies>"
    )
    api = (
        "<policies><inbound><cors><allowed-origins><origin>http://app.example.test:8080</origin></allowed-origins>"
        "<allowed-headers><header>*</header></allowed-headers></cors></inbound></policies>"
    )
    client, _ = _client([api, operation], methods=["GET"])
    with client:
        resp = _preflight(client, method="GET")
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_preflight_unmatched_with_terminate_false_and_no_other_policy_is_empty_200() -> None:
    policy = CORS_FULL.replace('allow-credentials="true"', 'terminate-unmatched-request="false"')
    client, _ = _client(policy, methods=["GET", "PATCH"])
    with client:
        resp = _preflight(client, origin="http://evil.example.test")
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_actual_request_gets_cors_response_headers_from_policy() -> None:
    client, seen = _client(CORS_FULL, methods=["GET"])
    with client:
        resp = client.get("/api/items", headers={"Origin": ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert resp.headers["access-control-expose-headers"] == "x-exposed"
    assert len(seen) == 1


def test_wildcard_origin_is_star_without_credentials_and_echoed_with_them() -> None:
    open_policy = (
        "<policies><inbound><cors><allowed-origins><origin>*</origin></allowed-origins>"
        "<allowed-headers><header>*</header></allowed-headers></cors></inbound></policies>"
    )
    client, _ = _client(open_policy, methods=["GET"])
    with client:
        assert client.get("/api/items", headers={"Origin": ORIGIN}).headers["access-control-allow-origin"] == "*"
    creds_policy = open_policy.replace("<cors>", '<cors allow-credentials="true">')
    client, _ = _client(creds_policy, methods=["GET"])
    with client:
        resp = client.get("/api/items", headers={"Origin": ORIGIN})
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_unmatched_simple_request_terminates_by_default_and_proceeds_when_false() -> None:
    """GET with a non-matching Origin: empty 200 when terminating, else proceed without CORS headers."""
    client, seen = _client(CORS_FULL, methods=["GET"])
    with client:
        resp = client.get("/api/items", headers={"Origin": "http://evil.example.test"})
    assert resp.status_code == 200
    assert resp.content == b""
    assert seen == []

    lenient = CORS_FULL.replace('allow-credentials="true"', 'terminate-unmatched-request="false"')
    client, seen = _client(lenient, methods=["GET"])
    with client:
        resp = client.get("/api/items", headers={"Origin": "http://evil.example.test"})
    assert resp.json() == {"ok": True}
    assert "access-control-allow-origin" not in resp.headers
    assert len(seen) == 1


def test_request_without_origin_is_untouched() -> None:
    client, seen = _client(CORS_FULL, methods=["GET"])
    with client:
        resp = client.get("/api/items")
    assert resp.json() == {"ok": True}
    assert "access-control-allow-origin" not in resp.headers
    assert len(seen) == 1


def test_gateway_config_allowed_origins_no_longer_adds_cors_headers_to_api_responses() -> None:
    """Gateway API CORS comes only from the cors policy, not the simulator-wide setting."""
    plain = "<policies><inbound /></policies>"
    client, _ = _client(plain, methods=["GET"], allowed_origins=[ORIGIN])
    with client:
        resp = client.get("/api/items", headers={"Origin": ORIGIN})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_simulator_own_endpoints_keep_cors_middleware() -> None:
    client, _ = _client("<policies><inbound /></policies>", methods=["GET"], allowed_origins=[ORIGIN])
    with client:
        resp = client.get("/apim/health", headers={"Origin": ORIGIN})
    assert resp.headers["access-control-allow-origin"] == ORIGIN


def test_preflight_with_no_cors_policy_is_not_answered_by_the_gateway() -> None:
    """No cors policy in scope: the preflight is an ordinary OPTIONS that finds no operation."""
    client, seen = _client("<policies><inbound /></policies>", methods=["GET"])
    with client:
        resp = _preflight(client, method="GET")
    assert resp.status_code == 404
    assert "access-control-allow-origin" not in resp.headers
    assert seen == []


def test_operation_defining_options_bypasses_cors_preflight_logic() -> None:
    """A matching OPTIONS operation runs its own pipeline instead of the cors preflight."""
    client, seen = _client(CORS_FULL, methods=["OPTIONS"])
    with client:
        resp = _preflight(client)
    assert resp.json() == {"ok": True}
    assert len(seen) == 1
