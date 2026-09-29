"""APIM error handling: what counts as a policy error and what LastError says.

Spec: https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies

Processing "immediately jumps to the on-error policy section" when an error
occurs. The predefined-errors table lists the deliberate refusals of
rate-limit, quota, ip-filter, check-header and validate-jwt as errors, so a
response produced by those policies enters on-error just like an exception.
return-response and mock-response are not errors.

Policy nodes carry the optional ``id`` attribute and parser-built nested path
so ``Path`` and ``PolicyId`` can be exposed through on-error.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from fastapi import HTTPException

# Policies whose deliberate refusal responses the docs list as predefined errors.
ERROR_RESPONSE_POLICIES = frozenset(
    {
        "rate-limit",
        "rate-limit-by-key",
        "quota",
        "quota-by-key",
        "ip-filter",
        "check-header",
        "validate-jwt",
        "validate-content",
        "validate-parameters",
        "llm-token-limit",
    }
)

_KEBAB = re.compile(r"(?<!^)(?=[A-Z])")


def element_name(step: Any) -> str:
    """The policy element name for a parsed node, e.g. CheckHeader -> check-header."""
    return _KEBAB.sub("-", type(step).__name__).lower()


def build_last_error(
    *,
    source: str,
    reason: str = "",
    message: str = "",
    scope: str = "",
    section: str = "",
    path: str = "",
    policy_id: str = "",
) -> dict[str, str]:
    """The one place a context.LastError dict is built."""
    return {
        "Source": source,
        "Reason": reason,
        "Message": message,
        "Scope": scope,
        "Section": section,
        "Path": path,
        "PolicyId": policy_id,
    }


# (substring of the message, reason) in match order, per the predefined tables.
_REASONS_BY_SOURCE: dict[str, list[tuple[str, str]]] = {
    "rate-limit": [("", "RateLimitExceeded")],
    "rate-limit-by-key": [("", "RateLimitExceeded")],
    "quota": [("", "QuotaExceeded")],
    "quota-by-key": [("", "QuotaExceeded")],
    "ip-filter": [("parse", "FailedToParseCallerIP"), ("block", "CallerIpBlocked"), ("", "CallerIpNotAllowed")],
    "check-header": [("not found", "HeaderNotFound"), ("", "HeaderValueNotAllowed")],
    "validate-jwt": [
        ("not present", "TokenNotPresent"),
        ("signature", "TokenSignatureInvalid"),
        ("audience", "TokenAudienceNotAllowed"),
        ("issuer", "TokenIssuerNotAllowed"),
        ("expired", "TokenExpired"),
        ("missing required claim", "TokenClaimNotFound"),
        ("claim", "TokenClaimValueNotAllowed"),
        ("", "JwtInvalid"),
    ],
}


def _reason_for(source: str, message: str) -> str:
    lowered = message.lower()
    for needle, reason in _REASONS_BY_SOURCE.get(source, []):
        if needle in lowered:
            return reason
    return ""


def _refusal_message(body: bytes) -> str:
    """The message a refusal carried: the envelope's message field, else the raw text."""
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
        return parsed["message"]
    return text


def response_last_error(
    source: str,
    body: bytes,
    *,
    scope: str,
    section: str,
    path: str = "",
    policy_id: str = "",
    reason: str | None = None,
) -> dict[str, str] | None:
    """LastError for a refusal response, or None when the response is not an error.

    A policy that knows its predefined Reason records it; otherwise the Reason is
    inferred from the message text.
    """
    if source not in ERROR_RESPONSE_POLICIES:
        return None
    message = _refusal_message(body)
    return build_last_error(
        source=source,
        reason=reason or _reason_for(source, message),
        message=message,
        scope=scope,
        section=section,
        path=path,
        policy_id=policy_id,
    )


def exception_last_error(
    exc: Exception,
    source: str,
    *,
    scope: str,
    section: str,
    path: str = "",
    policy_id: str = "",
) -> tuple[int, dict[str, str]]:
    """Status code and LastError for an exception raised while a policy ran.

    HTTPException carries its own status (a misconfigured policy is a 500). Any
    other exception is a runtime failure while evaluating an expression, which
    the docs name ExpressionValueEvaluationFailure. A timeout is Timeout. The
    docs list no other predefined reason, so the rest are left empty.
    """
    if isinstance(exc, HTTPException):
        timed_out = isinstance(exc.__cause__, httpx.TimeoutException)
        return exc.status_code, build_last_error(
            source=source,
            reason="Timeout" if timed_out else "",
            message=str(exc.detail),
            scope=scope,
            section=section,
            path=path,
            policy_id=policy_id,
        )
    return 500, build_last_error(
        source=source,
        reason="ExpressionValueEvaluationFailure",
        message=str(exc),
        scope=scope,
        section=section,
        path=path,
        policy_id=policy_id,
    )
