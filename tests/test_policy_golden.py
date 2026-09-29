from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.config import GatewayConfig, NamedValueConfig
from app.policy import (
    CacheLookup,
    CacheLookupValue,
    CacheRemoveValue,
    CacheStore,
    CacheStoreValue,
    PolicyRequest,
    PolicyRuntime,
    QuotaByKey,
    RateLimitByKey,
    apply_inbound,
    apply_on_error,
    apply_outbound,
    parse_policies_xml,
)


@pytest.mark.contract("POLICY-SET-HEADER")
def test_golden_policy_set_header_override() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <set-header name="x-a" exists-action="override"><value>1</value></set-header>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={}, headers={"x-a": "0"}, variables={})
    early = apply_inbound([doc], req)
    assert early is None
    assert req.headers["x-a"] == "1"


@pytest.mark.contract("POLICY-RETURN-RESPONSE")
def test_golden_policy_return_response() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <return-response>
      <set-status code="401" reason="no" />
      <set-header name="content-type" exists-action="override"><value>text/plain</value></set-header>
      <body>deny</body>
    </return-response>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={}, headers={}, variables={})
    early = apply_inbound([doc], req)
    assert early is not None
    assert early.status_code == 401
    assert early.headers["content-type"] == "text/plain"
    assert early.body == b"deny"


def test_golden_policy_choose_when() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <choose>
      <when condition="query('mode') == 'debug'">
        <set-header name="x-mode" exists-action="override"><value>debug</value></set-header>
      </when>
      <otherwise>
        <set-header name="x-mode" exists-action="override"><value>normal</value></set-header>
      </otherwise>
    </choose>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={"mode": "debug"}, headers={}, variables={})
    early = apply_inbound([doc], req)
    assert early is None
    assert req.headers["x-mode"] == "debug"


def test_golden_policy_on_error_return_response() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound />
  <backend />
  <outbound />
  <on-error>
    <return-response>
      <set-status code="502" reason="bad" />
      <set-header name="content-type" exists-action="override"><value>text/plain</value></set-header>
      <body>backend-down</body>
    </return-response>
  </on-error>
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={}, headers={}, variables={"error": "x"})
    out = apply_on_error([doc], req)
    assert out is not None
    assert out.status_code == 502
    assert out.body == b"backend-down"


def test_golden_policy_outbound_set_header() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound />
  <backend />
  <outbound>
    <set-header name="x-out" exists-action="override"><value>1</value></set-header>
  </outbound>
  <on-error />
</policies>
"""
    )
    headers: dict[str, str] = {}
    apply_outbound([doc], headers=headers)
    assert headers["x-out"] == "1"


def test_golden_policy_check_header_denies_when_missing() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <check-header name="x-required" failed-check-httpcode="401" failed-check-error-message="nope" ignore-case="false" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    early = apply_inbound([doc], req)
    assert early is not None
    assert early.status_code == 401
    assert early.body == b'{"statusCode":401,"message":"nope"}'


def test_golden_policy_ip_filter_denies() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <ip-filter action="allow">
      <address>10.0.0.1</address>
    </ip-filter>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={"client_ip": "10.0.0.2"})
    early = apply_inbound([doc], req)
    assert early is not None
    assert early.status_code == 403


def test_golden_policy_rate_limit_enforces_429() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <rate-limit calls="1" renewal-period="300" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    store: dict[str, object] = {}
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers={},
        variables={
            "route": "r1",
            "subscription_id": "sub1",
            "products": [],
            "client_ip": "10.0.0.1",
            "rate_limit_store": store,
        },
    )
    assert apply_inbound([doc], req) is None
    early = apply_inbound([doc], req)
    assert early is not None
    assert early.status_code == 429


def test_golden_policy_quota_enforces_403() -> None:
    """https://learn.microsoft.com/en-us/azure/api-management/quota-policy: over quota is 403 Forbidden."""
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <quota calls="1" renewal-period="999999" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    store: dict[str, object] = {}
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers={},
        variables={
            "route": "r1",
            "subscription_id": "sub1",
            "products": [],
            "client_ip": "10.0.0.1",
            "quota_store": store,
        },
    )
    assert apply_inbound([doc], req) is None
    early = apply_inbound([doc], req)
    assert early is not None
    assert early.status_code == 403


