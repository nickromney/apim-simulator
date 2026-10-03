"""Identical deterministic policy fixtures for the local and Azure gateways."""

from __future__ import annotations

from html import escape


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


def fixtures(prefix: str) -> tuple[dict, list[dict]]:
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
    return config, cases
