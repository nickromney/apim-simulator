"""Execute source-guide examples, including documented metadata and bodies."""

from __future__ import annotations

import base64
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient

from app.apim_expr import build_expression_context, evaluate_apim_expression
from app.config import (
    ApiConfig,
    GatewayConfig,
    NamedValueConfig,
    OperationConfig,
    RouteConfig,
    ServiceMetadataConfig,
    Subscription,
    SubscriptionConfig,
    SubscriptionKeyPair,
    TenantAccessConfig,
    UserConfig,
)
from app.main import create_app
from app.policy import PolicyRequest

FIXTURES = Path("tests/fixtures/policy_guides")
BASE64 = """@{
  string[] value;
  if (context.Request.Headers.TryGetValue("Authorization", out value))
  {
      if(value != null && value.Length > 0)
      {
          return Encoding.UTF8.GetString(Convert.FromBase64String(value[0]));
      }
  }
  return null;
}"""


def _evaluate(expr, **kwargs):
    return evaluate_apim_expression(
        expr,
        build_expression_context(
            PolicyRequest(
                method="GET",
                path="/demo",
                query=kwargs.pop("query", {}),
                headers=kwargs.pop("headers", {}),
                variables=kwargs.pop("variables", {}),
                **kwargs,
            )
        ),
    )


@pytest.mark.parametrize(
    ("expression", "result"), [("@(true)", True), ("@((1+1).ToString())", "2"), ('@("Hi There".Length)', 8)]
)
def test_source_expression_syntax(expression, result):
    assert _evaluate(expression) == result


def test_source_regex_and_max_age_default():
    expression = '@(Regex.Match(context.Response.Headers.GetValueOrDefault("Cache-Control",""), @"max-age=(?<maxAge>\\d+)").Groups["maxAge"]?.Value)'
    assert _evaluate(expression, response_headers={"Cache-Control": "public, max-age=600"}) == "600"
    assert _evaluate(expression, response_headers={}) == ""
    conditional = '@(context.Variables.ContainsKey("maxAge") ? int.Parse((string)context.Variables["maxAge"]) : 3600)'
    assert _evaluate(conditional) == 3600
    assert _evaluate(conditional, variables={"maxAge": "600"}) == 600


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [("${word}-$1-$$-$&", "ab-ab-$-ab"), ("$9", "$9"), (r"\n", r"\n")],
)
def test_regex_replace_uses_dotnet_substitutions(replacement, expected):
    assert (
        _evaluate(
            '@(Regex.Replace("ab", "(?<word>ab)", (string)context.Variables["replacement"]))',
            variables={"replacement": replacement},
        )
        == expected
    )


def test_source_multi_statement_out_parameter_and_named_body_argument():
    assert _evaluate(BASE64, headers={"Authorization": base64.b64encode("hello £".encode()).decode()}) == "hello £"
    assert _evaluate(BASE64) is None
    assert _evaluate("@(context.Request.Body.As<string>(preserveContent: true))", body=b"body") == "body"
    assert _evaluate("@(context.RequestId.ToString())", variables={"request_id": "request-1"}) == "request-1"
    assert len(_evaluate("@(Guid.NewGuid().ToString())")) == 36


def _client(cfg, calls):
    def backend(req):
        calls.append(req)
        return httpx.Response(200, json={"public": "visible", "private": "secret"})

    return TestClient(create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend))))


