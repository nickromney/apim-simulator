"""Identical deterministic policy fixtures for the local and Azure gateways."""

from __future__ import annotations

import secrets
from html import escape
from pathlib import Path

CORE_FRAGMENTS = Path(__file__).resolve().parents[1] / "policy-fragments" / "fragments"
# core-content-safety and core-pii call Azure AI services; this lab has none.
CORE_FRAGMENT_IDS = ("core-rate-limit", "core-token-budget-check", "core-token-budget-record")
CORE_NAMED_VALUES = {
    "core-rate-limit-enabled": "true",
    "core-rate-limit-default-calls": "60",
    "core-rate-limit-default-period": "60",
    "core-token-budget-enabled": "true",
    "core-token-budget-default-limit": "100000",
    "core-token-budget-default-period": "day",
}


def policy(inbound: str) -> str:
    # No inherited production policies: only the dedicated parity API is affected.
    return f"<policies><inbound>{inbound}</inbound><backend /><outbound /><on-error /></policies>"


def response(body: str, status: int = 200, header: str = "") -> str:
    return (
        f'<return-response><set-status code="{status}" reason="Fixture" />{header}'
        '<set-header name="Content-Type" exists-action="override"><value>text/plain</value></set-header>'
        f"<set-body>{escape(body)}</set-body></return-response>"
    )


def capture(expression: str, before: str = "") -> str:
    return policy(
        before
        + f'<set-variable name="value" value="{escape(expression, quote=True)}" />'
        + response('@((string)context.Variables["value"])')
    )


