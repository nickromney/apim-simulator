"""Emit deterministic behavior evidence for policy-expression optimizations.

Run from the repository root with ``uv run --extra dev python -m
scripts.golden_policy_outputs --output /private/tmp/apim-golden.json``.
The JSON intentionally retains exception messages, policy order, and bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

import httpx

from app.apim_expr import build_expression_context, evaluate_apim_expression
from app.config import GatewayConfig
from app.policy import (
    PolicyRequest,
    PolicyRuntime,
    PolicyTraceCollector,
    apply_inbound_async,
    apply_outbound_async,
    parse_policies_xml,
)

FIXED_TIME = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
FIXED_GUID = uuid.UUID("00000000-0000-4000-8000-000000000001")
MULTISTATEMENT = """@{
    string[] value;
    if (context.Request.Headers.TryGetValue("Authorization", out value)) {
        if (value != null && value.Length > 0) { return value[0].ToUpper(); }
    }
    return null;
}"""


def _request(value: str = "alpha") -> PolicyRequest:
    return PolicyRequest(
        method="POST",
        path=f"/api/{value}",
        query={"mode": value, "repeated": ["first", value]},
        headers={"X-Value": value, "Authorization": value},
        variables={"key": value, "request_id": "request-1", "_matched_parameters": {"id": value}},
        body=b'{"message":"request"}',
        response_status_code=201,
        response_headers={"X-Response": value},
        response_body=b'{"message":"response"}',
    )


def _evaluate(expression: str, request: PolicyRequest) -> dict[str, Any]:
    try:
        value = evaluate_apim_expression(expression, build_expression_context(request))
        return {"value": value, "type": type(value).__name__}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}


def _expression_outputs() -> list[dict[str, Any]]:
    expressions = [
        '@(context.Request.Headers.GetValueOrDefault("x-value", "missing"))',
        '@(context.Request.Url.Query["repeated"].Last())',
        '@(context.Request.MatchedParameters.GetValueOrDefault("id", ""))',
        '@(context.Variables.GetValueOrDefault("key", ""))',
        '@(context.Response.Headers.GetValueOrDefault("x-response", ""))',
        '@(context.Request.Url.Query.GetValueOrDefault("mode", "") == "alpha" ? "yes" : "no")',
        '@($"{context.Request.Method}:{context.Request.Url.Path}:{context.Response.StatusCode}")',
        '@(Regex.Replace("abc123", @"(?<letters>[a-z]+)([0-9]+)", "${letters}-$2"))',
        "@(-7 / 2)",
        "@(7.0 / 2)",
        "@(Guid.NewGuid().ToString())",
        "@(DateTime.UtcNow.ToString())",
        MULTISTATEMENT,
    ]
    outputs = [
        {"expression": expression, "request": value, **_evaluate(expression, _request(value))}
        for value in ("alpha", "beta", "alpha")
        for expression in expressions
    ]
    scopes_and_errors = [
        "@{ var scoped = 7; return scoped; }",
        "@(scoped)",
        "@{ var other = 8; return scoped; }",
        "@{ var scoped = 9; var other = 0; return scoped; }",
        "@{ var scoped = 11; return scoped; }",
        "@(context.Request.ToHttpMessage.__globals__)",
        "@(context.__class__)",
        "@(unknown)",
        "@(1 / 0)",
        "@{ var value = 1; }",
        "@(1 +)",
    ]
    outputs.extend({"expression": expression, **_evaluate(expression, _request())} for expression in scopes_and_errors)
    return outputs


def _body_outputs() -> list[dict[str, Any]]:
    outputs = []
    for target in ("Request", "Response"):
        request = _request()
        expressions = [
            f"@(context.{target}.Body.As<string>(preserveContent: true))",
            f"@(context.{target}.Body.As<JObject>(preserveContent: true))",
            f"@(context.{target}.Body.As<string>())",
            f"@(context.{target}.Body.As<string>())",
        ]
        for expression in expressions:
            outputs.append(
                {
                    "expression": expression,
                    **_evaluate(expression, request),
                    "request_body": request.body.decode(),
                    "response_body": request.response_body.decode(),
                }
            )
    return outputs


async def _callout_output(traced: bool) -> dict[str, Any]:
    calls = []

    def backend(request: httpx.Request) -> httpx.Response:
        calls.append({"method": request.method, "url": str(request.url), "body": request.content.decode()})
        return httpx.Response(200, json={"orders": 3})

    xml = """<policies><inbound>
      <set-variable name="mode" value='@(context.Request.Url.Query.GetValueOrDefault("mode", ""))' />
      <send-request mode="new" response-variable-name="orders">
        <set-url>https://example.test/orders</set-url><set-method>POST</set-method>
        <set-body>@(context.Request.Body.As&lt;string&gt;(preserveContent: true))</set-body>
      </send-request>
    </inbound><backend /><outbound>
      <set-body>@(((IResponse)context.Variables["orders"]).Body.As&lt;string&gt;())</set-body>
      <set-header name="x-mode"><value>@(context.Variables["mode"])</value></set-header>
    </outbound><on-error /></policies>"""
    document = parse_policies_xml(xml)
    trace = PolicyTraceCollector() if traced else None
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as client:
        runtime = PolicyRuntime(gateway_config=GatewayConfig(allow_anonymous=True), http_client=client, trace=trace)
        request = _request()
        await apply_inbound_async([document], request, runtime)
        request.section = "outbound"
        await apply_outbound_async([document], request, runtime)
        return {
            "traced": traced,
            "calls": calls,
            "request_body": request.body.decode(),
            "response_body": request.response_body.decode(),
            "response_headers": dict(request.response_headers or {}),
            "remaining_callout_body": request.variables["orders"].Body.AsString(),
            "trace": asdict(trace) if trace else None,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON here instead of stdout")
    args = parser.parse_args()
    with (
        patch("app.apim_expr.uuid.uuid4", return_value=FIXED_GUID),
        patch("app.apim_expr._DateTimeNamespace.UtcNow", new_callable=PropertyMock, return_value=FIXED_TIME),
    ):
        payload = {
            "expressions": _expression_outputs(),
            "bodies": _body_outputs(),
            "callouts": [asyncio.run(_callout_output(traced)) for traced in (False, True)],
        }
    output = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
