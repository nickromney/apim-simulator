"""Unit tests for the APIM expression evaluator.

The gateway reaches `app.apim_expr` through policy documents, which exercises
the translation table only where some policy happens to use it. These drive the
module directly: one case per translation rule, per validation refusal, and per
branch of the request normaliser.
"""

from __future__ import annotations

import pytest

from app.apim_expr import (
    CalloutBody,
    CalloutResponse,
    ExpressionMap,
    JwtValue,
    _strip_outer_expression,
    build_expression_context,
    evaluate_apim_expression,
    is_apim_expression,
    split_last,
)
from app.policy import PolicyRequest


def _request(**overrides: object) -> PolicyRequest:
    fields: dict[str, object] = {
        "method": "GET",
        "path": "/api/items",
        "query": {"mode": "debug"},
        "headers": {"x-key": "demo"},
        "variables": {},
    }
    fields.update(overrides)
    return PolicyRequest(**fields)  # type: ignore[arg-type]


def _context(**overrides: object):
    return build_expression_context(_request(**overrides))


def _evaluate(expression: str, **overrides: object):
    return evaluate_apim_expression(expression, _context(**overrides))


# --- ExpressionMap ---------------------------------------------------------


def test_expression_map_matches_a_key_regardless_of_case() -> None:
    headers = ExpressionMap({"Cache-Control": "public"})
    assert headers["cache-control"] == "public"
    assert headers["CACHE-CONTROL"] == "public"
    assert headers["Cache-Control"] == "public"


def test_expression_map_stringifies_keys_it_is_built_from() -> None:
    assert ExpressionMap({1: "one"})["1"] == "one"


def test_expression_map_raises_for_a_key_it_does_not_hold() -> None:
    with pytest.raises(KeyError) as raised:
        ExpressionMap({"a": 1})["b"]
    assert raised.value.args[0] == "b"


def test_expression_map_get_falls_back_to_the_default() -> None:
    headers = ExpressionMap({"a": 1})
    assert headers.get("missing") is None
    assert headers.get("missing", "fallback") == "fallback"
    assert headers.GetValueOrDefault("missing") == ""
    assert headers.GetValueOrDefault("missing", "fallback") == "fallback"
    assert headers.GetValueOrDefault("A") == 1


# --- callout response ------------------------------------------------------


def test_callout_body_reads_a_json_object() -> None:
    assert CalloutBody(b'{"a": 1}').AsJObject() == {"a": 1}