def test_documented_fragment_user_region_update_all_callers_and_delete_constraint():
    fragment = (FIXTURES / "policy-fragments/example-01.xml").read_text()
    # Local fragment persistence stores the children of the documented fragment root.
    fragment = fragment.removeprefix("<fragment>").strip().removesuffix("</fragment>").strip()
    xml = '<policies><inbound><include-fragment fragment-id="context" /><base /></inbound><backend><base /></backend><outbound><base /></outbound></policies>'
    cfg = GatewayConfig(
        allow_anonymous=True,
        tenant_access=TenantAccessConfig(enabled=True, primary_key="operator"),
        service=ServiceMetadataConfig(region="local-region"),
        users={"alice": UserConfig(id="alice")},
        subscription=SubscriptionConfig(
            subscriptions={
                "demo": Subscription(
                    id="demo",
                    name="demo",
                    keys=SubscriptionKeyPair(primary="demo", secondary="second"),
                    all_apis=True,
                    created_by="alice",
                )
            }
        ),
        policy_fragments={"context": fragment},
        apis={
            name: ApiConfig(
                name=name,
                path=name,
                upstream_base_url="http://backend",
                policies_xml=xml,
                operations={"get": OperationConfig(name="get", method="GET", url_template="/")},
            )
            for name in ["first", "second"]
        },
    )
    calls = []
    with _client(cfg, calls) as client:
        for name in ["first", "second"]:
            assert client.get("/" + name, headers={"Ocp-Apim-Subscription-Key": "demo"}).status_code == 200
        assert [r.headers["x-request-context-data"] for r in calls] == ["alice,local-region"] * 2
        key = {"X-Apim-Tenant-Key": "operator"}
        update = client.put(
            "/apim/management/policy-fragments/context",
            headers=key,
            json={"xml": '<set-header name="x-request-context-data"><value>updated</value></set-header>'},
        )
        assert update.status_code == 200, update.text
        for name in ["first", "second"]:
            assert client.get("/" + name, headers={"Ocp-Apim-Subscription-Key": "demo"}).status_code == 200
        assert [r.headers["x-request-context-data"] for r in calls[-2:]] == ["updated"] * 2
        assert client.delete("/apim/management/policy-fragments/context", headers=key).status_code == 400


def test_named_values_source_samples_and_secret_masking():
    samples = [
        (FIXTURES / f"api-management-howto-properties/example-{number:02}.xml").read_text() for number in [6, 8, 9]
    ]
    samples[1] = samples[1].replace("CustomHeader", "ExpressionHeader")
    samples[2] = samples[2].replace("CustomHeader", "EncodedHeader")
    cfg = GatewayConfig(
        allow_anonymous=True,
        named_values={
            "ContosoHeader": NamedValueConfig(value="TrackingId"),
            "ContosoHeaderValue": NamedValueConfig(value="private-value", secret=True),
            "ExpressionProperty": NamedValueConfig(value="@(DateTime.Now.ToString())"),
            "ContosoHeaderValue2": NamedValueConfig(value="This is a header value."),
        },
        routes=[
            RouteConfig(
                name="named",
                path_prefix="/named",
                upstream_base_url="http://backend",
                policies_xml="<policies><inbound>"
                + "".join(samples)
                + "</inbound><backend><forward-request /></backend></policies>",
            )
        ],
    )
    calls = []
    with _client(cfg, calls) as client:
        response = client.get("/named")
        assert response.status_code == 200, response.text
        listed = client.get("/apim/management/named-values").text
        assert "private-value" not in listed
    assert calls[0].headers["TrackingId"] == "private-value"
    assert calls[0].headers["EncodedHeader"] == "The URL encoded value is This+is+a+header+value."
    assert calls[0].headers["ExpressionHeader"]


def test_exact_error_sample_exposes_failed_policy_context():
    source = (FIXTURES / "api-management-error-handling-policies/example-02.xml").read_text()
    source = source.replace(
        "<base />",
        '<check-header id="required" name="required" failed-check-httpcode="403" failed-check-error-message="missing" ignore-case="false" />',
        1,
    )
    cfg = GatewayConfig(
        allow_anonymous=True,
        routes=[
            RouteConfig(name="errors", path_prefix="/errors", upstream_base_url="http://backend", policies_xml=source)
        ],
    )
    calls = []
    with _client(cfg, calls) as client:
        response = client.get("/errors")
    assert response.status_code == 403
    assert response.headers["ErrorSource"] == "check-header"
    assert response.headers["ErrorReason"] == "HeaderNotFound"
    assert response.headers["ErrorPolicyId"] == "required"
    assert response.headers["ErrorStatusCode"] == "403"
    assert response.headers["ErrorSection"] == "inbound"
    assert not calls


def test_role_filter_prompt_outcome_uses_policy_expression():
    # The authoring guide supplies prose rather than XML; this authored policy
    # demonstrates its role-dependent response filtering outcome locally.
    expr = '@{ if (context.Request.Headers.GetValueOrDefault("Role", "") == "admin") { return context.Response.Body.As<string>(preserveContent: true); } return "{\\"public\\":\\"visible\\"}"; }'
    policy = (
        "<policies><backend><forward-request /></backend><outbound><set-body>"
        + escape(expr)
        + "</set-body></outbound></policies>"
    )
    cfg = GatewayConfig(
        allow_anonymous=True,
        routes=[RouteConfig(name="role", path_prefix="/role", upstream_base_url="http://backend", policies_xml=policy)],
    )
    with _client(cfg, []) as client:
        assert client.get("/role", headers={"Role": "admin"}).json() == {"public": "visible", "private": "secret"}
        assert client.get("/role", headers={"Role": "reader"}).json() == {"public": "visible"}


