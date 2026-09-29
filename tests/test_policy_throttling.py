from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.policy import (
    PolicyRequest,
    PolicyRuntime,
    apply_inbound,
    finalize_deferred_actions,
    parse_policies_xml,
)


def _doc(inbound: str):
    return parse_policies_xml(
        f"""<policies><inbound>{inbound}</inbound><backend /><outbound /><on-error /></policies>"""
    )


def _request(
    *,
    variables: dict[str, Any],
    body: bytes = b"",
    response_status_code: int | None = None,
    response_body: bytes = b"",
    response_headers: dict[str, str] | None = None,
) -> PolicyRequest:
    return PolicyRequest(
        method="GET",
        path="/api/items",
        query={},
        headers={},
        variables=variables,
        body=body,
        response_status_code=response_status_code,
        response_body=response_body,
        response_headers=response_headers,
    )


def _runtime(now: float) -> PolicyRuntime:
    return PolicyRuntime(clock=lambda: now)


def _json_body(response: Any) -> dict[str, Any]:
    return json.loads(response.body)


def test_subscription_throttling_parser_matches_supported_attributes_and_children() -> None:
    """The policy reference defines these attributes and api/operation children.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    """
    doc = _doc(
        '<rate-limit calls="2" renewal-period="60" retry-after-header-name="X-Retry" '
        'retry-after-variable-name="retrySeconds" remaining-calls-header-name="X-Remaining" '
        'remaining-calls-variable-name="remainingCalls" total-calls-header-name="X-Total">'
        '<api id="orders" calls="1" renewal-period="60">'
        '<operation name="get-order" calls="1" renewal-period="60" />'
        "</api></rate-limit>"
    )

    policy = doc.inbound[0]
    assert policy.calls == 2
    assert policy.renewal_period == 60
    assert policy.retry_after_header_name == "X-Retry"
    assert len(policy.rules) == 2
    assert policy.rules[0].target_id == "orders"
    assert policy.rules[1].target_name == "get-order"


@pytest.mark.parametrize(
    "xml, detail",
    [
        ('<rate-limit calls="1" renewal-period="60" scope="subscription" />', "scope"),
        ('<rate-limit calls="1" renewal-period="301" />', "300"),
        ('<quota-by-key calls="1" renewal-period="299" counter-key="demo" />', "300"),
        ('<quota-by-key bandwidth="1" renewal-period="300" counter-key="demo" />', "bandwidth"),
    ],
)
def test_throttling_parser_rejects_non_apim_or_unsupported_configuration(xml: str, detail: str) -> None:
    """The policy references list the supported attributes and period limits.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    https://learn.microsoft.com/en-us/azure/api-management/quota-policy
    https://learn.microsoft.com/en-us/azure/api-management/quota-by-key-policy
    """
    with pytest.raises(HTTPException, match=detail):
        _doc(xml)