def fixtures(prefix: str, run: str | None = None) -> tuple[dict, list[dict]]:
    rows = [
        ("literal", "GET", "/literal", policy(response("hello")), {}, "hello"),
        (
            "request-header",
            "GET",
            "/request-header",
            capture('@(context.Request.Headers.GetValueOrDefault("X-Fixture", "missing"))'),
            {"headers": {"X-Fixture": "hello"}},
            "hello",
        ),
        (
            "missing-header",
            "GET",
            "/missing-header",
            capture('@(context.Request.Headers.GetValueOrDefault("X-Fixture", "missing"))'),
            {},
            "missing",
        ),
        (
            "query",
            "GET",
            "/query",
            capture('@(context.Request.Url.Query.GetValueOrDefault("q", "none"))'),
            {"query": {"q": "hello"}},
            "hello",
        ),
        (
            "query-override",
            "GET",
            "/query-override",
            capture(
                '@(context.Request.Url.Query.GetValueOrDefault("q", "none"))',
                '<set-query-parameter name="q" exists-action="override"><value>changed</value></set-query-parameter>',
            ),
            {"query": {"q": "original"}},
            "changed",
        ),
        (
            "header-override",
            "GET",
            "/header-override",
            capture(
                '@(context.Request.Headers.GetValueOrDefault("X-Fixture", "missing"))',
                '<set-header name="X-Fixture" exists-action="override"><value>changed</value></set-header>',
            ),
            {"headers": {"X-Fixture": "original"}},
            "changed",
        ),
        (
            "header-delete",
            "GET",
            "/header-delete",
            capture(
                '@(context.Request.Headers.GetValueOrDefault("X-Fixture", "missing"))',
                '<set-header name="X-Fixture" exists-action="delete" />',
            ),
            {"headers": {"X-Fixture": "original"}},
            "missing",
        ),
        (
            "path-parameter",
            "GET",
            "/path/{id}",
            capture('@(context.Request.MatchedParameters["id"])'),
            {"path_suffix": "/path/42"},
            "42",
        ),
        (
            "body-echo",
            "POST",
            "/body",
            capture("@(context.Request.Body.As<string>(preserveContent: true))"),
            {"body_text": '{"message":"hello"}', "headers": {"Content-Type": "application/json"}},
            '{"message":"hello"}',
        ),
        (
            "body-transform",
            "POST",
            "/body-transform",
            capture("@(context.Request.Body.As<string>(preserveContent: true))", "<set-body>transformed</set-body>"),
            {"body_text": "original"},
            "transformed",
        ),
        ("method", "POST", "/method", capture("@(context.Request.Method)"), {"body_text": ""}, "POST"),
        (
            "set-method",
            "GET",
            "/set-method",
            capture("@(context.Request.Method)", "<set-method>POST</set-method>"),
            {},
            "POST",
        ),
        (
            "variable",
            "GET",
            "/variable",
            policy(
                '<set-variable name="greeting" value="hello" />' + response('@((string)context.Variables["greeting"])')
            ),
            {},
            "hello",
        ),
        (
            "choose-match",
            "GET",
            "/choose-match",
            policy(
                '<choose><when condition="@(context.Request.Headers.GetValueOrDefault(&quot;X-Fixture&quot;, &quot;&quot;) == &quot;yes&quot;)">'
                + response("matched")
                + "</when><otherwise>"
                + response("fallback")
                + "</otherwise></choose>"
            ),
            {"headers": {"X-Fixture": "yes"}},
            "matched",
        ),
        (
            "choose-fallback",
            "GET",
            "/choose-fallback",
            policy(
                '<choose><when condition="@(context.Request.Headers.GetValueOrDefault(&quot;X-Fixture&quot;, &quot;&quot;) == &quot;yes&quot;)">'
                + response("matched")
                + "</when><otherwise>"
                + response("fallback")
                + "</otherwise></choose>"
            ),
            {},
            "fallback",
        ),
        (
            "response-header",
            "GET",
            "/response-header",
            policy(
                response(
                    "hello",
                    header='<set-header name="X-Parity" exists-action="override"><value>verified</value></set-header>',
                )
            ),
            {"compare_headers": ["X-Parity", "Content-Type"]},
            "hello",
        ),
        (
            "custom-status",
            "GET",
            "/custom-status",
            policy(response("created", status=201)),
            {"expected_status": 201},
            "created",
        ),
    ]
    fragment_id = prefix + "-fragment"
    named_id = prefix + "-greeting"
    rows.extend(
        [
            ("named-value", "GET", "/named-value", policy(response("{{" + named_id + "}}")), {}, "configured greeting"),
            (
                "fragment",
                "GET",
                "/fragment",
                capture(
                    '@(context.Request.Headers.GetValueOrDefault("X-Fragment", "missing"))',
                    f'<include-fragment fragment-id="{fragment_id}" />',
                ),
                {},
                "included",
            ),
            (
                "json-body",
                "POST",
                "/json-body",
                capture('@((string)context.Request.Body.As<JObject>(preserveContent: true)["message"])'),
                {"body_text": '{"message":"hello"}', "headers": {"Content-Type": "application/json"}},
                "hello",
            ),
            (
                "regex",
                "GET",
                "/regex",
                capture('@(Regex.Replace(context.Request.Headers.GetValueOrDefault("X-Fixture", ""), "[0-9]", ""))'),
                {"headers": {"X-Fixture": "a1b2"}},
                "ab",
            ),
            (
                "check-header",
                "GET",
                "/check-header",
                policy(
                    '<check-header name="X-Required" failed-check-httpcode="403" failed-check-error-message="Required header" ignore-case="false"><value>ok</value></check-header>'
                    + response("accepted")
                ),
                {"headers": {"X-Required": "ok"}},
                "accepted",
            ),
            (
                "on-error",
                "GET",
                "/on-error",
                '<policies><inbound><check-header name="X-Required" failed-check-httpcode="403" failed-check-error-message="Required header" ignore-case="false"><value>ok</value></check-header></inbound><backend /><outbound /><on-error>'
                + response("@(context.LastError.Reason)", status=409)
                + "</on-error></policies>",
                {"expected_status": 409},
                "HeaderNotFound",
            ),
        ]
    )
    operations = {}
    cases = []
    for name, method, path, xml, arguments, expected in rows:
        arguments = dict(arguments)
        suffix = arguments.pop("path_suffix", path)
        operations[name] = {"name": name, "method": method, "url_template": path, "policies_xml": xml}
        cases.append(
            {
                "name": name,
                "method": method,
                "path": f"/{prefix}{suffix}",
                "expected_status": 200,
                "expected_body": expected,
                **arguments,
            }
        )
    config = {
        "allow_anonymous": True,
        "subscription": {"required": True},
        "apis": {
            prefix: {
                "name": "APIM simulator parity fixtures",
                "path": prefix,
                "upstream_base_url": "https://example.invalid",
                "operations": operations,
            }
        },
    }
    config["named_values"] = {named_id: {"display_name": named_id, "value": "configured greeting"}}
    config["policy_fragments"] = {
        fragment_id: '<fragment><set-header name="X-Fragment" exists-action="override"><value>included</value></set-header></fragment>'
    }
    _add_core_fragment_cases(prefix, run or secrets.token_hex(4), config, cases)
    return config, cases