def test_golden_policy_set_variable_renders_into_later_policy_values() -> None:
    """APIM policy expressions, not simulator-only token syntax, read request data.

    https://learn.microsoft.com/en-us/azure/api-management/set-body-policy
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <set-variable name="mode" value='@(context.Request.Url.Query.GetValueOrDefault("mode", ""))' />
    <set-header name="x-mode" exists-action="override"><value>@(context.Variables.GetValueOrDefault("mode", ""))</value></set-header>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={"mode": "debug"}, headers={}, variables={})
    early = apply_inbound([doc], req)
    assert early is None
    assert req.variables["mode"] == "debug"
    assert req.headers["x-mode"] == "debug"


def test_golden_policy_set_query_parameter_mutates_upstream_query_only() -> None:
    """APIM request path access uses a policy expression.

    https://learn.microsoft.com/en-us/azure/api-management/set-body-policy
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <set-query-parameter name="source" exists-action="override">
      <value>@(context.Request.Url.Path)</value>
    </set-query-parameter>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={}, headers={}, variables={})
    early = apply_inbound([doc], req)
    assert early is None
    assert req.query["source"] == "/api/health"


def test_golden_policy_set_body_keeps_literal_braces_literal() -> None:
    """Literal set-body text is not an undocumented token template.

    https://learn.microsoft.com/en-us/azure/api-management/set-body-policy
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <set-body>{"path":"{path}","subscription":"{subscription_id}"}</set-body>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(
        method="POST", path="/api/items", query={}, headers={}, variables={"subscription_id": "sub-1"}, body=b"original"
    )
    early = apply_inbound([doc], req)
    assert early is None
    assert req.body == b'{"path":"{path}","subscription":"{subscription_id}"}'


def test_golden_policy_return_response_supports_set_body_template() -> None:
    """Policy expressions provide variable access in literal policy text.

    https://learn.microsoft.com/en-us/azure/api-management/set-body-policy
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <set-variable name="mode" value='@(context.Request.Url.Query.GetValueOrDefault("mode", ""))' />
    <return-response>
      <set-status code="200" reason="ok" />
      <set-header name="content-type" exists-action="override"><value>application/json</value></set-header>
      <set-body>@("{\\&quot;mode\\&quot;:\\&quot;" + context.Variables.GetValueOrDefault("mode", "") + "\\&quot;}")</set-body>
    </return-response>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={"mode": "trace"}, headers={}, variables={})
    early = apply_inbound([doc], req)
    assert early is not None
    assert early.status_code == 200
    assert early.body == b'{"mode":"trace"}'


@pytest.mark.contract("POLICY-INCLUDE-FRAGMENT")
def test_golden_policy_include_fragment_inserts_fragment_nodes() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <include-fragment fragment-id="common-header" />
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
""",
        policy_fragments={
            "common-header": """
<fragment>
  <set-header name="x-fragment" exists-action="override"><value>1</value></set-header>
</fragment>
"""
        },
    )
    req = PolicyRequest(method="GET", path="/api/health", query={}, headers={}, variables={})
    early = apply_inbound([doc], req)
    assert early is None
    assert req.headers["x-fragment"] == "1"


def test_golden_policy_named_values_resolve_before_template_tokens() -> None:
    """Named values are resolved in policy values before policy expressions run.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <set-header name="x-backend" exists-action="override"><value>@("https://{{backend-host}}" + context.Request.Url.Path)</value></set-header>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    req = PolicyRequest(method="GET", path="/api/health", query={}, headers={}, variables={})
    runtime = PolicyRuntime(
        gateway_config=GatewayConfig(named_values={"backend-host": NamedValueConfig(value="backend.example.test")})
    )

    early = apply_inbound([doc], req, runtime=runtime)

    assert early is None
    assert req.headers["x-backend"] == "https://backend.example.test/api/health"


