from __future__ import annotations

import pytest

from scripts.smoke_oidc import retry_call


def test_retry_call_retries_until_success() -> None:
    attempts = {"count": 0}

    def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("warming up")
        return "ok"

    assert retry_call(operation, attempts=3, delay_seconds=0) == "ok"
    assert attempts["count"] == 3


def test_retry_call_raises_last_error_when_attempts_are_exhausted() -> None:
    attempts = {"count": 0}

    def operation() -> None:
        attempts["count"] += 1
        raise ValueError("still failing")

    with pytest.raises(ValueError, match="still failing"):
        retry_call(operation, attempts=2, delay_seconds=0)

    assert attempts["count"] == 2


def test_retry_after_seconds_reads_the_header() -> None:
    import httpx

    from scripts.smoke_oidc import retry_after_seconds

    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": "37"})) == 37.0
    assert retry_after_seconds(httpx.Response(429)) == 1.0
    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": "soon"})) == 1.0
