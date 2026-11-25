"""Check the new-instance stack through its published localhost port.

Run it against a freshly started stack: the Starter rate limit and the portal
subscriptions it creates carry over between runs until `make down`.
"""

from __future__ import annotations

import argparse
import time

import httpx

STARTER_KEY = "new-instance-starter-primary"
UNLIMITED_KEY = "new-instance-unlimited-primary"
ALL_ACCESS_KEY = "new-instance-all-access-primary"
TENANT = {"X-Apim-Tenant-Key": "local-dev-tenant-key"}
PORTAL_USER = {"X-Apim-Portal-User": "demo-dev"}


def _wait_ready(client: httpx.Client, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if client.get("/apim/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() >= deadline:
            raise SystemExit(f"gateway at {client.base_url} did not become healthy within {seconds:.0f}s")
        time.sleep(1)


def _call(client: httpx.Client, key: str | None, method: str = "GET", path: str = "/echo/resource") -> int:
    headers = {"Ocp-Apim-Subscription-Key": key} if key else {}
    return client.request(method, path, params={"param1": "sample"}, headers=headers).status_code


def _expect(label: str, seen: object, expected: object) -> None:
    assert seen == expected, f"{label}: expected {expected}, got {seen}"
    print(f"ok {label}")


def _gateway_checks(client: httpx.Client) -> None:
    _expect("a call without a key gets 401", _call(client, None), 401)
    statuses = [_call(client, STARTER_KEY) for _ in range(6)]
    _expect("Starter allows 5 calls a minute, then returns 429", statuses, [200] * 5 + [429])
    _expect("Unlimited allows 10 calls in a row", [_call(client, UNLIMITED_KEY) for _ in range(10)], [200] * 10)
    _expect(
        "the built-in all-access key skips Starter's limits",
        [_call(client, ALL_ACCESS_KEY) for _ in range(8)],
        [200] * 8,
    )
    _expect("HEAD /echo/resource returns 200", _call(client, ALL_ACCESS_KEY, "HEAD"), 200)


def _portal_checks(client: httpx.Client) -> None:
    def request(product_id: str) -> httpx.Response:
        return client.post("/apim/portal/subscriptions", headers=PORTAL_USER, json={"product_id": product_id})

    starter = request("starter")
    _expect(
        "a portal Starter subscription is active at once",
        (starter.status_code, starter.json()["state"]),
        (201, "active"),
    )
    _expect("a second Starter subscription hits subscriptions_limit", request("starter").status_code, 409)

    unlimited = request("unlimited")
    _expect("a portal Unlimited subscription waits for approval", unlimited.json()["state"], "submitted")
    subscription_id = unlimited.json()["id"]
    key = unlimited.json()["keys"]["primary"]
    _expect("the submitted Unlimited key is refused", _call(client, key), 401)

    approved = client.patch(
        f"/apim/management/subscriptions/{subscription_id}", headers=TENANT, json={"state": "active"}
    )
    _expect("the administrator approves the Unlimited subscription", approved.status_code, 200)
    _expect("the approved Unlimited key works", _call(client, key), 200)

    messages = client.get("/apim/management/outbox", headers=TENANT).json()["messages"]
    activated = [
        m
        for m in messages
        if m["notification"] == "PurchaseDeveloperNotificationMessage" and "demo-dev@example.test" in m["to"]
    ]
    _expect("the outbox holds demo-dev's activation email", len(activated) >= 2, True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8000")
    parser.add_argument("--wait-seconds", type=float, default=60)
    args = parser.parse_args()
    with httpx.Client(base_url=args.gateway_url, timeout=10) as client:
        _wait_ready(client, args.wait_seconds)
        _gateway_checks(client)
        _portal_checks(client)
    print("new instance smoke passed")


if __name__ == "__main__":
    main()