@pytest.mark.parametrize(
    "xml",
    [
        "<base />",
        '<include-fragment fragment-id="other" />',
        "<inbound />",
        '<choose><when condition="@(true)"><base /></when></choose>',
        "<set-body>" + ("x" * (512 * 1024)) + "</set-body>",
    ],
)
def test_unused_fragment_rejects_documented_authoring_constraints(xml):
    cfg = GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="operator"))
    with _client(cfg, []) as client:
        response = client.put(
            "/apim/management/policy-fragments/invalid", headers={"X-Apim-Tenant-Key": "operator"}, json={"xml": xml}
        )
    assert response.status_code == 400


def test_five_guide_journey_helper_contract(monkeypatch):
    import importlib.util
    import json

    monkeypatch.setenv("APIM_TENANT_KEY", "operator")
    spec = importlib.util.spec_from_file_location(
        "expressions_graphql", "examples/apim-policies/expressions_graphql.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def backend(request):
        path = request.url.path
        if path.startswith("/api/comment/"):
            return httpx.Response(
                200, json={"id": path.rsplit("/", 1)[-1], "text": "Local comment", "private": "hidden"}
            )
        if path.startswith("/api/blog/"):
            return httpx.Response(200, json=[{"id": "7", "text": "Local comment"}])
        if path.startswith("/api/blog-record/"):
            return httpx.Response(200, json={"id": path.rsplit("/", 1)[-1], "title": "Local blog"})
        return httpx.Response(200, json={"headers": dict(request.headers), "body": request.content.decode()})

    cfg = GatewayConfig(allow_anonymous=True, tenant_access=TenantAccessConfig(enabled=True, primary_key="operator"))
    with TestClient(
        create_app(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    ) as client:
        result = module.run(client)
    assert set(result) == {"expressions", "fragments", "named_values", "errors", "graphql"}
    assert result["graphql"]["parent"]
    assert json.dumps(result)


def test_expression_namespaces_cannot_expose_python_runtime_internals():
    with pytest.raises(ValueError, match="Private expression members"):
        _evaluate("@(context.Request.ToHttpMessage.__globals__)")


def test_standalone_on_error_status_body_and_response_transform():
    xml = '<policies><inbound><check-header name="required" failed-check-httpcode="403" failed-check-error-message="missing" ignore-case="false" /></inbound><on-error><set-status code="429" reason="Too Many Requests" /><set-body>slow down</set-body><find-and-replace from="slow" to="retry" /><set-header name="handled"><value>true</value></set-header></on-error></policies>'
    cfg = GatewayConfig(
        allow_anonymous=True,
        routes=[
            RouteConfig(name="errors", path_prefix="/errors", upstream_base_url="http://backend", policies_xml=xml)
        ],
    )
    with _client(cfg, []) as client:
        response = client.get("/errors")
    assert response.status_code == 429
    assert response.text == "retry down"
    assert response.headers["handled"] == "true"


def test_documented_send_request_query_array_last_value():
    assert (
        _evaluate('@(context.Request.Url.Query["fromDate"].Last())', query={"fromDate": ["first", "second"]})
        == "second"
    )


def test_source_send_request_composition_json_constructors():
    import json

    from defusedxml import ElementTree

    from app.apim_expr import CalloutResponse

    source = (FIXTURES / "api-management-sample-send-request/example-09.xml").read_text()
    # Escape only the source fence's unescaped generic type markup.
    expression = ElementTree.fromstring(source.replace("<JObject>", "&lt;JObject&gt;")).find("set-body").text
    variables = {
        name: CalloutResponse(status_code=200, headers={}, content=json.dumps({"name": name}).encode())
        for name in ["revenuedata", "materialdata", "throughputdata", "accidentdata"]
    }
    assert json.loads(_evaluate(expression, variables=variables)) == {name: {"name": name} for name in variables}
