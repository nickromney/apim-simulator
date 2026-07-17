from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import httpx

GATEWAY_BASE_URL = os.getenv("SMOKE_AI_BASE_URL", "http://127.0.0.1:8000")
SUBSCRIPTION_KEY = os.getenv("SMOKE_AI_SUBSCRIPTION_KEY", "ai-team-alpha-key")
SECOND_SUBSCRIPTION_KEY = os.getenv("SMOKE_AI_SECOND_SUBSCRIPTION_KEY", "ai-team-beta-key")
DEFAULT_ATTEMPTS = int(os.getenv("SMOKE_AI_ATTEMPTS", "30"))
DEFAULT_DELAY_SECONDS = float(os.getenv("SMOKE_AI_RETRY_DELAY_SECONDS", "1"))

CHAT_PATH = "/openai/deployments/gpt-demo/chat/completions"
COMPAT_CHAT_PATH = "/llm/v1/chat/completions"


def retry_call[T](
    operation: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    raise RuntimeError("retry_call exhausted without executing operation")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def chat_request(path: str, *, key: str | None, prompt: str = "Say hello to the AI gateway.") -> httpx.Response:
    headers: dict[str, str] = {"content-type": "application/json"}
    if key is not None:
        headers["api-key"] = key
    payload: dict[str, Any] = {
        "model": "gpt-demo",
        "messages": [{"role": "user", "content": prompt}],
    }
    with httpx.Client(timeout=15.0) as client:
        return client.post(f"{GATEWAY_BASE_URL}{path}", headers=headers, json=payload)


def check_gateway_health() -> None:
    with httpx.Client(timeout=10.0) as client:
        client.get(f"{GATEWAY_BASE_URL}/apim/health").raise_for_status()


def smoke_auth() -> None:
    missing = chat_request(CHAT_PATH, key=None)
    require(missing.status_code == 401, f"expected 401 without subscription key, got {missing.status_code}")
    invalid = chat_request(CHAT_PATH, key="not-a-real-key")
    require(invalid.status_code == 401, f"expected 401 with invalid key, got {invalid.status_code}")


def smoke_completion() -> dict[str, Any]:
    response = retry_call(lambda: chat_request(CHAT_PATH, key=SUBSCRIPTION_KEY))
    require(response.status_code == 200, f"expected 200 completion, got {response.status_code}: {response.text}")
    payload = response.json()
    usage = payload.get("usage") or {}
    require(int(usage.get("total_tokens") or 0) > 0, f"expected usage in payload, got {payload}")
    require(
        "x-apim-tokens-consumed" in response.headers,
        "expected x-apim-tokens-consumed header from llm-token-limit policy",
    )
    require(
        "x-apim-remaining-tokens" in response.headers,
        "expected x-apim-remaining-tokens header from llm-token-limit policy",
    )
    require(
        response.headers.get("x-apim-backend-pool") == "llm-pool",
        f"expected x-apim-backend-pool=llm-pool, got {response.headers.get('x-apim-backend-pool')!r}",
    )
    return payload


def smoke_pool_distribution() -> list[str]:
    members = []
    for _ in range(2):
        response = chat_request(CHAT_PATH, key=SECOND_SUBSCRIPTION_KEY, prompt="Which deployment am I on?")
        require(response.status_code == 200, f"expected 200 during pool check, got {response.status_code}")
        members.append(response.headers.get("x-apim-backend-id", ""))
    require(
        len(set(members)) == 2,
        f"expected round-robin across both pool members, saw {members}",
    )
    return members


def smoke_token_limit_429() -> int:
    for attempt in range(1, 21):
        response = chat_request(CHAT_PATH, key=SUBSCRIPTION_KEY, prompt="Fill the token bucket quickly please.")
        if response.status_code == 429:
            require(
                "retry-after" in response.headers,
                f"expected Retry-After header on 429, got {dict(response.headers)}",
            )
            require(
                "Token limit is exceeded" in response.text,
                f"unexpected 429 body: {response.text}",
            )
            return attempt
        require(
            response.status_code == 200,
            f"expected 200 or 429 while filling bucket, got {response.status_code}: {response.text}",
        )
    raise RuntimeError("token limit was never hit after 20 requests")


def smoke_counter_isolation() -> None:
    response = chat_request(CHAT_PATH, key=SECOND_SUBSCRIPTION_KEY)
    require(
        response.status_code == 200,
        f"expected second subscription to have its own token counter, got {response.status_code}: {response.text}",
    )


def smoke_openai_compat_quota_headers() -> None:
    response = chat_request(COMPAT_CHAT_PATH, key=SUBSCRIPTION_KEY)
    require(response.status_code == 200, f"expected 200 from compat API, got {response.status_code}: {response.text}")
    require(
        "x-apim-remaining-quota-tokens" in response.headers,
        "expected x-apim-remaining-quota-tokens header from token-quota policy",
    )


def smoke_streaming_passthrough() -> None:
    headers = {"content-type": "application/json", "api-key": SECOND_SUBSCRIPTION_KEY}
    payload = {
        "model": "gpt-demo",
        "messages": [{"role": "user", "content": "Stream a short reply."}],
        "stream": True,
    }
    with httpx.Client(timeout=15.0) as client:
        response = client.post(f"{GATEWAY_BASE_URL}{CHAT_PATH}", headers=headers, json=payload)
    require(response.status_code == 200, f"expected 200 stream, got {response.status_code}: {response.text}")
    require("data:" in response.text, f"expected SSE chunks in body, got: {response.text[:200]}")
    require(response.text.rstrip().endswith("data: [DONE]"), "expected SSE stream to finish with [DONE]")


def main() -> int:
    retry_call(check_gateway_health)
    smoke_auth()
    payload = smoke_completion()
    members = smoke_pool_distribution()
    tripped_at = smoke_token_limit_429()
    smoke_counter_isolation()
    smoke_openai_compat_quota_headers()
    smoke_streaming_passthrough()

    print("ai gateway smoke passed")
    print("- missing/invalid subscription key: 401")
    print(f"- chat completion usage total_tokens: {payload['usage']['total_tokens']}")
    print(f"- backend pool round-robin across members: {members}")
    print(f"- llm-token-limit tripped 429 after {tripped_at} follow-up requests")
    print("- second subscription kept its own token counter: 200")
    print("- token-quota headers present on OpenAI-compatible API")
    print("- SSE streaming passthrough: 200 with [DONE]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
