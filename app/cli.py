from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
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


def _import_openapi_body(args: argparse.Namespace) -> dict[str, Any]:
    """Body for an OpenAPI import, from either a local file or a URL.

    A .json file is `openapi+json`; anything else read from disk is treated as
    YAML `openapi`. A URL is never fetched here, it is handed to the gateway as
    `openapi-link`.
    """
    if args.file is not None:
        suffix = Path(args.file).suffix.lower()
        content_format = "openapi+json" if suffix == ".json" else "openapi"
        content_value = _read_text_file(args.file)
    else:
        content_format = "openapi-link"
        content_value = args.url

    body: dict[str, Any] = {"content_format": content_format, "content_value": content_value}
    if args.api_name is not None:
        body["name"] = args.api_name
    if args.api_path is not None:
        body["path"] = args.api_path
    return body


def _set_policy_body(args: argparse.Namespace) -> dict[str, Any]:
    return {"xml": _read_text_file(args.file) if args.file is not None else args.xml}


def _replay_body(args: argparse.Namespace) -> dict[str, Any]:
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
    return body


def _json_file_body(args: argparse.Namespace) -> Any:
    return _read_json_file(args.file)


@dataclass(frozen=True)
class _Command:
    """One CLI verb, as the management-plane call it turns into.

    `path` is formatted against the parsed arguments, so a template names the
    argparse destinations it needs. `confirm` names the thing being destroyed;
    a command that sets it refuses to run without --yes.
    """

    method: str
    path: str
    body: Callable[[argparse.Namespace], Any] | None = None
    confirm: str | None = None


_COMMANDS: dict[str, _Command] = {
    "status": _Command("GET", "/apim/management/status"),
    "summary": _Command("GET", "/apim/management/summary"),
    "service": _Command("GET", "/apim/management/service"),
    "apis": _Command("GET", "/apim/management/apis"),
    "api": _Command("GET", "/apim/management/apis/{api_id}"),
    "operations": _Command("GET", "/apim/management/apis/{api_id}/operations"),
    "put-api": _Command("PUT", "/apim/management/apis/{api_id}", body=_json_file_body),
    "delete-api": _Command("DELETE", "/apim/management/apis/{api_id}", confirm="API {api_id!r}"),
    "import-openapi": _Command("POST", "/apim/management/apis/{api_id}/import", body=_import_openapi_body),
    "products": _Command("GET", "/apim/management/products"),
    "product": _Command("GET", "/apim/management/products/{product_id}"),
    "put-product": _Command("PUT", "/apim/management/products/{product_id}", body=_json_file_body),
    "delete-product": _Command("DELETE", "/apim/management/products/{product_id}", confirm="product {product_id!r}"),
    "subscriptions": _Command("GET", "/apim/management/subscriptions"),
    "policy": _Command("GET", "/apim/management/policies/{scope_type}/{scope_name}"),
    "set-policy": _Command("PUT", "/apim/management/policies/{scope_type}/{scope_name}", body=_set_policy_body),
    "traces": _Command("GET", "/apim/management/traces"),
    "trace": _Command("GET", "/apim/trace/{trace_id}"),
    "replay": _Command("POST", "/apim/management/replay", body=_replay_body),
}


async def _dispatch(args: argparse.Namespace, client: httpx.AsyncClient) -> httpx.Response:
    """Turn the parsed command line into one management-plane call."""
    command = _COMMANDS.get(args.command)
    if command is None:  # pragma: no cover - argparse enforces valid choices
        raise SystemExit(f"error: unknown command {args.command!r}")

    fields = vars(args)
    if command.confirm is not None and not args.yes:
        raise CliUsageError(f"refusing to delete {command.confirm.format(**fields)} without --yes")

    return await _request(
        client,
        command.method,
        command.path.format(**fields),
        tenant_key=args.tenant_key,
        json_body=command.body(args) if command.body is not None else None,
    )


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