def test_rate_limit_returns_apim_json_and_retry_after_without_invented_headers() -> None:
    """The rate-limit reference and error-handling docs define the 429 contract.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    now = 1_000.0
    policy = _doc('<rate-limit calls="1" renewal-period="60" />')
    store: dict[str, Any] = {}
    variables = {"subscription_id": "sub-1", "rate_limit_store": store}
    runtime = _runtime(now)

    assert apply_inbound([policy], _request(variables=variables), runtime) is None
    blocked = apply_inbound([policy], _request(variables=variables), runtime)

    assert blocked is not None
    assert blocked.status_code == 429
    assert blocked.headers == {"content-type": "application/json", "retry-after": "60"}
    assert _json_body(blocked) == {
        "statusCode": 429,
        "message": "Rate limit is exceeded. Try again in 60 seconds.",
    }


def test_rate_limit_is_sliding_and_is_inactive_without_subscription() -> None:
    """The rate-limit docs require a subscription key and document a sliding window.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    """
    current = [1_000.0]
    policy = _doc('<rate-limit calls="1" renewal-period="60" />')
    store: dict[str, Any] = {}
    runtime = PolicyRuntime(clock=lambda: current[0])
    subscribed = {"subscription_id": "sub-1", "rate_limit_store": store}

    assert apply_inbound([policy], _request(variables=subscribed), runtime) is None
    assert apply_inbound([policy], _request(variables=subscribed), runtime) is not None

    current[0] = 1_060.001
    assert apply_inbound([policy], _request(variables=subscribed), runtime) is None

    no_subscription = {"subscription_id": "", "rate_limit_store": store}
    assert apply_inbound([policy], _request(variables=no_subscription), runtime) is None


def test_rate_limit_publishes_only_requested_headers_and_variables() -> None:
    """The rate-limit docs say rate headers are opt-in and describe their values.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    """
    policy = _doc(
        '<rate-limit calls="2" renewal-period="60" remaining-calls-header-name="X-Remaining" '
        'remaining-calls-variable-name="remaining" total-calls-header-name="X-Total" />'
    )
    variables = {"subscription_id": "sub-1", "rate_limit_store": {}}
    req = _request(variables=variables)
    runtime = _runtime(1_000.0)

    assert apply_inbound([policy], req, runtime) is None
    response_headers: dict[str, str] = {}
    req.response_headers = response_headers
    finalize_deferred_actions(req, runtime)

    assert response_headers == {"x-remaining": "1", "x-total": "2"}
    assert req.variables["remaining"] == 1


def test_rate_limit_nested_api_and_operation_limits_are_independent() -> None:
    """The rate-limit docs state product, API, and operation limits apply independently.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    """
    policy = _doc(
        '<rate-limit calls="10" renewal-period="60">'
        '<api id="orders" calls="2" renewal-period="60">'
        '<operation id="get-order" calls="1" renewal-period="60" />'
        "</api></rate-limit>"
    )
    store: dict[str, Any] = {}
    variables = {
        "subscription_id": "sub-1",
        "api_id": "orders",
        "operation_id": "get-order",
        "rate_limit_store": store,
    }
    runtime = _runtime(1_000.0)

    assert apply_inbound([policy], _request(variables=variables), runtime) is None
    blocked = apply_inbound([policy], _request(variables=variables), runtime)
    assert blocked is not None
    assert blocked.status_code == 429


def test_rate_limit_by_key_evaluates_response_condition_during_finalize() -> None:
    """The rate-limit-by-key docs defer expression increments to outbound processing.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-by-key-policy
    """
    policy = _doc(
        '<rate-limit-by-key calls="1" renewal-period="60" counter-key="client" '
        'increment-condition="@(context.Response.StatusCode == 200)" />'
    )
    store: dict[str, Any] = {}
    variables = {"rate_limit_store": store}
    runtime = _runtime(1_000.0)

    first = _request(variables=variables)
    assert apply_inbound([policy], first, runtime) is None
    first.response_status_code = 500
    finalize_deferred_actions(first, runtime)
    assert store == {"rate-limit-by-key:client": []}

    second = _request(variables=variables)
    assert apply_inbound([policy], second, runtime) is None
    second.response_status_code = 200
    finalize_deferred_actions(second, runtime)
    assert store["rate-limit-by-key:client"] == [1_000.0]

    blocked = apply_inbound([policy], _request(variables=variables), runtime)
    assert blocked is not None
    assert blocked.status_code == 429
    assert _json_body(blocked)["message"] == "Rate limit is exceeded. Try again in 60 seconds."


def test_quota_returns_forbidden_json_retry_after_and_call_volume_message() -> None:
    """The quota and error-handling docs define the 403, Retry-After, and message.

    https://learn.microsoft.com/en-us/azure/api-management/quota-policy
    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    policy = _doc('<quota calls="1" renewal-period="300" />')
    store: dict[str, Any] = {}
    variables = {"subscription_id": "sub-1", "quota_store": store}
    runtime = _runtime(1_000.0)

    assert apply_inbound([policy], _request(variables=variables), runtime) is None
    blocked = apply_inbound([policy], _request(variables=variables), runtime)

    assert blocked is not None
    assert blocked.status_code == 403
    assert blocked.headers == {"content-type": "application/json", "retry-after": "300"}
    assert _json_body(blocked) == {
        "statusCode": 403,
        "message": "Out of call volume quota. Quota will be replenished in 00:05:00.",
    }


