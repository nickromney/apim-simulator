from __future__ import annotations

import argparse
import asyncio

import httpx


async def smoke(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=5) as client:
        order = await client.get("/orders/42")
        assert order.status_code == 200, order.text
        assert order.json() == {
            "order_id": "42",
            "account_id": "acct-17",
            "total": {"amount": 12.5, "currency": "GBP"},
            "status": "allocated",
        }

        tenant_a = await client.get("/tenants/a/catalog")
        assert tenant_a.status_code == 200 and tenant_a.json()["tenant_stamp"] == "tenant-a-stamp"
        unknown_tenant = await client.get("/tenants/c/catalog")
        assert unknown_tenant.status_code == 404, unknown_tenant.text
        failed = await client.get("/tenants/a/catalog?fail=true")
        assert failed.status_code == 503, failed.text
        isolated = await client.get("/tenants/a/catalog")
        assert isolated.status_code == 503, isolated.text
        tenant_b = await client.get("/tenants/b/catalog")
        assert tenant_b.status_code == 200 and tenant_b.json()["tenant_stamp"] == "tenant-b-stamp"
        await asyncio.sleep(1.1)
        recovered_a = await client.get("/tenants/a/catalog")
        assert recovered_a.status_code == 200 and recovered_a.json()["tenant_stamp"] == "tenant-a-stamp"


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test adapter translation and isolated tenant stamps.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    asyncio.run(smoke(args.base_url))
    print("Architecture pattern smoke passed: adapter mapping and isolated stamp failure")


if __name__ == "__main__":
    main()
