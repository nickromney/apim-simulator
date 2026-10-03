"""Run the advanced request throttling guide through local management and gateway APIs."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import httpx
import jwt


def _source_example(number: int) -> str:
    fixtures = (
        Path(__file__).resolve().parents[2] / "tests/fixtures/policy_guides/api-management-sample-flexible-throttling"
    )
    xml = (fixtures / f"example-{number:02d}.xml").read_text()
    # Correct nested XML quoting in Learn without changing either expression.
    return re.sub(r'counter-key="(@\(.+\))"', lambda match: f"counter-key='{match[1]}'", xml)


def _policy(inbound: str) -> str:
    return (
        f"<policies><inbound><base />{inbound}"
        '<return-response><set-status code="200" reason="OK" /><set-body>ok</set-body></return-response>'
        "</inbound><backend /><outbound /><on-error /></policies>"
    )


def _manage(client: httpx.Client, method: str, path: str, body=None) -> dict:
    tenant_key = client.headers.get("X-Apim-Tenant-Key") or os.environ.get("APIM_TENANT_KEY", "local-dev-tenant-key")
    response = client.request(method, path, json=body, headers={"X-Apim-Tenant-Key": tenant_key})
    response.raise_for_status()
    return response.json()


def _expect(response: httpx.Response, status: int) -> int:
    if response.status_code != status:
        raise AssertionError(f"Expected HTTP {status}, got {response.status_code}: {response.text}")
    if status in (403, 429):
        assert int(response.headers["Retry-After"]) > 0
    return status


def run(client: httpx.Client) -> dict:
    """Create isolated policies, assert their gateway behavior, and delete the resources."""
    suffix = uuid.uuid4().hex[:12]
    resources = []
    results = {}

    def api(label: str, inbound: str, products=()) -> str:
        api_id = f"guide-throttle-{suffix}-{label}"
        route = f"/apim/management/apis/{api_id}"
        _manage(
            client,
            "PUT",
            route,
            {
                "name": "Advanced throttling guide",
                "path": api_id,
                "upstream_base_url": "http://localhost:8000",
                "products": list(products),
                "policies_xml": _policy(inbound),
            },
        )
        resources.append(route)
        _manage(client, "PUT", f"{route}/operations/call", {"name": "Call", "method": "POST", "url_template": "/call"})
        return f"/{api_id}/call"

    def burst(path: str, calls: int, headers: dict) -> dict:
        for _ in range(calls):
            _expect(client.post(path, headers=headers), 200)
        blocked = client.post(path, headers=headers)
        _expect(blocked, 429)
        return {"allowed": calls, "blocked": 429, "retry_after": int(blocked.headers["Retry-After"])}

    try:
        path = api("bucket", _source_example(1).replace("Counter1", f"Counter1-{suffix}"))
        results["six_call_bucket"] = burst(path, 6, {})

        path = api("ip", _source_example(2))
        address = int(suffix[:4], 16)
        first_ip = f"198.18.{address // 256}.{address % 256}"
        second_ip = f"198.18.{address // 256}.{(address + 1) % 256}"
        results["ip"] = burst(path, 10, {"X-Forwarded-For": first_ip})
        results["ip"]["independent_address"] = _expect(client.post(path, headers={"X-Forwarded-For": second_ip}), 200)

        path = api("jwt", _source_example(3))

        def user(subject):
            token = jwt.encode(
                {"sub": f"{suffix}-{subject}"}, "local-guide-signing-key-at-least-32-bytes", algorithm="HS256"
            )
            return {"Authorization": f"Bearer {token}"}

        results["jwt_subject"] = burst(path, 10, user("alice"))
        results["jwt_subject"]["independent_subject"] = _expect(client.post(path, headers=user("bob")), 200)

        path = api("header", _source_example(4))
        results["client_header"] = burst(path, 100, {"Rate-Key": f"{suffix}-customer-a"})
        results["client_header"]["independent_customer"] = _expect(
            client.post(path, headers={"Rate-Key": f"{suffix}-customer-b"}), 200
        )

        path = api(
            "bandwidth",
            '<quota-by-key bandwidth="1" renewal-period="300" counter-key="@(context.Request.IpAddress)" />',
        )
        headers = {"X-Forwarded-For": f"198.19.{address // 256}.{address % 256}"}
        _expect(client.post(path, content=b"x" * 2048, headers=headers), 200)
        results["bandwidth"] = {"first": 200, "after_body_accounting": _expect(client.post(path, headers=headers), 403)}
        results["bandwidth"]["independent_address"] = _expect(
            client.post(path, headers={"X-Forwarded-For": f"198.19.{address // 256}.{(address + 1) % 256}"}), 200
        )

        product_id = f"guide-throttle-{suffix}-product"
        product_route = f"/apim/management/products/{product_id}"
        _manage(client, "PUT", product_route, {"name": "Combined guide limits", "state": "published"})
        resources.append(product_route)
        _manage(
            client,
            "PUT",
            f"/apim/management/policies/product/{product_id}",
            {
                "xml": '<policies><inbound><base /><rate-limit calls="3" renewal-period="60" /></inbound><backend /><outbound /><on-error /></policies>'
            },
        )
        path = api(
            "combined",
            '<rate-limit-by-key calls="2" renewal-period="60" counter-key=\'@(request.Headers.GetValueOrDefault("Rate-Key",""))\' />',
            [product_id],
        )
        subscription_id = f"guide-throttle-{suffix}-subscription"
        subscription_key = f"local-guide-{suffix}"
        _manage(
            client,
            "POST",
            "/apim/management/subscriptions",
            {
                "id": subscription_id,
                "name": "Combined guide subscription",
                "products": [product_id],
                "primary_key": subscription_key,
            },
        )
        resources.append(f"/apim/management/subscriptions/{subscription_id}")
        headers = {"Ocp-Apim-Subscription-Key": subscription_key, "Rate-Key": f"{suffix}-combined-alice"}
        _expect(client.post(path, headers=headers), 200)
        _expect(client.post(path, headers=headers), 200)
        headers["Rate-Key"] = f"{suffix}-combined-bob"
        _expect(client.post(path, headers=headers), 200)
        results["combined"] = {
            "alice": 2,
            "bob": 1,
            "shared_subscription_block": _expect(client.post(path, headers=headers), 429),
        }
        return {"guide": "advanced-request-throttling", "results": results}
    finally:
        for resource in reversed(resources):
            _manage(client, "DELETE", resource)


if __name__ == "__main__":
    with httpx.Client(
        base_url=os.environ.get("APIM_BASE", "http://localhost:8000"),
        headers={"X-Apim-Tenant-Key": os.environ.get("APIM_TENANT_KEY", "local-dev-tenant-key")},
        timeout=30.0,
    ) as client:
        print(json.dumps(run(client), indent=2))