def _core_fragments(prefix: str) -> tuple[dict[str, str], dict[str, dict]]:
    """The shipped core fragments, renamed into this run's namespace.

    Fragment and named-value IDs carry the run prefix so the lab never touches
    an existing core-* resource in the target APIM.
    """
    fragments = {}
    for fragment_id in CORE_FRAGMENT_IDS:
        xml = (CORE_FRAGMENTS / f"{fragment_id}.xml").read_text(encoding="utf-8").strip()
        fragments[f"{prefix}-{fragment_id}"] = xml.replace("{{core-", "{{" + prefix + "-core-")
    named_values = {
        f"{prefix}-{name}": {"display_name": f"{prefix}-{name}", "value": value}
        for name, value in CORE_NAMED_VALUES.items()
    }
    return fragments, named_values


def _add_core_fragment_cases(prefix: str, run: str, config: dict, cases: list[dict]) -> None:
    """Replay examples/policy-fragments against both gateways.

    Counters and budgets key on the X-Parity-Run header, so each run starts from
    empty counters on both gateways. The token-budget record fragment needs a
    model response in outbound, which this backend-free lab cannot produce;
    its C# expressions are replayed against the request body instead.
    """
    fragments, named_values = _core_fragments(prefix)
    config["policy_fragments"].update(fragments)
    config["named_values"].update(named_values)
    operations = config["apis"][prefix]["operations"]
    run_header = {"X-Parity-Run": run}
    key = "@(&quot;{scope}:&quot; + context.Request.Headers.GetValueOrDefault(&quot;X-Parity-Run&quot;, &quot;&quot;))"

    def include(name: str) -> str:
        return f'<include-fragment fragment-id="{prefix}-{name}" />'

    def operation(name: str, method: str, inbound: str) -> None:
        operations[name] = {"name": name, "method": method, "url_template": "/" + name, "policies_xml": policy(inbound)}

    def case(name: str, operation_name: str, **fields: object) -> None:
        method = operations[operation_name]["method"]
        headers = {**run_header, **fields.pop("headers", {})}
        cases.append(
            {"name": name, "method": method, "path": f"/{prefix}/{operation_name}", "headers": headers, **fields}
        )

    budget = (
        '<set-variable name="core-token-budget-limit" value="@(100)" />'
        f'<set-variable name="core-token-budget-key" value="{key.format(scope="budget")}" />'
    )
    operation(
        "core-rate-limit",
        "GET",
        '<set-variable name="core-rate-limit-calls" value="@(3)" />'
        f'<set-variable name="core-rate-limit-key" value="{key.format(scope="rate")}" />'
        + include("core-rate-limit")
        + response("allowed"),
    )
    operation(
        "core-rate-limit-off",
        "GET",
        '<set-variable name="core-rate-limit-enabled" value="false" />'
        '<set-variable name="core-rate-limit-calls" value="@(1)" />'
        f'<set-variable name="core-rate-limit-key" value="{key.format(scope="rate")}" />'
        + include("core-rate-limit")
        + response("allowed"),
    )
    operation(
        "core-token-budget",
        "POST",
        budget
        + include("core-token-budget-check")
        + response('@("used:" + Convert.ToString(context.Variables["core-token-budget-used"]))'),
    )
    operation(
        "core-token-budget-seed",
        "POST",
        budget
        + include("core-token-budget-check")
        + '<cache-store-value key="@((string)context.Variables[&quot;core-token-budget-cache-key&quot;])" '
        'value="@(100)" duration="@((int)context.Variables[&quot;core-token-budget-ttl&quot;])" />'
        + response("seeded"),
    )
    operation(
        "core-token-budget-off",
        "POST",
        '<set-variable name="core-token-budget-enabled" value="false" />'
        + budget
        + include("core-token-budget-check")
        + response("skipped"),
    )

    remaining = "x-ratelimit-remaining"
    for index, left in enumerate(["2", "1", "0"], start=1):
        case(
            f"core-rate-limit-{index}",
            "core-rate-limit",
            expected_status=200,
            expected_body="allowed",
            expected_headers={remaining: left},
        )
    # The 429 message names the seconds until renewal, which depends on timing.
    case("core-rate-limit-exceeded", "core-rate-limit", expected_status=429, compare_body=False)
    case(
        "core-rate-limit-off",
        "core-rate-limit-off",
        expected_status=200,
        expected_body="allowed",
        expected_headers={remaining: ""},
    )
    case("core-token-budget-unspent", "core-token-budget", expected_status=200, expected_body="used:0", body_text="")
    case("core-token-budget-seed", "core-token-budget-seed", expected_status=200, expected_body="seeded", body_text="")
    case(
        "core-token-budget-exhausted",
        "core-token-budget",
        expected_status=403,
        expected_body={"statusCode": 403, "message": "Token budget exhausted for this period."},
        expected_headers={"x-token-budget-remaining": "0"},
        body_text="",
    )
    case("core-token-budget-off", "core-token-budget-off", expected_status=200, expected_body="skipped", body_text="")

    usage = escape(
        '@{ var usage = context.Request.Body.As<JObject>(preserveContent: true)["usage"];'
        ' if (usage["total_tokens"] != null) { return usage["total_tokens"].Value<int>().ToString(); }'
        ' return ((usage["input_tokens"]?.Value<int>() ?? 0) + (usage["output_tokens"]?.Value<int>() ?? 0)).ToString(); }',
        quote=True,
    )
    operation(
        "core-usage",
        "POST",
        f'<set-variable name="value" value="{usage}" />' + response('@((string)context.Variables["value"])'),
    )
    json_headers = {"Content-Type": "application/json"}
    for name, body, expected in [
        ("core-usage-total", '{"usage":{"total_tokens":60}}', "60"),
        ("core-usage-input-output", '{"usage":{"input_tokens":30,"output_tokens":25}}', "55"),
        ("core-usage-missing-output", '{"usage":{"input_tokens":30}}', "30"),
    ]:
        case(name, "core-usage", expected_status=200, expected_body=expected, body_text=body, headers=json_headers)

    for name, expression, expected in [
        ("core-subscription-coalesce", '@(context.Subscription?.Id ?? "none")', prefix),
        ("core-variable-default-null", '@((string)(context.Variables.GetValueOrDefault("absent") ?? "null"))', "null"),
        (
            "core-variable-default-value",
            '@(Convert.ToString(context.Variables.GetValueOrDefault("absent", "{{'
            + prefix
            + '-core-token-budget-default-period}}")))',
            "day",
        ),
        (
            "core-convert-math",
            '@(Math.Max(0, Convert.ToInt32("40") - 60).ToString() + ":" + Convert.ToBoolean("True").ToString())',
            "0:True",
        ),
        (
            "core-equals-ignore-case",
            '@("MiXed".Equals("mixed", StringComparison.OrdinalIgnoreCase).ToString())',
            "True",
        ),
    ]:
        operations[name] = {
            "name": name,
            "method": "GET",
            "url_template": "/" + name,
            "policies_xml": capture(expression),
        }
        case(name, name, expected_status=200, expected_body=expected)
