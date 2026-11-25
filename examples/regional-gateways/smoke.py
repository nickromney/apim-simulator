"""Check the regional-gateways stack through its published localhost ports."""

from __future__ import annotations

import argparse
import time

import httpx


def _order(client: httpx.Client, url: str, outage: str | None = None) -> httpx.Response:
    headers = {"x-simulate-outage": outage} if outage else {}
    response = client.get(f"{url}/orders/42", headers=headers)
    assert response.status_code == 200, f"{url}: {response.status_code} {response.text}"
    return response


def _expect(response: httpx.Response, *, gateway: str, backend: str, attempts: str) -> None:
    seen = (
        response.headers.get("x-apim-region"),
        response.json().get("backend_region"),
        response.headers.get("x-apim-attempts"),
    )
    assert seen == (gateway, backend, attempts), f"expected {(gateway, backend, attempts)}, got {seen}"


def smoke(args: argparse.Namespace) -> None:
    with httpx.Client(timeout=10) as client:
        if args.expect_global_region:
            region = args.expect_global_region
            deadline = time.monotonic() + args.wait_seconds
            while True:
                try:
                    _expect(_order(client, args.global_url), gateway=region, backend=region, attempts="1")
                    break
                except (AssertionError, httpx.HTTPError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            print(f"ok global URL is served by {region}")
            return

        _expect(_order(client, args.westeurope_url), gateway="West Europe", backend="West Europe", attempts="1")
        print("ok West Europe gateway calls the West Europe backend")
        _expect(_order(client, args.eastus_url), gateway="East US", backend="East US", attempts="1")
        print("ok East US gateway calls the East US backend")
        _expect(_order(client, args.global_url), gateway="West Europe", backend="West Europe", attempts="1")
        print("ok global URL prefers West Europe")
        _expect(
            _order(client, args.westeurope_url, outage="West Europe"),
            gateway="West Europe",
            backend="East US",
            attempts="2",
        )
        print("ok retry fails over to the East US backend when West Europe's backend returns 503")
    print("regional gateways smoke passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--global-url", default="http://127.0.0.1:8000")
    parser.add_argument("--westeurope-url", default="http://127.0.0.1:8001")
    parser.add_argument("--eastus-url", default="http://127.0.0.1:8002")
    parser.add_argument("--expect-global-region", help="Only check which region serves the global URL")
    parser.add_argument("--wait-seconds", type=float, default=0, help="Keep checking the global URL this long")
    smoke(parser.parse_args())


if __name__ == "__main__":
    main()