def test_golden_policy_named_values_resolve_in_attribute_values() -> None:
    """Named values are substituted in policy attributes before execution.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <set-header name="{{header-name}}" exists-action="override"><value>enabled</value></set-header>
    <set-query-parameter name="{{query-name}}" exists-action="override"><value>yes</value></set-query-parameter>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
""",
        gateway_config=GatewayConfig(
            named_values={
                "header-name": NamedValueConfig(value="x-feature"),
                "query-name": NamedValueConfig(value="feature"),
            }
        ),
    )
    req = PolicyRequest(method="GET", path="/api/health", query={}, headers={}, variables={})

    early = apply_inbound([doc], req)

    assert early is None
    assert req.headers["x-feature"] == "enabled"
    assert req.query["feature"] == "yes"


def test_golden_policy_named_value_expansion_is_single_pass() -> None:
    """Named values cannot contain and expand another named value.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties
    """
    cfg = GatewayConfig(
        named_values={
            "outer": NamedValueConfig(value="{{inner}}"),
            "inner": NamedValueConfig(value="resolved"),
        }
    )
    doc = parse_policies_xml(
        '<policies><inbound><set-header name="x-value"><value>{{outer}}</value></set-header></inbound></policies>',
        gateway_config=cfg,
    )
    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})

    apply_inbound([doc], req, runtime=PolicyRuntime(gateway_config=cfg))

    assert req.headers["x-value"] == "{{inner}}"


def test_golden_policy_parses_policy_parity_v2_nodes() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <rate-limit-by-key calls="10" renewal-period="60" counter-key="user-a" />
    <quota-by-key calls="100" renewal-period="300" counter-key="user-a" first-period-start="2026-04-02T10:00:00Z" />
    <cache-lookup vary-by-developer="true" vary-by-developer-groups="false" caching-type="internal">
      <vary-by-query-parameter>version;locale</vary-by-query-parameter>
    </cache-lookup>
    <cache-lookup-value key="token-user-a" variable-name="tokenstate" default-value="missing" />
    <cache-remove-value key="token-user-a" />
  </inbound>
  <backend />
  <outbound>
    <cache-store duration="60" />
    <cache-store-value key="token-user-a" value="warm" duration="60" />
  </outbound>
  <on-error />
</policies>
"""
    )

    assert isinstance(doc.inbound[0], RateLimitByKey)
    assert isinstance(doc.inbound[1], QuotaByKey)
    assert isinstance(doc.inbound[2], CacheLookup)
    assert isinstance(doc.inbound[3], CacheLookupValue)
    assert isinstance(doc.inbound[4], CacheRemoveValue)
    assert isinstance(doc.outbound[0], CacheStore)
    assert isinstance(doc.outbound[1], CacheStoreValue)


@pytest.mark.parametrize("attribute", ["vary-by-developer", "vary-by-developer-groups"])
def test_cache_lookup_requires_developer_variation_attributes(attribute: str) -> None:
    """The cache-lookup developer variation attributes are required by APIM.

    https://learn.microsoft.com/en-us/azure/api-management/cache-lookup-policy
    """
    other = "vary-by-developer-groups" if attribute == "vary-by-developer" else "vary-by-developer"
    with pytest.raises(HTTPException, match=f"cache-lookup requires {attribute}"):
        parse_policies_xml(
            f"""\
<policies>
  <inbound><cache-lookup {other}="false" /></inbound>