def test_callout_body_that_is_not_a_json_object_raises() -> None:
    """IMessageBody.As<JObject> rejects invalid JSON.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    with pytest.raises(ValueError, match="body is not valid JSON"):
        CalloutBody(b"not json").AsJObject()
    with pytest.raises(ValueError, match="body is not valid JSON"):
        CalloutBody(b"[1, 2]").AsJObject()
    with pytest.raises(ValueError, match="body is not valid JSON"):
        CalloutBody(b"\xff\xfe").AsJObject()


def test_callout_body_reads_as_string_and_replaces_undecodable_bytes() -> None:
    # Accepted equivalents: AsJObject__mutmut_6, AsString__mutmut_3 and
    # AsString__mutmut_6 survive. They spell the encoding "UTF-8" or leave it to
    # the default, and Python resolves all three to the same codec.
    assert CalloutBody(b"hello").AsString() == "hello"
    assert CalloutBody(b"\xff").AsString() == "�"


def test_callout_response_exposes_status_headers_and_reason() -> None:
    response = CalloutResponse(status_code=201, headers={"X-A": "1"}, content=b'{"b": 2}')
    assert response.StatusCode == 201
    assert response.Headers.GetValueOrDefault("x-a") == "1"
    assert response.Body.AsJObject() == {"b": 2}
    assert response.ReasonPhrase == ""
    assert CalloutResponse(status_code=404, headers={}, content=b"", reason="Not Found").ReasonPhrase == "Not Found"


# --- JwtValue --------------------------------------------------------------


def test_jwt_value_exposes_claims_as_lists_of_strings() -> None:
    jwt = JwtValue({"sub": "user-1", "iss": "https://issuer", "roles": ["a", "b"], "n": 7}, "raw-token")
    assert jwt.Raw == "raw-token"
    assert jwt.Subject == "user-1"
    assert jwt.Issuer == "https://issuer"
    assert jwt.Claims["roles"] == ["a", "b"]
    assert jwt.Claims["n"] == ["7"]


def test_jwt_value_drops_null_claims() -> None:
    assert JwtValue({"sub": "s", "empty": None}, "t").Claims.get("empty") is None


def test_jwt_value_normalises_every_shape_of_audience() -> None:
    assert JwtValue({"aud": ["a", "b"]}, "t").Audiences == ["a", "b"]
    assert JwtValue({"aud": "a"}, "t").Audiences == ["a"]
    assert JwtValue({}, "t").Audiences == []


def test_jwt_value_without_subject_or_issuer_reads_as_empty_strings() -> None:
    jwt = JwtValue({}, "t")
    assert jwt.Subject == ""
    assert jwt.Issuer == ""


# --- request normalisation -------------------------------------------------


def test_forwarded_host_wins_over_incoming_host() -> None:
    context = _context(variables={"forwarded_host": "fwd.example", "incoming_host": "in.example"})
    assert context.request.original_host == "fwd.example"


def test_incoming_host_is_used_when_no_forwarded_host_is_set() -> None:
    assert _context(variables={"incoming_host": "in.example"}).request.original_host == "in.example"


def test_original_host_takes_the_first_forwarded_entry_and_strips_the_port() -> None:
    context = _context(variables={"forwarded_host": " first.example:8443 , second.example:80 , third.example "})
    assert context.request.original_host == "first.example"


def test_only_the_last_colon_of_a_host_is_treated_as_the_port() -> None:
    # Accepted equivalents: _normalize_request__mutmut_16 and __mutmut_19 change
    # the maxsplit of the comma split. Only element [0] is ever read, so no
    # forwarded-host value can tell the three spellings apart.
    assert _context(variables={"incoming_host": "a:b:8080"}).request.original_host == "a:b"


def test_a_bracketed_ipv6_host_keeps_its_colons() -> None:
    assert _context(variables={"incoming_host": "[::1]"}).request.original_host == "[::1]"


def test_no_host_variable_reads_as_an_empty_host() -> None:
    assert _context().request.original_host == ""


def test_headers_and_query_fall_back_to_the_request_when_no_override_is_set() -> None:
    context = _context()
    assert context.request.headers_get("X-Key") == "demo"
    assert context.request.query_get("Mode") == "debug"


def test_a_non_dict_header_override_falls_back_to_the_request_headers() -> None:
    context = _context(variables={"_request_headers": "not a dict", "_request_query": ["not a dict"]})
    assert context.request.headers_get("x-key") == "demo"
    assert context.request.query_get("mode") == "debug"


def test_header_and_query_overrides_are_preferred_when_they_are_dicts() -> None:
    context = _context(variables={"_request_headers": {"x-key": "override"}, "_request_query": {"mode": "override"}})
    assert context.request.headers_get("x-key") == "override"
    assert context.request.query_get("mode") == "override"


def test_missing_header_and_query_keys_read_as_the_default() -> None:
    context = _context()
    assert context.request.headers_get("absent") == ""
    assert context.request.headers_get("absent", "fallback") == "fallback"
    assert context.request.query_get("absent") == ""
    assert context.request.query_get("absent", "fallback") == "fallback"


def test_context_carries_method_path_ip_and_subscription() -> None:
    context = _context(variables={"client_ip": "10.1.2.3", "subscription_id": "sub-1"})
    assert context.request.method == "GET"
    assert context.request.path == "/api/items"
    assert context.request.ip_address == "10.1.2.3"
    assert context.subscription.id == "sub-1"


def test_an_absent_ip_or_subscription_reads_as_an_empty_string() -> None:
    context = _context()
    assert context.request.ip_address == ""
    assert context.subscription.id == ""


def test_response_status_defaults_to_zero_and_headers_default_to_empty() -> None:
    context = _context()
    assert context.response.status_code == 0
    assert context.response.headers_get("any") == ""


def test_response_headers_come_from_the_variable_when_the_request_has_none() -> None:
    context = _context(variables={"_response_headers": {"cache-control": "public"}})
    assert context.response.headers_get("Cache-Control") == "public"


def test_response_headers_on_the_request_win_over_the_variable() -> None:
    context = _context(
        response_headers={"cache-control": "private"},
        variables={"_response_headers": {"cache-control": "public"}},
    )
    assert context.response.headers_get("cache-control") == "private"


def test_variables_are_readable_by_name_and_by_lookup() -> None:
    context = _context(variables={"tier": "gold"})
    assert context.variables["tier"] == "gold"
    assert context.variables_get("tier") == "gold"
    assert context.variables_get("absent") == ""
    assert context.variables_get("absent", "fallback") == "fallback"


# --- split_last ------------------------------------------------------------


def test_split_last_returns_the_final_segment() -> None:
    assert split_last("a/b/c", "/") == "c"
    assert split_last("nosep", "/") == "nosep"
    assert split_last(123, "/") == "123"


# --- expression envelope ---------------------------------------------------


def test_is_apim_expression_only_matches_the_two_apim_envelopes() -> None:
    assert is_apim_expression("@(1)") is True
    assert is_apim_expression("  @{1}  ") is True
    assert is_apim_expression("@1") is False
    assert is_apim_expression("plain") is False
    assert is_apim_expression("(1)") is False


def test_both_envelopes_and_a_bare_expression_evaluate_the_same() -> None:
    assert _evaluate("@(1 + 1)") == 2
    assert _evaluate("@{1 + 1}") == 2
    assert _evaluate("  @( 1 + 1 )  ") == 2
    assert _evaluate("1 + 1") == 2


# --- translation table -----------------------------------------------------


def test_casts_are_dropped() -> None:
    assert _evaluate('@((string)"x")') == "x"
    assert _evaluate("@((bool)True)") is True


def test_dotnet_booleans_translate_in_any_case() -> None:
    # Accepted equivalents: _translate_expression__mutmut_17 and __mutmut_31
    # upper-case the literal inside the pattern. The substitution is compiled
    # with re.IGNORECASE, so the pattern's own case cannot matter.
    assert _evaluate("@(true)") is True
    assert _evaluate("@(TRUE)") is True
    assert _evaluate("@(false)") is False
    assert _evaluate("@(FALSE)") is False


def test_logical_operators_translate() -> None:
    assert _evaluate("@(true && false)") is False
    assert _evaluate("@(true || false)") is True
    assert _evaluate("@(!false)") is True
    assert _evaluate('@("a" != "b")') is True
    assert _evaluate('@("a" == "a")') is True


# Accepted equivalent: _translate_expression__mutmut_56 removes the "!=" spacing
# replace and survives. The spacing only changes what the negation rewrite sees
# for a "!" written directly after an "=", and every such expression -- "a !=!b"
# translates to "a != not b" -- is a syntax error either way.


def test_operator_translation_does_not_rewrite_string_literals() -> None:
    """C# operators are syntax, not replacements inside string literals.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    assert _evaluate('@("true")') == "true"
    assert _evaluate('@("a && b || !c != d")') == "a && b || !c != d"