def test_quota_is_inactive_without_subscription_and_resets_in_fixed_window() -> None:
    """The quota docs require a subscription key and document fixed renewal windows.

    https://learn.microsoft.com/en-us/azure/api-management/quota-policy
    """
    policy = _doc('<quota calls="1" renewal-period="300" />')
    store: dict[str, Any] = {}
    current = [1_000.0]
    runtime = PolicyRuntime(clock=lambda: current[0])
    variables = {"subscription_id": "sub-1", "quota_store": store}

    assert apply_inbound([policy], _request(variables=variables), runtime) is None
    assert apply_inbound([policy], _request(variables=variables), runtime) is not None
    current[0] = 1_300.0
    assert apply_inbound([policy], _request(variables=variables), runtime) is None

    no_subscription = {"subscription_id": "", "quota_store": store}
    assert apply_inbound([policy], _request(variables=no_subscription), runtime) is None


def test_quota_nested_operation_limit_and_bandwidth_message() -> None:
    """The quota docs define independent API/operation limits and bandwidth errors.

    https://learn.microsoft.com/en-us/azure/api-management/quota-policy
    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    policy = _doc(
        '<quota bandwidth="1" calls="10" renewal-period="300">'
        '<api id="orders" bandwidth="1" calls="10" renewal-period="300">'
        '<operation id="get-order" bandwidth="1" calls="1" renewal-period="300" />'
        "</api></quota>"
    )
    store: dict[str, Any] = {}
    variables = {
        "subscription_id": "sub-1",
        "api_id": "orders",
        "operation_id": "get-order",
        "quota_store": store,
    }
    runtime = _runtime(1_000.0)

    assert apply_inbound([policy], _request(variables=variables, body=b"x"), runtime) is None
    req = _request(variables=variables, body=b"x", response_status_code=200, response_body=b"y" * 1025)
    finalize_deferred_actions(req, runtime)

    blocked = apply_inbound([policy], _request(variables=variables), runtime)
    assert blocked is not None
    assert blocked.status_code == 403
    assert _json_body(blocked)["message"].startswith("Out of bandwidth quota.")


def test_quota_by_key_evaluates_success_condition_and_uses_quota_error_shape() -> None:
    """The quota-by-key docs define response-conditioned increments and 403 Retry-After.

    https://learn.microsoft.com/en-us/azure/api-management/quota-by-key-policy
    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    policy = _doc(
        '<quota-by-key calls="1" renewal-period="300" counter-key="client" '
        'increment-condition="@(context.Response.StatusCode >= 200 &amp;&amp; context.Response.StatusCode &lt; 400)" />'
    )
    store: dict[str, Any] = {}
    variables = {"quota_store": store}
    runtime = _runtime(1_000.0)

    first = _request(variables=variables)
    assert apply_inbound([policy], first, runtime) is None
    first.response_status_code = 500
    finalize_deferred_actions(first, runtime)
    assert store["quota-by-key:client"]["count"] == 0

    second = _request(variables=variables)
    assert apply_inbound([policy], second, runtime) is None
    second.response_status_code = 200
    finalize_deferred_actions(second, runtime)

    blocked = apply_inbound([policy], _request(variables=variables), runtime)
    assert blocked is not None
    assert blocked.status_code == 403
    # Windows are anchored at the default first-period-start (0001-01-01T00:00:00Z),
    # so at t=1000 the current 300 s window began at 900 and resets at 1200.
    assert blocked.headers["retry-after"] == "200"
    assert _json_body(blocked)["message"] == "Out of call volume quota. Quota will be replenished in 00:03:20."


def test_quota_by_key_accepts_first_period_start_in_apim_format() -> None:
    """The quota-by-key docs define first-period-start as an ISO-8601 UTC timestamp.

    https://learn.microsoft.com/en-us/azure/api-management/quota-by-key-policy
    """
    fixed_now = datetime(2026, 4, 2, 10, 1, 0, tzinfo=UTC).timestamp()
    policy = _doc(
        '<quota-by-key calls="1" renewal-period="300" counter-key="client" first-period-start="2026-04-02T10:00:00Z" />'
    )
    store: dict[str, Any] = {}
    variables = {"quota_store": store}
    runtime = _runtime(fixed_now)

    assert apply_inbound([policy], _request(variables=variables), runtime) is None
    blocked = apply_inbound([policy], _request(variables=variables), runtime)
    assert blocked is not None
    assert blocked.headers["retry-after"] == "240"


