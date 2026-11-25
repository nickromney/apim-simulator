"""The retry policy, inside policy sections and around forward-request.

https://learn.microsoft.com/en-us/azure/api-management/retry-policy
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import BackendConfig, GatewayConfig, RouteConfig
from app.main import create_app
from app.policy import PolicyRequest, PolicyRuntime, PolicyTraceCollector, apply_inbound_async, parse_policies_xml


def _request() -> PolicyRequest:
    return PolicyRequest(method="GET", path="/api/test", query={}, headers={}, variables={})


def _runtime(handler=None, sleeps: list[float] | None = None) -> PolicyRuntime:
    recorded = sleeps if sleeps is not None else []

    async def sleep(seconds: float) -> None:
        recorded.append(seconds)

    return PolicyRuntime(
        gateway_config=GatewayConfig(allow_anonymous=True),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler or (lambda r: httpx.Response(200)))),
        trace=PolicyTraceCollector(),
        sleep=sleep,
    )


def _retry(attrs: str, body: str, section: str = "inbound") -> str:
    return f"<policies><{section}><retry {attrs}>{body}</retry></{section}></policies>"


_COUNTER = '<set-variable name="n" value="@(context.Variables.GetValueOrDefault&lt;int&gt;(&quot;n&quot;, 0) + 1)" />'


@pytest.mark.parametrize(
    ("attrs", "message"),
    [
        ('count="1" interval="1"', "retry requires condition"),
        ('condition="true" interval="1"', "retry requires count"),
        ('condition="true" count="1"', "retry requires interval"),
        ('condition="maybe" count="1" interval="1"', "Unsupported retry condition"),
    ],
)
def test_retry_rejects_missing_or_invalid_required_attributes(attrs: str, message: str) -> None:
    """condition, count and interval are required; condition is a Boolean or expression."""
    with pytest.raises(HTTPException, match=message):
        parse_policies_xml(_retry(attrs, _COUNTER))


@pytest.mark.parametrize("count", ["0", "51", "two"])
def test_retry_count_must_be_an_integer_from_1_to_50(count: str) -> None:
    doc = parse_policies_xml(_retry(f'condition="true" count="{count}" interval="0"', _COUNTER))
    with pytest.raises(HTTPException, match="retry count"):
        asyncio.run(apply_inbound_async([doc], _request(), _runtime()))


@pytest.mark.contract("POLICY-RETRY")
def test_retry_runs_children_once_then_while_condition_holds() -> None:
    """Children run before the condition is first evaluated, then until it is false."""
    doc = parse_policies_xml(
        _retry(
            'condition="@(context.Variables.GetValueOrDefault&lt;int&gt;(&quot;n&quot;) &lt; 3)" count="10" interval="0"',
            _COUNTER,
        )
    )
    req = _request()
    asyncio.run(apply_inbound_async([doc], req, _runtime()))
    assert req.variables["n"] == 3


def test_retry_false_condition_still_runs_children_once() -> None:
    doc = parse_policies_xml(_retry('condition="false" count="5" interval="0"', _COUNTER))
    req = _request()
    asyncio.run(apply_inbound_async([doc], req, _runtime()))
    assert req.variables["n"] == 1


def test_retry_stops_when_count_is_used_up() -> None:
    doc = parse_policies_xml(_retry('condition="true" count="2" interval="0"', _COUNTER))
    req = _request()
    runtime = _runtime()
    asyncio.run(apply_inbound_async([doc], req, runtime))
    assert req.variables["n"] == 3
    assert runtime.trace is not None
    assert runtime.trace.steps[-1] == {"step": "retry", "stopped": "count", "retries": 2}


def test_retry_fixed_interval_waits_the_same_each_time() -> None:
    sleeps: list[float] = []
    doc = parse_policies_xml(_retry('condition="true" count="3" interval="2"', _COUNTER))
    asyncio.run(apply_inbound_async([doc], _request(), _runtime(sleeps=sleeps)))
    assert sleeps == [2.0, 2.0, 2.0]


def test_retry_linear_interval_adds_delta_and_first_fast_retry_skips_first_wait() -> None:
    """interval + (n - 1) * delta; first-fast-retry makes the first retry immediate."""
    sleeps: list[float] = []
    doc = parse_policies_xml(
        _retry('condition="true" count="4" interval="1" delta="2" first-fast-retry="true"', _COUNTER)
    )
    asyncio.run(apply_inbound_async([doc], _request(), _runtime(sleeps=sleeps)))
    # The zero wait for the immediate retry is not slept.
    assert sleeps == [3.0, 5.0, 7.0]


@pytest.mark.contract("POLICY-RETRY")
def test_retry_exponential_interval_is_jittered_and_capped() -> None:
    """interval + 2^(n - 1) * random(0.8 delta, 1.2 delta), capped at max-interval."""
    sleeps: list[float] = []
    doc = parse_policies_xml(_retry('condition="true" count="5" interval="10" delta="10" max-interval="40"', _COUNTER))
    asyncio.run(apply_inbound_async([doc], _request(), _runtime(sleeps=sleeps)))
    assert 18.0 <= sleeps[0] <= 22.0
    assert 26.0 <= sleeps[1] <= 34.0
    assert sleeps[2:] == [40.0, 40.0, 40.0]


def test_retry_around_send_request_retries_until_the_callout_succeeds() -> None:
    """Microsoft's example: retry a callout on a dropped connection or a 5xx."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503 if len(calls) < 3 else 200, json={"call": len(calls)})

    doc = parse_policies_xml(
        _retry(
            'condition="@(context.Variables[&quot;response&quot;] == null || '
            '((IResponse)context.Variables[&quot;response&quot;]).StatusCode &gt;= 500)" '
            'count="3" interval="1" first-fast-retry="true"',
            '<send-request mode="new" response-variable-name="response" timeout="3" ignore-error="true">'
            "<set-url>https://api.contoso.test/products/5</set-url><set-method>GET</set-method></send-request>",
        )
    )
    req = _request()
    sleeps: list[float] = []
    asyncio.run(apply_inbound_async([doc], req, _runtime(handler, sleeps)))
    assert len(calls) == 3
    assert req.variables["response"].StatusCode == 200
    assert sleeps == [1.0]


