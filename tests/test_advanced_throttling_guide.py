"""Executable checks for Microsoft Learn's advanced throttling guide."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import ApiConfig, GatewayConfig, OperationConfig, TenantAccessConfig
from app.main import create_app
from app.policy import PolicyRequest, PolicyRuntime, apply_inbound, finalize_deferred_actions, parse_policies_xml


def _example(number: int) -> str:
    path = Path(__file__).parent / "fixtures/policy_guides/api-management-sample-flexible-throttling"
    xml = (path / f"example-{number:02d}.xml").read_text()
    # Learn's two expression snippets contain nested, unescaped XML quotes.
    return re.sub(r'counter-key="(@\(.+\))"', lambda match: f"counter-key='{match[1]}'", xml)


def _policy(inbound: str) -> str:
    return f"<policies><inbound>{inbound}</inbound><backend><forward-request /></backend><outbound /><on-error /></policies>"


def _client(inbound: str) -> TestClient:
    cfg = GatewayConfig(
        network_security={"allow_simulated_forwarded_headers": True},
        allow_anonymous=True,
        apis={
            "throttle": ApiConfig(
                name="Throttle",
                path="throttle",
                upstream_base_url="http://backend",
                policies_xml=_policy(inbound),
                operations={"call": OperationConfig(name="Call", method="POST", url_template="/call")},
            )
        },
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"ok")))
    return TestClient(create_app(config=cfg, http_client=upstream))


def test_documented_ip_rate_and_bandwidth_quota_accepts_xml_and_isolates_addresses() -> None:
    inbound = _example(2)
    with _client(inbound) as client:
        first = {"X-Forwarded-For": "198.51.100.10"}
        assert [client.post("/throttle/call", headers=first).status_code for _ in range(10)] == [200] * 10
        blocked = client.post("/throttle/call", headers=first)
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        assert client.post("/throttle/call", headers={"X-Forwarded-For": "198.51.100.20"}).status_code == 200


def test_documented_jwt_subject_keys_isolate_users() -> None:
    inbound = _example(3)

    def headers(subject):
        token = jwt.encode({"sub": subject}, "local-test-key-at-least-32-bytes-long", algorithm="HS256")
        return {"Authorization": "Bearer " + token}

    with _client(inbound) as client:
        assert [client.post("/throttle/call", headers=headers("alice")).status_code for _ in range(10)] == [200] * 10
        assert client.post("/throttle/call", headers=headers("alice")).status_code == 429
        assert client.post("/throttle/call", headers=headers("bob")).status_code == 200


def test_documented_client_header_keys_isolate_customers() -> None:
    inbound = _example(4)
    with _client(inbound) as client:
        assert [client.post("/throttle/call", headers={"Rate-Key": "customer-a"}).status_code for _ in range(100)] == [
            200
        ] * 100
        assert client.post("/throttle/call", headers={"Rate-Key": "customer-a"}).status_code == 429
        assert client.post("/throttle/call", headers={"Rate-Key": "customer-b"}).status_code == 200


def test_first_example_runs_with_classic_sliding_window_and_renews() -> None:
    doc = parse_policies_xml(_policy(_example(1)))
    store = {}

    def apply(now: float):
        request = PolicyRequest(
            method="POST", path="/call", query={}, headers={}, variables={"rate_limit_store": store}
        )
        return apply_inbound([doc], request, PolicyRuntime(clock=lambda: now))

    assert [apply(1000.0) for _ in range(6)] == [None] * 6
    assert apply(1010.0).status_code == 429
    assert apply(1060.0) is None


@pytest.mark.parametrize("inbound", [_example(1), '<rate-limit calls="6" renewal-period="60" />'])
def test_first_example_v2_token_bucket_refills_one_token_after_ten_seconds(inbound: str) -> None:
    doc = parse_policies_xml(_policy(inbound))
    config = GatewayConfig(
        network_security={"allow_simulated_forwarded_headers": True}, throttling={"algorithm": "token-bucket"}
    )
    store = {}

    def apply(now: float):
        request = PolicyRequest(
            method="POST",
            path="/call",
            query={},
            headers={},
            variables={"rate_limit_store": store, "subscription_id": "local-subscription"},
        )
        runtime = PolicyRuntime(clock=lambda: now, gateway_config=config)
        return apply_inbound([doc], request, runtime)

    assert [apply(1000.0) for _ in range(6)] == [None] * 6
    assert int(apply(1000.0).headers["retry-after"]) == 10
    assert int(apply(1009.0).headers["retry-after"]) == 1
    assert apply(1010.0) is None
    assert apply(1010.0).status_code == 429
    # Long idle time refills to capacity, never beyond it.
    assert [apply(2000.0) for _ in range(6)] == [None] * 6
    assert apply(2000.0).status_code == 429


def test_keyed_bandwidth_quota_counts_request_and_response_once_across_scopes() -> None:
    xml = _policy(
        '<quota-by-key calls="1" bandwidth="2" renewal-period="300" counter-key="same" /><quota-by-key calls="1" bandwidth="2" renewal-period="300" counter-key="same" />'
    )
    doc = parse_policies_xml(xml)
    quota_store = {}
    runtime = PolicyRuntime(clock=lambda: 1000.0)
    req = PolicyRequest(
        method="POST",
        path="/call",
        query={},
        headers={},
        variables={"quota_store": quota_store},
        body=b"a" * 1024,
        response_body=b"b" * 1024,
        response_status_code=200,
    )
    assert apply_inbound([doc], req, runtime) is None
    finalize_deferred_actions(req, runtime)
    assert quota_store["quota-by-key:same"]["count"] == 1
    assert quota_store["quota-by-key:same"]["bandwidth"] == 2
    next_req = PolicyRequest(method="POST", path="/call", query={}, headers={}, variables={"quota_store": quota_store})
    blocked = apply_inbound([doc], next_req, PolicyRuntime(clock=lambda: 1001.0))
    assert blocked.status_code == 403
    assert "bandwidth" in json.loads(blocked.body)["message"]


def test_bandwidth_only_quota_blocks_after_response_and_renews_without_call_limit() -> None:
    doc = parse_policies_xml(
        _policy(
            '<quota-by-key bandwidth="1" renewal-period="300" counter-key="same" first-period-start="1970-01-01T00:00:00Z" />'
        )
    )
    store = {}

    def request():
        return PolicyRequest(
            method="POST",
            path="/call",
            query={},
            headers={},
            variables={"quota_store": store},
            response_body=b"x" * 1024,
            response_status_code=200,
        )

    first = request()
    runtime = PolicyRuntime(clock=lambda: 1000.0)
    assert apply_inbound([doc], first, runtime) is None
    finalize_deferred_actions(first, runtime)
    blocked = apply_inbound([doc], request(), PolicyRuntime(clock=lambda: 1001.0))
    assert blocked.status_code == 403
    assert int(blocked.headers["retry-after"]) == 199
    renewed = request()
    renewed_runtime = PolicyRuntime(clock=lambda: 1200.0)
    assert apply_inbound([doc], renewed, renewed_runtime) is None
    assert store["quota-by-key:same"]["count"] == 0


def test_keyed_bandwidth_conditional_accounting_waits_for_successful_response() -> None:
    doc = parse_policies_xml(
        _policy(
            """<quota-by-key calls="5" bandwidth="1" renewal-period="300" counter-key="same" increment-condition="@(context.Response.StatusCode &lt; 400)" />"""
        )
    )
    store = {}
    for status in (500, 200):
        req = PolicyRequest(
            method="POST",
            path="/call",
            query={},
            headers={},
            variables={"quota_store": store},
            response_body=b"x" * 1024,
            response_status_code=status,
        )
        runtime = PolicyRuntime(clock=lambda: 1000.0)
        assert apply_inbound([doc], req, runtime) is None
        finalize_deferred_actions(req, runtime)
    assert store["quota-by-key:same"]["count"] == 1
    assert store["quota-by-key:same"]["bandwidth"] == 1


@pytest.mark.parametrize("condition", ["", ' increment-condition="@(context.Response.StatusCode &lt; 400)"'])
def test_keyed_bandwidth_quota_accounts_for_streaming_backend_response(condition: str) -> None:
    class BackendBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"response" * 64
            yield b"response" * 64

    backend_calls = []

    def backend(request):
        backend_calls.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/plain"}, stream=BackendBody())

    cfg = GatewayConfig(
        network_security={"allow_simulated_forwarded_headers": True},
        allow_anonymous=True,
        proxy_streaming=True,
        apis={
            "stream": ApiConfig(
                name="Stream",
                path="stream",
                upstream_base_url="http://backend",
                policies_xml=_policy(
                    f'<quota-by-key calls="10" bandwidth="2" renewal-period="300" counter-key="stream"{condition} />'
                ).replace("<forward-request />", '<forward-request buffer-response="false" />'),
                operations={"call": OperationConfig(name="Call", method="POST", url_template="/call")},
            )
        },
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(backend))
    app = create_app(config=cfg, http_client=upstream)
    with TestClient(app) as client:
        with client.stream("POST", "/stream/call", content=b"request!" * 128) as first:
            assert first.status_code == 200
            assert first.read() == b"response" * 128
        assert app.state.quota_store["quota-by-key:stream"]["bandwidth"] == 2
        second = client.post("/stream/call")
        assert second.status_code == 403
        assert "bandwidth" in second.json()["message"]
        assert int(second.headers["Retry-After"]) > 0
        assert len(backend_calls) == 1


def test_live_workflow_authors_guide_policies_and_cleans_up_resources() -> None:
    path = Path(__file__).resolve().parents[1] / "examples/apim-policies/throttling.py"
    spec = importlib.util.spec_from_file_location("throttling_workflow", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = GatewayConfig(
        network_security={"allow_simulated_forwarded_headers": True},
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="tenant"),
    )
    with TestClient(create_app(config=config), headers={"X-Apim-Tenant-Key": "tenant"}) as client:
        outcome = module.run(client)
        assert outcome["results"]["client_header"]["allowed"] == 100
        assert outcome["results"]["bandwidth"]["after_body_accounting"] == 403
        assert outcome["results"]["combined"]["shared_subscription_block"] == 429
        assert client.get("/apim/management/apis").json() == []
        assert client.get("/apim/management/products").json() == []
        assert client.get("/apim/management/subscriptions").json() == []
