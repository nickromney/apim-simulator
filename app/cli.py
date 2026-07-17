from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 10.0


class CliUsageError(Exception):
    """A command-level validation failure (bad flags, unreadable files) reported as exit code 1."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apimsim",
        description="Thin HTTP client for the APIM simulator's tenant-key-protected management API.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("APIM_BASE_URL", DEFAULT_BASE_URL),
        help=f"Simulator base URL (env APIM_BASE_URL, default {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--tenant-key",
        default=os.environ.get("APIM_TENANT_KEY"),
        help="Tenant key sent as X-Apim-Tenant-Key (env APIM_TENANT_KEY).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("APIM_TIMEOUT", DEFAULT_TIMEOUT)),
        help=f"Request timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show management status (counts, gateway policy scope).")
    subparsers.add_parser("summary", help="Show management summary (routes, gateway policy scope).")
    subparsers.add_parser("service", help="Show service metadata.")

    subparsers.add_parser("apis", help="List APIs.")
    api_parser = subparsers.add_parser("api", help="Show one API.")
    api_parser.add_argument("api_id")

    operations_parser = subparsers.add_parser("operations", help="List operations for one API.")
    operations_parser.add_argument("api_id")

    subparsers.add_parser("products", help="List products.")
    product_parser = subparsers.add_parser("product", help="Show one product.")
    product_parser.add_argument("product_id")

    put_api_parser = subparsers.add_parser("put-api", help="Create or replace an API from a JSON file.")
    put_api_parser.add_argument("api_id")
    put_api_parser.add_argument("--file", required=True, help="Path to a JSON file with the API payload.")

    delete_api_parser = subparsers.add_parser("delete-api", help="Delete an API.")
    delete_api_parser.add_argument("api_id")
    delete_api_parser.add_argument("--yes", action="store_true", help="Confirm deletion (required).")

    import_openapi_parser = subparsers.add_parser(
        "import-openapi", help="Import an OpenAPI document into an API via POST /apim/management/apis/{id}/import."
    )
    import_openapi_parser.add_argument("api_id")
    import_source_group = import_openapi_parser.add_mutually_exclusive_group(required=True)
    import_source_group.add_argument("--file", help="Path to a local OpenAPI document (JSON or YAML).")
    import_source_group.add_argument("--url", help="URL the simulator should fetch the OpenAPI document from.")
    import_openapi_parser.add_argument("--api-name", dest="api_name", help="Override the imported API's name.")
    import_openapi_parser.add_argument("--api-path", dest="api_path", help="Override the imported API's path.")

    put_product_parser = subparsers.add_parser("put-product", help="Create or replace a product from a JSON file.")
    put_product_parser.add_argument("product_id")
    put_product_parser.add_argument("--file", required=True, help="Path to a JSON file with the product payload.")

    delete_product_parser = subparsers.add_parser("delete-product", help="Delete a product.")
    delete_product_parser.add_argument("product_id")
    delete_product_parser.add_argument("--yes", action="store_true", help="Confirm deletion (required).")

    subparsers.add_parser("subscriptions", help="List subscriptions.")

    policy_parser = subparsers.add_parser(
        "policy", help="Show the policy XML for a scope, e.g. `policy api weather` or `policy gateway gateway`."
    )
    policy_parser.add_argument("scope_type", help="Policy scope type: gateway, api, operation, product, or route.")
    policy_parser.add_argument("scope_name", help="Scope name, e.g. an api id or api:operation for operation scope.")

    set_policy_parser = subparsers.add_parser("set-policy", help="Replace the policy XML for a scope.")
    set_policy_parser.add_argument("scope_type", help="Policy scope type: gateway, api, operation, product, or route.")
    set_policy_parser.add_argument(
        "scope_name", help="Scope name, e.g. an api id or api:operation for operation scope."
    )
    policy_body_group = set_policy_parser.add_mutually_exclusive_group(required=True)
    policy_body_group.add_argument("--file", help="Path to a file containing the policy XML.")
    policy_body_group.add_argument("--xml", help="Policy XML as a literal string.")

    subparsers.add_parser("traces", help="List recent traces.")
    trace_parser = subparsers.add_parser("trace", help="Show one trace (public endpoint, no tenant key required).")
    trace_parser.add_argument("trace_id")

    replay_parser = subparsers.add_parser(
        "replay", help="Replay a request through the gateway via POST /apim/management/replay."
    )
    replay_parser.add_argument("path", help="Gateway path to replay, e.g. /api/health.")
    replay_parser.add_argument("--method", default="GET", help="HTTP method to replay (default GET).")
    replay_parser.add_argument(
        "--query", action="append", default=[], metavar="KEY=VALUE", help="Query parameter, repeatable."
    )
    replay_parser.add_argument(
        "--header", action="append", default=[], metavar="NAME=VALUE", help="Request header, repeatable."
    )
    body_group = replay_parser.add_mutually_exclusive_group()
    body_group.add_argument("--body-text", help="Request body as text.")
    body_group.add_argument("--body-base64", help="Request body as base64.")

    return parser


def _parse_kv_pairs(items: list[str], *, flag: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"error: {flag} value {item!r} must be KEY=VALUE")
        pairs[key] = value
    return pairs


def _headers(tenant_key: str | None) -> dict[str, str]:
    return {"X-Apim-Tenant-Key": tenant_key} if tenant_key else {}


def _read_text_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliUsageError(f"could not read {path}: {exc}") from exc


def _read_json_file(path: str) -> dict[str, Any]:
    text = _read_text_file(path)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliUsageError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CliUsageError(f"{path} must contain a JSON object")
    return payload


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    tenant_key: str | None,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    return await client.request(method, path, headers=_headers(tenant_key), json=json_body)


async def _dispatch(args: argparse.Namespace, client: httpx.AsyncClient) -> httpx.Response:
    command = args.command
    if command == "status":
        return await _request(client, "GET", "/apim/management/status", tenant_key=args.tenant_key)
    if command == "summary":
        return await _request(client, "GET", "/apim/management/summary", tenant_key=args.tenant_key)
    if command == "service":
        return await _request(client, "GET", "/apim/management/service", tenant_key=args.tenant_key)
    if command == "apis":
        return await _request(client, "GET", "/apim/management/apis", tenant_key=args.tenant_key)
    if command == "api":
        return await _request(client, "GET", f"/apim/management/apis/{args.api_id}", tenant_key=args.tenant_key)
    if command == "operations":
        return await _request(
            client, "GET", f"/apim/management/apis/{args.api_id}/operations", tenant_key=args.tenant_key
        )
    if command == "put-api":
        body = _read_json_file(args.file)
        return await _request(
            client, "PUT", f"/apim/management/apis/{args.api_id}", tenant_key=args.tenant_key, json_body=body
        )
    if command == "delete-api":
        if not args.yes:
            raise CliUsageError(f"refusing to delete API {args.api_id!r} without --yes")
        return await _request(client, "DELETE", f"/apim/management/apis/{args.api_id}", tenant_key=args.tenant_key)
    if command == "import-openapi":
        if args.file is not None:
            suffix = Path(args.file).suffix.lower()
            content_format = "openapi+json" if suffix == ".json" else "openapi"
            content_value = _read_text_file(args.file)
        else:
            content_format = "openapi-link"
            content_value = args.url
        import_body: dict[str, Any] = {"content_format": content_format, "content_value": content_value}
        if args.api_name is not None:
            import_body["name"] = args.api_name
        if args.api_path is not None:
            import_body["path"] = args.api_path
        return await _request(
            client,
            "POST",
            f"/apim/management/apis/{args.api_id}/import",
            tenant_key=args.tenant_key,
            json_body=import_body,
        )
    if command == "products":
        return await _request(client, "GET", "/apim/management/products", tenant_key=args.tenant_key)
    if command == "product":
        return await _request(client, "GET", f"/apim/management/products/{args.product_id}", tenant_key=args.tenant_key)
    if command == "put-product":
        body = _read_json_file(args.file)
        return await _request(
            client, "PUT", f"/apim/management/products/{args.product_id}", tenant_key=args.tenant_key, json_body=body
        )
    if command == "delete-product":
        if not args.yes:
            raise CliUsageError(f"refusing to delete product {args.product_id!r} without --yes")
        return await _request(
            client, "DELETE", f"/apim/management/products/{args.product_id}", tenant_key=args.tenant_key
        )
    if command == "subscriptions":
        return await _request(client, "GET", "/apim/management/subscriptions", tenant_key=args.tenant_key)
    if command == "policy":
        return await _request(
            client,
            "GET",
            f"/apim/management/policies/{args.scope_type}/{args.scope_name}",
            tenant_key=args.tenant_key,
        )
    if command == "set-policy":
        xml = _read_text_file(args.file) if args.file is not None else args.xml
        return await _request(
            client,
            "PUT",
            f"/apim/management/policies/{args.scope_type}/{args.scope_name}",
            tenant_key=args.tenant_key,
            json_body={"xml": xml},
        )
    if command == "traces":
        return await _request(client, "GET", "/apim/management/traces", tenant_key=args.tenant_key)
    if command == "trace":
        return await _request(client, "GET", f"/apim/trace/{args.trace_id}", tenant_key=args.tenant_key)
    if command == "replay":
        body: dict[str, Any] = {
            "method": args.method,
            "path": args.path,
            "query": _parse_kv_pairs(args.query, flag="--query"),
            "headers": _parse_kv_pairs(args.header, flag="--header"),
        }
        if args.body_text is not None:
            body["body_text"] = args.body_text
        if args.body_base64 is not None:
            body["body_base64"] = args.body_base64
        return await _request(client, "POST", "/apim/management/replay", tenant_key=args.tenant_key, json_body=body)
    raise SystemExit(f"error: unknown command {command!r}")  # pragma: no cover - argparse enforces valid choices


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return json.dumps(payload)


async def _run(
    args: argparse.Namespace,
    transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None,
) -> int:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout, transport=transport) as client:
        try:
            response = await _dispatch(args, client)
        except CliUsageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except httpx.TransportError as exc:
            print(f"error: could not reach {args.base_url}: {exc}", file=sys.stderr)
            return 2

    if response.status_code >= 400:
        print(f"error: {response.status_code} {_error_detail(response)}", file=sys.stderr)
        return 1

    print(json.dumps(response.json(), indent=2))
    return 0


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run(args, transport))


if __name__ == "__main__":
    raise SystemExit(main())