</policies>
"""
        )


@pytest.mark.parametrize(
    ("element", "attribute", "value", "message"),
    [
        ("cache-lookup-value", "caching-type", "unknown", "Unsupported caching-type unknown"),
        ("cache-store-value", "caching-type", "unknown", "Unsupported caching-type unknown"),
        ("cache-remove-value", "caching-type", "unknown", "Unsupported caching-type unknown"),
        ("cache-lookup", "downstream-caching-type", "unknown", "Unsupported downstream-caching-type unknown"),
    ],
)
def test_cache_policies_reject_unknown_enum_values(element: str, attribute: str, value: str, message: str) -> None:
    """Cache policy enum attributes accept only the documented values.

    https://learn.microsoft.com/en-us/azure/api-management/cache-lookup-policy
    https://learn.microsoft.com/en-us/azure/api-management/cache-lookup-value-policy
    https://learn.microsoft.com/en-us/azure/api-management/cache-store-value-policy
    https://learn.microsoft.com/en-us/azure/api-management/cache-remove-value-policy
    """
    if element == "cache-lookup":
        attrs = f'{attribute}="{value}" vary-by-developer="false" vary-by-developer-groups="false"'
    elif element == "cache-lookup-value":
        attrs = f'{attribute}="{value}" key="key" variable-name="value"'
    elif element == "cache-store-value":
        attrs = f'{attribute}="{value}" key="key" value="value" duration="60"'
    else:
        attrs = f'{attribute}="{value}" key="key"'
    with pytest.raises(HTTPException, match=message):
        parse_policies_xml(f"<policies><inbound><{element} {attrs} /></inbound></policies>")


def test_cache_remove_value_honours_fail_on_removal_error() -> None:
    """cache-remove-value can fail the request when cache removal fails.

    https://learn.microsoft.com/en-us/azure/api-management/cache-remove-value-policy
    """

    class FailingCache(dict[str, object]):
        def pop(self, key: str, default: object = None) -> object:
            raise RuntimeError(f"cannot remove {key}")

    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={})
    runtime = PolicyRuntime(value_cache=FailingCache())
    with pytest.raises(RuntimeError, match="cannot remove key"):
        CacheRemoveValue(key="key", fail_on_cache_removal_error="true").apply(req, runtime)
    assert CacheRemoveValue(key="key", fail_on_cache_removal_error="false").apply(req, runtime) is None


def test_cache_store_value_duration_is_seconds_and_expires_at_boundary() -> None:
    """cache-store-value duration is measured in seconds.

    https://learn.microsoft.com/en-us/azure/api-management/cache-store-value-policy
    https://learn.microsoft.com/en-us/azure/api-management/cache-lookup-value-policy
    """
    now = [100.0]
    runtime = PolicyRuntime(value_cache={}, clock=lambda: now[0])
    store_request = PolicyRequest(method="POST", path="/", query={}, headers={}, variables={})
    CacheStoreValue(key="key", value="value", duration="2", caching_type="internal").apply(store_request, runtime)

    now[0] = 101.0
    hit_request = PolicyRequest(method="POST", path="/", query={}, headers={}, variables={})
    CacheLookupValue(key="key", variable_name="cached", caching_type="internal").apply(hit_request, runtime)
    assert hit_request.variables["cached"] == "value"

    now[0] = 102.0
    expired_request = PolicyRequest(method="POST", path="/", query={}, headers={}, variables={})
    CacheLookupValue(key="key", variable_name="cached", caching_type="internal").apply(expired_request, runtime)
    assert "cached" not in expired_request.variables


@pytest.mark.contract("POLICY-EMIT-METRIC")
def test_golden_policy_emit_metric_uses_dimensions_and_value() -> None:
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <emit-metric name="requests-by-team" namespace="internal" value="2">
      <dimension name="API ID" />
      <dimension name="team" value="alpha" />
    </emit-metric>
  </inbound>
  <backend />
  <outbound />
  <on-error />
</policies>
"""
    )
    emitted: list[tuple[int, dict[str, str]]] = []
    runtime = PolicyRuntime(custom_metric_emitter=lambda amount, attributes: emitted.append((amount, attributes)))
    req = PolicyRequest(
        method="GET",
        path="/api/health",
        query={},
        headers={},
        variables={"api_id": "demo-api"},
    )
    assert apply_inbound([doc], req, runtime) is None
    assert emitted == [
        (
            2,
            {
                "apim.metric.name": "requests-by-team",
                "apim.metric.namespace": "internal",
                "apim.metric.dimension.API ID": "demo-api",
                "apim.metric.dimension.team": "alpha",
            },
        )
    ]


@pytest.mark.contract("POLICY-EMIT-METRIC")
def test_golden_policy_emit_metric_requires_name_and_dimension() -> None:
    """emit-metric requires a name, dimensions, and values for custom dimensions.

    https://learn.microsoft.com/en-us/azure/api-management/emit-metric-policy
    """
    with pytest.raises(HTTPException):
        parse_policies_xml("<policies><inbound><emit-metric name='x' /></inbound></policies>")
    with pytest.raises(HTTPException):
        parse_policies_xml("<policies><inbound><emit-metric><dimension name='d' /></emit-metric></inbound></policies>")