def test_retry_children_may_end_the_section_with_a_response() -> None:
    doc = parse_policies_xml(
        _retry(
            'condition="true" count="3" interval="0"', '<return-response><set-status code="418" /></return-response>'
        )
    )
    result = asyncio.run(apply_inbound_async([doc], _request(), _runtime()))
    assert result is not None
    assert result.status_code == 418


def test_retry_wrapping_forward_request_rejects_return_response() -> None:
    with pytest.raises(HTTPException, match="return-response inside a retry"):
        parse_policies_xml(
            _retry(
                'condition="true" count="1" interval="0"',
                '<choose><when condition="true"><return-response /></when></choose><forward-request />',
                section="backend",
            )
        )


def _gateway(policy: str, handler, backends: dict[str, BackendConfig] | None = None) -> TestClient:
    app = create_app(
        config=GatewayConfig(
            allow_anonymous=True,
            backends=backends or {},
            routes=[
                RouteConfig(
                    name="r1",
                    path_prefix="/api",
                    upstream_base_url="http://primary",
                    upstream_path_prefix="/api",
                    policies_xml=policy,
                )
            ],
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return TestClient(app)


@pytest.mark.contract("POLICY-RETRY")
def test_retry_switches_backend_after_429() -> None:
    """Microsoft's example: on 429 from the primary, forward again to the secondary."""
    policy = """\
<policies>
  <inbound><base /></inbound>
  <backend>
    <retry condition="@(context.Response != null &amp;&amp; context.Response.StatusCode == 429)"
           count="1" interval="1" first-fast-retry="true">
      <set-variable name="attempt-count"
                    value="@(context.Variables.GetValueOrDefault&lt;int&gt;(&quot;attempt-count&quot;, 0)+1)" />
      <set-backend-service backend-id="@(context.Variables.GetValueOrDefault&lt;int&gt;(&quot;attempt-count&quot;) &lt; 2 ? &quot;primary-backend&quot; : &quot;secondary-backend&quot; )" />
      <forward-request />
    </retry>
  </backend>
  <outbound><base /></outbound>
  <on-error><base /></on-error>
</policies>
"""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "primary":
            return httpx.Response(429, json={"from": "primary"})
        return httpx.Response(200, json={"from": "secondary"})

    backends = {
        "primary-backend": BackendConfig(url="http://primary/api"),
        "secondary-backend": BackendConfig(url="http://secondary/api"),
    }
    with _gateway(policy, handler, backends) as client:
        resp = client.get("/api/widgets")

    assert resp.status_code == 200
    assert resp.json() == {"from": "secondary"}
    assert hosts == ["primary", "secondary"]


def test_retry_around_forward_request_returns_last_response_when_count_is_used_up() -> None:
    policy = """\
<policies>
  <backend>
    <retry condition="@(context.Response.StatusCode == 500)" count="2" interval="0">
      <forward-request buffer-request-body="true" />
    </retry>
  </backend>
</policies>
"""
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(500, json={"attempt": len(bodies)})

    with _gateway(policy, handler) as client:
        resp = client.post("/api/orders", content=b"payload")

    assert resp.status_code == 500
    assert resp.json() == {"attempt": 3}
    assert bodies == [b"payload", b"payload", b"payload"]


def test_retry_around_forward_request_stops_on_success() -> None:
    policy = """\
<policies>
  <backend>
    <retry condition="@(context.Response.StatusCode &gt;= 500)" count="5" interval="0">
      <forward-request />
    </retry>
  </backend>
</policies>
"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"attempt": len(calls)})

    with _gateway(policy, handler) as client:
        resp = client.get("/api/orders")

    assert resp.status_code == 200
    assert resp.json() == {"attempt": 2}
