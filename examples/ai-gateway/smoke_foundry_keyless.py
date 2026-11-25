"""Check the keyless AI gateway pairing through published localhost ports.

Expects the sibling aifoundry-simulator started with `make up-keyless` and this
gateway with `make up-ai-foundry-keyless`. Proves:

- the gateway publishes its issuer's discovery document and JWKS, with the
  issuer Foundry trusts;
- a chat completion through APIM succeeds, and the trace shows the gateway
  attached its managed identity token and removed the client's `api-key`;
- Foundry rejects the credentials that could otherwise explain that success:
  a JWT it can't verify, and the APIM subscription key sent as `api-key`.

The gateway config holds no Foundry key, so a 200 through APIM means Foundry
accepted the gateway's bearer JWT (its signature, `iss` and `aud`, and the
`system-assigned` role).
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

ISSUER = "http://apim-simulator:8000/.well-known/workload-identity"
RESOURCE = "https://cognitiveservices.azure.com"
SUBSCRIPTION_KEY = "ai-team-alpha-key"
CHAT_PATH = "/openai/deployments/gpt-demo/chat/completions?api-version=2024-10-21"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def retry[T](operation: Callable[[], T], *, attempts: int = 30, delay_seconds: float = 1.0) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception:  # noqa: BLE001
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)
    raise RuntimeError("retry exhausted")


def _b64url(value: dict[str, Any] | bytes) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def garbage_jwt() -> str:
    """A well-formed RS256 JWT with the trusted issuer and audience but a bogus signature."""
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": "not-a-published-key"}
    claims = {"iss": ISSUER, "aud": RESOURCE, "oid": "system-assigned", "iat": now, "nbf": now, "exp": now + 300}
    return f"{_b64url(header)}.{_b64url(claims)}.{_b64url(b'not-a-signature')}"


def chat_payload() -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": f"keyless smoke {uuid.uuid4()}"}]}


def check_jwks(gateway: httpx.Client) -> int:
    discovery = gateway.get("/.well-known/workload-identity/openid-configuration")
    require(discovery.status_code == 200, f"discovery: expected 200, got {discovery.status_code}: {discovery.text}")
    document = discovery.json()
    require(document.get("issuer") == ISSUER, f"discovery issuer {document.get('issuer')!r} != {ISSUER!r}")
    jwks = gateway.get("/.well-known/workload-identity/jwks.json")
    require(jwks.status_code == 200, f"jwks: expected 200, got {jwks.status_code}: {jwks.text}")
    keys = jwks.json().get("keys") or []
    require(bool(keys) and all(key.get("kty") == "RSA" and key.get("kid") for key in keys), f"bad JWKS: {keys}")
    return len(keys)


def check_chat_through_gateway(gateway: httpx.Client) -> dict[str, Any]:
    response = gateway.post(
        CHAT_PATH, headers={"api-key": SUBSCRIPTION_KEY, "x-apim-trace": "true"}, json=chat_payload()
    )
    require(response.status_code == 200, f"chat via APIM: expected 200, got {response.status_code}: {response.text}")
    require(int((response.json().get("usage") or {}).get("total_tokens") or 0) > 0, "chat via APIM: no usage")
    trace_id = response.headers.get("x-apim-trace-id")
    require(bool(trace_id), "chat via APIM: no x-apim-trace-id header")
    trace = gateway.get(f"/apim/trace/{trace_id}")
    require(trace.status_code == 200, f"trace: expected 200, got {trace.status_code}: {trace.text}")
    steps = trace.json().get("policy_steps") or []
    require(
        {"step": "authentication-managed-identity", "resource": RESOURCE} in steps,
        f"trace: no authentication-managed-identity step for {RESOURCE}: {steps}",
    )
    require(
        {"step": "set-header", "name": "api-key", "action": "delete"} in steps,
        f"trace: the client's api-key header was not removed: {steps}",
    )
    return response.json()


def check_foundry_rejects(foundry: httpx.Client) -> None:
    bogus = foundry.post(CHAT_PATH, headers={"authorization": f"Bearer {garbage_jwt()}"}, json=chat_payload())
    require(bogus.status_code == 401, f"Foundry with garbage JWT: expected 401, got {bogus.status_code}: {bogus.text}")
    subscription_key = foundry.post(CHAT_PATH, headers={"api-key": SUBSCRIPTION_KEY}, json=chat_payload())
    require(
        subscription_key.status_code == 401,
        f"Foundry with the APIM subscription key: expected 401, got {subscription_key.status_code}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8000")
    parser.add_argument("--foundry-url", default="http://127.0.0.1:8020")
    args = parser.parse_args()

    with (
        httpx.Client(base_url=args.gateway_url.rstrip("/"), timeout=15.0, trust_env=False) as gateway,
        httpx.Client(base_url=args.foundry_url.rstrip("/"), timeout=15.0, trust_env=False) as foundry,
    ):
        retry(lambda: gateway.get("/apim/health").raise_for_status())
        retry(lambda: foundry.get("/foundry/health").raise_for_status())
        key_count = check_jwks(gateway)
        payload = retry(lambda: check_chat_through_gateway(gateway), attempts=10)
        check_foundry_rejects(foundry)

    print("keyless ai foundry smoke passed")
    print(f"- issuer discovery and JWKS published ({key_count} key) with issuer {ISSUER}")
    print(f"- chat via APIM with no Foundry key: 200, total_tokens {payload['usage']['total_tokens']}")
    print(f"- trace: authentication-managed-identity for {RESOURCE}")
    print("- Foundry direct: garbage JWT 401, APIM subscription key as api-key 401")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