def test_emit_metric_uses_double_values_including_zero() -> None:
    """emit-metric value is a double and zero is still emitted.

    https://learn.microsoft.com/en-us/azure/api-management/emit-metric-policy
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <emit-metric name="fractional" value="0.5">
      <dimension name="API ID" />
    </emit-metric>
    <emit-metric name="zero" value="0">
      <dimension name="API ID" />
    </emit-metric>
  </inbound>
</policies>
"""
    )
    emitted: list[tuple[float, dict[str, str]]] = []
    runtime = PolicyRuntime(custom_metric_emitter=lambda amount, attributes: emitted.append((amount, attributes)))
    req = PolicyRequest(method="GET", path="/", query={}, headers={}, variables={"api_id": "demo-api"})

    assert apply_inbound([doc], req, runtime) is None
    assert [amount for amount, _ in emitted] == [0.5, 0.0]


def test_emit_metric_enforces_five_custom_dimensions_and_evaluates_names() -> None:
    """emit-metric supports expression dimension names and caps configured dimensions at five.

    https://learn.microsoft.com/en-us/azure/api-management/emit-metric-policy
    """
    doc = parse_policies_xml(
        """\
<policies>
  <inbound>
    <emit-metric name="named">
      <dimension name='@(context.Variables["dimension_name"])' value="value" />
    </emit-metric>
  </inbound>
</policies>
"""
    )
    emitted: list[tuple[float, dict[str, str]]] = []
    runtime = PolicyRuntime(custom_metric_emitter=lambda amount, attributes: emitted.append((amount, attributes)))
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers={},
        variables={"api_id": "demo-api", "dimension_name": "dynamic-name"},
    )

    assert apply_inbound([doc], req, runtime) is None
    assert emitted[0][1]["apim.metric.dimension.dynamic-name"] == "value"

    dimensions = "".join(f'<dimension name="d{i}" value="{i}" />' for i in range(6))
    with pytest.raises(HTTPException):
        parse_policies_xml(f'<policies><inbound><emit-metric name="x">{dimensions}</emit-metric></inbound></policies>')


def test_emit_metric_populates_default_dimensions_from_request_context() -> None:
    """emit-metric resolves all documented default dimensions from request context.

    https://learn.microsoft.com/en-us/azure/api-management/emit-metric-policy
    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    doc = parse_policies_xml(
        """\
<policies>
  <outbound>
    <emit-metric name="request">
      <dimension name="API ID" />
      <dimension name="Operation ID" />
      <dimension name="Product ID" />
      <dimension name="User ID" />
      <dimension name="Subscription ID" />
    </emit-metric>
    <emit-metric name="deployment">
      <dimension name="Location" />
      <dimension name="Gateway ID" />
      <dimension name="Backend ID" />
    </emit-metric>
  </outbound>
</policies>
"""
    )
    emitted: list[tuple[float, dict[str, str]]] = []
    runtime = PolicyRuntime(custom_metric_emitter=lambda amount, attributes: emitted.append((amount, attributes)))
    req = PolicyRequest(
        method="GET",
        path="/",
        query={},
        headers={},
        variables={
            "api_id": "demo-api",
            "operation_id": "get",
            "product_id": "demo-product",
            "user_id": "demo-user",
            "subscription_id": "demo-subscription",
            "location": "local",
            "gateway_id": "local",
            "backend_id": "demo-backend",
        },
    )
    req.section = "outbound"

    assert apply_outbound([doc], headers={}, variables=req.variables, runtime=runtime) is None
    assert emitted[0][1] == {
        "apim.metric.name": "request",
        "apim.metric.namespace": "API Management",
        "apim.metric.dimension.API ID": "demo-api",
        "apim.metric.dimension.Operation ID": "get",
        "apim.metric.dimension.Product ID": "demo-product",
        "apim.metric.dimension.User ID": "demo-user",
        "apim.metric.dimension.Subscription ID": "demo-subscription",
    }
    assert emitted[1][1] == {
        "apim.metric.name": "deployment",
        "apim.metric.namespace": "API Management",
        "apim.metric.dimension.Location": "local",
        "apim.metric.dimension.Gateway ID": "local",
        "apim.metric.dimension.Backend ID": "demo-backend",
    }