def test_quota_accepts_short_renewal_periods() -> None:
    """quota documents no minimum renewal-period; only quota-by-key has the 300 s floor.

    https://learn.microsoft.com/en-us/azure/api-management/quota-policy
    """
    doc = _doc('<quota calls="1" renewal-period="60" />')

    assert doc.inbound[0].renewal_period == 60


def test_quota_message_uses_timespan_day_prefix_past_24_hours() -> None:
    """The replenish interval is a .NET TimeSpan, which gains a day part past 24 hours.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies
    """
    doc = _doc('<quota calls="1" renewal-period="604800" />')
    variables = {"subscription_id": "sub1", "quota_store": {}}
    runtime = _runtime(1_000_000.0)

    assert apply_inbound([doc], _request(variables=variables), runtime) is None
    blocked = apply_inbound([doc], _request(variables=variables), runtime)

    assert blocked is not None
    assert blocked.status_code == 403
    assert blocked.headers["retry-after"] == "604800"
    assert _json_body(blocked) == {
        "statusCode": 403,
        "message": "Out of call volume quota. Quota will be replenished in 7.00:00:00.",
    }


def test_quota_by_key_shared_key_increments_once_per_request() -> None:
    """Several policies sharing a counter-key increment it only once per request.

    https://learn.microsoft.com/en-us/azure/api-management/quota-by-key-policy
    """
    policy = '<quota-by-key calls="5" renewal-period="300" counter-key="shared" />'
    store: dict[str, Any] = {}
    request = _request(variables={"quota_store": store})

    assert apply_inbound([_doc(policy), _doc(policy)], request, _runtime(1_000_000.0)) is None

    assert store["quota-by-key:shared"]["count"] == 1


def _throttled_gateway(*, product_policy: str | None, api_policies: dict[str, str]):
    import httpx
    from fastapi.testclient import TestClient

    from app.config import (
        ApiConfig,
        GatewayConfig,
        OperationConfig,
        ProductConfig,
        Subscription,
        SubscriptionConfig,
        SubscriptionKeyPair,
    )
    from app.main import create_app

    def wrap(inner: str) -> str:
        return f"<policies><inbound><base />{inner}</inbound><backend><base /></backend><outbound><base /></outbound></policies>"

    config = GatewayConfig(
        allow_anonymous=True,
        products={
            "p": ProductConfig(
                name="p", require_subscription=True, policies_xml=wrap(product_policy) if product_policy else None
            )
        },
        subscription=SubscriptionConfig(
            required=True,
            subscriptions={
                "s1": Subscription(
                    id="s1", name="s1", keys=SubscriptionKeyPair(primary="k1", secondary="k2"), products=["p"]
                )
            },
        ),
        apis={
            api_id: ApiConfig(
                name=api_id,
                path=api_id,
                upstream_base_url="http://backend.test",
                products=["p"],
                policies_xml=wrap(policy),
                operations={"op": OperationConfig(name="op", method="GET", url_template="/x")},
            )
            for api_id, policy in api_policies.items()
        },
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    return TestClient(create_app(config=config, http_client=httpx.AsyncClient(transport=transport)))


def test_product_and_api_rate_limits_count_independently() -> None:
    """Product and API call rate limits are applied independently.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    """
    limit = '<rate-limit calls="3" renewal-period="60" />'
    with _throttled_gateway(product_policy=limit, api_policies={"a": limit}) as client:
        statuses = [client.get("/a/x", headers={"Ocp-Apim-Subscription-Key": "k1"}).status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 429]


def test_the_same_api_rate_limit_counts_separately_per_api() -> None:
    """Each API's rate-limit keeps its own per-subscription counter.

    https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy
    """
    limit = '<rate-limit calls="2" renewal-period="60" />'
    with _throttled_gateway(product_policy=None, api_policies={"a": limit, "b": limit}) as client:
        statuses = [
            client.get(path, headers={"Ocp-Apim-Subscription-Key": "k1"}).status_code
            for path in ("/a/x", "/b/x", "/a/x", "/b/x", "/a/x")
        ]

    assert statuses == [200, 200, 200, 200, 429]
