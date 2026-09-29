from __future__ import annotations

import pytest

from app.policy import PolicyRequest, evaluate_policy_value


def test_apim_expression_can_read_response_status_and_headers() -> None:
    req = PolicyRequest(
        method="GET",
        path="/api/items",
        query={"mode": "debug"},
        headers={},
        variables={
            "client_ip": "10.1.2.3",
            "_request_headers": {"x-key": "demo"},
            "_request_query": {"mode": "debug"},
        },
        response_status_code=202,
        response_headers={"cache-control": "public"},
    )

    assert evaluate_policy_value("@(context.Response.StatusCode == 202)", req) is True
    assert evaluate_policy_value('@(context.Response.Headers.GetValueOrDefault("Cache-Control",""))', req) == "public"
    assert evaluate_policy_value("@(context.Request.IpAddress)", req) == "10.1.2.3"


def test_expression_condition_rejects_non_boolean_results() -> None:
    """C# if and choose conditions must evaluate to Boolean.

    https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/statements/selection-statements
    """
    from app.policy import ExpressionCondition

    req = PolicyRequest(method="GET", path="/api/items", query={}, headers={}, variables={})
    with pytest.raises(ValueError, match="Boolean"):
        ExpressionCondition("@(1)").__call__(req)