def test_multi_statement_expression_supports_declaration_assignment_and_return() -> None:
    """C# policy expressions support local declarations, assignment, and return.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    assert _evaluate('@{ string value = "a"; value = value + "b"; return value; }') == "ab"


def test_multi_statement_expression_supports_if_else() -> None:
    """C# if/else statements select one Boolean-controlled branch.

    https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/statements/selection-statements
    """
    assert _evaluate('@{ var value = 2; if (value > 1) { return "yes"; } else { return "no"; } }') == "yes"


def test_csharp_string_methods_and_ternary_are_supported() -> None:
    """C# string members and the conditional operator follow documented semantics.

    https://learn.microsoft.com/en-us/dotnet/api/system.string.contains
    https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/operators/conditional-operator
    """
    assert _evaluate('@("Abc".Contains("b"))') is True
    assert _evaluate('@("Abc".Contains("B"))') is False
    assert _evaluate('@("Abc".Equals("Abc"))') is True
    assert _evaluate('@("Abc".Length)') == 3
    assert _evaluate('@("abc".StartsWith("a") ? "yes" : "no")') == "yes"
    assert _evaluate('@("abc".EndsWith("c"))') is True
    assert _evaluate('@("abc".ToUpper())') == "ABC"
    assert _evaluate('@("ABC".ToLower())') == "abc"
    assert _evaluate('@("  abc  ".Trim())') == "abc"


def test_dictionary_contains_key_and_generic_calls_are_supported() -> None:
    """Allowed dictionary and generic context members remain callable.

    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    https://learn.microsoft.com/en-us/dotnet/api/system.collections.generic.dictionary-2.containskey
    """
    assert (
        _evaluate(
            '@(context.Variables.ContainsKey("tier") ? '
            'context.Variables.GetValueOrDefault<string>("tier", "fallback") : "missing")',
            variables={"tier": "gold"},
        )
        == "gold"
    )
    context = _context()
    context.variables["resp"] = CalloutResponse(status_code=200, headers={}, content=b'{"a": 1}')
    assert evaluate_apim_expression('@(context.Variables["resp"].Body.As<JObject>()["a"])', context) == 1


def test_csharp_integer_division_and_to_string_are_preserved() -> None:
    """C# integer division truncates toward zero and ToString returns text.

    https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/operators/arithmetic-operators
    https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions
    """
    assert _evaluate("@(5 / 2)") == 2
    assert _evaluate("@(-5 / 2)") == -2
    assert _evaluate("@(5.ToString())") == "5"


def test_request_accessors_translate() -> None:
    variables = {"client_ip": "10.1.2.3", "incoming_host": "api.example:443"}
    assert _evaluate('@(context.Request.Headers.GetValueOrDefault("X-Key",""))', variables=variables) == "demo"
    assert _evaluate('@(context.Request.Url.Query.GetValueOrDefault("mode",""))', variables=variables) == "debug"
    assert _evaluate("@(context.Request.OriginalUrl.Host)", variables=variables) == "api.example"
    assert _evaluate("@(context.Request.IpAddress)", variables=variables) == "10.1.2.3"
    assert _evaluate("@(context.Request.Url.Path)", variables=variables) == "/api/items"
    assert _evaluate("@(context.Request.Method)", variables=variables) == "GET"


def test_response_and_subscription_accessors_translate() -> None:
    assert (
        _evaluate(
            '@(context.Response.Headers.GetValueOrDefault("Cache-Control",""))',
            response_headers={"cache-control": "public"},
        )
        == "public"
    )
    assert _evaluate("@(context.Response.StatusCode)", response_status_code=202) == 202
    assert _evaluate("@(context.Subscription.Id)", variables={"subscription_id": "sub-1"}) == "sub-1"


def test_variable_accessors_translate() -> None:
    assert _evaluate('@(context.Variables.GetValueOrDefault("tier",""))', variables={"tier": "gold"}) == "gold"
    assert _evaluate('@(context.Variables["tier"])', variables={"tier": "gold"}) == "gold"


def test_string_methods_translate() -> None:
    assert _evaluate('@("a,b".Split(",").Last())') == "b"
    assert _evaluate('@("abc".StartsWith("a"))') is True
    assert _evaluate('@("  x  ".Trim())') == "x"
    assert _evaluate("@((1).ToString())") == "1"


def test_body_accessors_translate() -> None:
    context = _context()
    body = CalloutResponse(status_code=200, headers={}, content=b'{"a": 1}')
    context.variables["resp"] = body
    assert evaluate_apim_expression('@(context.Variables["resp"].Body.As<JObject>()["a"])', context) == 1
    assert evaluate_apim_expression('@(context.Variables["resp"].Body.As<string>())', context) == '{"a": 1}'


def test_split_last_is_callable_from_an_expression() -> None:
    assert _evaluate('@(split_last("a/b/c", "/"))') == "c"
    assert _evaluate('@(len("abc"))') == 3
    assert _evaluate("@(str(1))") == "1"


# --- interpolated strings --------------------------------------------------


def test_an_interpolated_string_renders_each_expression() -> None:
    assert _evaluate('@($"a{1 + 1}b{2 + 2}c")') == "a2b4c"


def test_an_interpolated_string_with_no_expression_is_returned_as_is() -> None:
    assert _evaluate('@($"plain")') == "plain"


def test_interpolated_strings_unescape_double_braces() -> None:
    """C# interpolated strings use doubled braces for literal braces.

    https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/tokens/interpolated
    """
    assert _evaluate('@($"{{value}}={1 + 1}")') == "{value}=2"


def test_an_interpolated_string_handles_a_nested_brace() -> None:
    assert _evaluate('@($"{ {"a": 1}["a"] }")') == "1"


def test_an_interpolation_escapes_each_double_brace() -> None:
    assert _evaluate('@($"{{{{")') == "{{"


def test_an_unclosed_interpolation_is_an_error() -> None:
    with pytest.raises(ValueError, match=r"^Unclosed interpolation expression$"):
        _evaluate('@($"a{1 + 1")')


# --- validation ------------------------------------------------------------


def test_an_unsupported_node_is_refused_and_names_the_node() -> None:
    with pytest.raises(ValueError, match=r"^Unsupported expression syntax: ListComp$"):
        _evaluate("@([x for x in [1]])")


def test_an_unknown_name_is_refused_and_names_the_name() -> None:
    with pytest.raises(ValueError, match=r"^Unsupported expression name: os$"):
        _evaluate("@(os)")


# --- envelope stripping ----------------------------------------------------


def test_an_envelope_is_only_stripped_when_both_ends_are_present() -> None:
    assert _strip_outer_expression("@(1 + 1)") == "1 + 1"
    assert _strip_outer_expression("@{1 + 1}") == "1 + 1"
    assert _strip_outer_expression("@(1 + 1") == "(1 + 1"
    assert _strip_outer_expression("@1 + 1)") == "1 + 1)"
    assert _strip_outer_expression("@{1 + 1") == "{1 + 1"
    assert _strip_outer_expression("@1 + 1}") == "1 + 1}"


def test_the_allowed_names_are_not_refused() -> None:
    # Accepted equivalents: evaluate_apim_expression__mutmut_19, 22, 24 and 25
    # weaken or drop the empty __builtins__ passed to eval. _validate_ast runs
    # first and refuses every name outside the allowlist, so nothing that
    # reaches eval can name a builtin. The empty globals stay as a second line
    # of defence, not because a test can observe it.
    assert _evaluate("@(context.request.method)") == "GET"
    assert _evaluate("@(True)") is True
    assert _evaluate("@(False)") is False
