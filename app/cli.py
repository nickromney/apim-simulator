from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 10.0


class CliUsageError(Exception):
    """A command-level validation failure (bad flags, unreadable files) reported as exit code 1."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if message.startswith("unrecognized arguments:") and any(
            option.startswith("--")
            and len(option) > 2
            and any(flag.startswith(option.split("=", 1)[0]) for flag in ("--tenant-key", "--bearer-token"))
            for option in message.split()[2:]
        ):
            message = "credential options must precede the command"
        super().error(message)


_BUNDLE_WRITE_COMMANDS = frozenset({"plan-product", "apply-product", "plan-service", "apply-service"})


def _add_service_artifact_parsers(subparsers: Any) -> None:
    subparsers.add_parser("extract-service", help="Extract a whole-service JSON artifact.")
    for name in ("plan-service", "apply-service"):
        artifact_parser = subparsers.add_parser(name, help="Plan or apply a whole-service JSON artifact.")
        artifact_parser.add_argument("--file", required=True, help="Path to the service artifact JSON file.")
        artifact_parser.add_argument("--overrides", help="Path to environment override JSON.")
        if name == "apply-service":
            artifact_parser.add_argument("--if-match", required=True, help="Specific strong ETag from plan-service.")


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="apimsim",
        description="Thin HTTP client for the APIM simulator management API.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("APIM_BASE_URL", DEFAULT_BASE_URL),
        help=f"Simulator base URL (env APIM_BASE_URL, default {DEFAULT_BASE_URL}).",
    )
    credentials = parser.add_mutually_exclusive_group()
    credentials.add_argument(
        "--tenant-key",
        help="Tenant key sent as X-Apim-Tenant-Key (env APIM_TENANT_KEY).",
    )
    credentials.add_argument("--bearer-token", help="Bearer token sent as Authorization (env APIM_BEARER_TOKEN).")
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("APIM_TIMEOUT", DEFAULT_TIMEOUT)),
        help=f"Request timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="Print the request plan without sending HTTP (before the command)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inspect", help="Collect bounded read-only metadata, preserving partial failures as JSON.")
    subparsers.add_parser("commands", help="Describe CLI command effects as JSON without contacting the gateway.")
    subparsers.add_parser("status", help="Show management status (counts, gateway policy scope).")
    subparsers.add_parser("summary", help="Show management summary (routes, gateway policy scope).")
    subparsers.add_parser("service", help="Show service metadata.")

    subparsers.add_parser("apis", help="List APIs.")
    api_parser = subparsers.add_parser("api", help="Show one API.")
    api_parser.add_argument("api_id")

    extract_parser = subparsers.add_parser("extract-api", help="Extract a whole-API JSON artifact.")
    extract_parser.add_argument("api_id")
    for name in ("plan-api", "apply-api"):
        artifact_parser = subparsers.add_parser(name, help="Plan or apply a whole-API JSON artifact.")
        artifact_parser.add_argument("api_id")
        artifact_parser.add_argument("--file", required=True, help="Path to the API artifact JSON file.")
        artifact_parser.add_argument("--upstream-base-url", help="Override the imported upstream URL.")
        if name == "apply-api":
            artifact_parser.add_argument("--if-match", required=True, help="Specific strong ETag from plan-api.")

    operations_parser = subparsers.add_parser("operations", help="List operations for one API.")
    operations_parser.add_argument("api_id")

    subparsers.add_parser("products", help="List products.")
    product_parser = subparsers.add_parser("product", help="Show one product.")
    product_parser.add_argument("product_id")

    extract_product_parser = subparsers.add_parser("extract-product", help="Extract a whole-product JSON artifact.")
    extract_product_parser.add_argument("product_id")
    for name in ("plan-product", "apply-product"):
        artifact_parser = subparsers.add_parser(name, help="Plan or apply a whole-product JSON artifact.")
        artifact_parser.add_argument("product_id")
        artifact_parser.add_argument("--file", required=True, help="Path to the product artifact JSON file.")
        artifact_parser.add_argument("--overrides", help="Path to environment override JSON.")
        if name == "apply-product":
            artifact_parser.add_argument("--if-match", required=True, help="Specific strong ETag from plan-product.")

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

    _add_service_artifact_parsers(subparsers)

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
    policy_parser.add_argument(
        "--effective", action="store_true", help="Inspect composed policy including inheritance and fragments."
    )
    policy_parser.add_argument(
        "--product-id", help="Product context for effective policy inspection (requires --effective)."
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

    for name, help_text in (
        ("plan-policy", "Validate and preview an API policy against current server state."),
        ("apply-policy", "Replace an API policy only if its ETag still matches."),
    ):
        policy_command_parser = subparsers.add_parser(name, help=help_text)
        policy_command_parser.add_argument("api_id")
        policy_command_parser.add_argument("--file", required=True, help="Path to a file containing the policy XML.")
        if name == "apply-policy":
            policy_command_parser.add_argument(
                "--if-match", required=True, help="Specific strong ETag from plan-policy."
            )

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


def _headers(tenant_key: str | None, bearer_token: str | None) -> dict[str, str]:
    if bearer_token is not None:
        return {"Authorization": f"Bearer {bearer_token}"}
    return {"X-Apim-Tenant-Key": tenant_key} if tenant_key else {}


def _read_text_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
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
    bearer_token: str | None,
    json_body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.request(
        method, path, headers={**_headers(tenant_key, bearer_token), **(headers or {})}, json=json_body, params=params
    )


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


def _api_artifact_body(args: argparse.Namespace) -> dict[str, Any]:
    body = {"artifact": _read_json_file(args.file)}
    if args.upstream_base_url is not None:
        body["upstream_base_url"] = args.upstream_base_url
    return body


def _bundle_artifact_body(args: argparse.Namespace) -> dict[str, Any]:
    body = {"artifact": _read_json_file(args.file)}
    if args.overrides is not None:
        body["overrides"] = _read_json_file(args.overrides)
    return body


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
    read_only: bool = False
    requires_etag: bool = False

    @property
    def effect(self) -> str:
        if self.method == "GET" or self.read_only:
            return "read"
        return "execute" if self.path == "/apim/management/replay" else "write"


_COMMANDS: dict[str, _Command] = {
    "status": _Command("GET", "/apim/management/status"),
    "summary": _Command("GET", "/apim/management/summary"),
    "service": _Command("GET", "/apim/management/service"),
    "apis": _Command("GET", "/apim/management/apis"),
    "api": _Command("GET", "/apim/management/apis/{api_id}"),
    "extract-api": _Command("GET", "/apim/management/apis/{api_id}/artifacts"),
    "plan-api": _Command("POST", "/apim/management/apis/{api_id}/artifacts", body=_api_artifact_body, read_only=True),
    "apply-api": _Command(
        "PUT", "/apim/management/apis/{api_id}/artifacts", body=_api_artifact_body, requires_etag=True
    ),
    "operations": _Command("GET", "/apim/management/apis/{api_id}/operations"),
    "put-api": _Command("PUT", "/apim/management/apis/{api_id}", body=_json_file_body),
    "delete-api": _Command("DELETE", "/apim/management/apis/{api_id}", confirm="API {api_id!r}"),
    "import-openapi": _Command("POST", "/apim/management/apis/{api_id}/import", body=_import_openapi_body),
    "products": _Command("GET", "/apim/management/products"),
    "product": _Command("GET", "/apim/management/products/{product_id}"),
    "extract-product": _Command("GET", "/apim/management/products/{product_id}/artifacts"),
    "plan-product": _Command(
        "POST", "/apim/management/products/{product_id}/artifacts", body=_bundle_artifact_body, read_only=True
    ),
    "apply-product": _Command(
        "PUT", "/apim/management/products/{product_id}/artifacts", body=_bundle_artifact_body, requires_etag=True
    ),
    "extract-service": _Command("GET", "/apim/management/service/artifacts"),
    "plan-service": _Command("POST", "/apim/management/service/artifacts", body=_bundle_artifact_body, read_only=True),
    "apply-service": _Command(
        "PUT", "/apim/management/service/artifacts", body=_bundle_artifact_body, requires_etag=True
    ),
    "put-product": _Command("PUT", "/apim/management/products/{product_id}", body=_json_file_body),
    "delete-product": _Command("DELETE", "/apim/management/products/{product_id}", confirm="product {product_id!r}"),
    "subscriptions": _Command("GET", "/apim/management/subscriptions"),
    "policy": _Command("GET", "/apim/management/policies/{scope_type}/{scope_name}"),
    "set-policy": _Command("PUT", "/apim/management/policies/{scope_type}/{scope_name}", body=_set_policy_body),
    "plan-policy": _Command("POST", "/apim/management/policies/api/{api_id}", body=_set_policy_body, read_only=True),
    "apply-policy": _Command(
        "PUT", "/apim/management/policies/api/{api_id}", body=_set_policy_body, requires_etag=True
    ),
    "traces": _Command("GET", "/apim/management/traces"),
    "trace": _Command("GET", "/apim/trace/{trace_id}"),
    "replay": _Command("POST", "/apim/management/replay", body=_replay_body),
}


_INSPECTIONS = {
    "health": "/apim/health",
    "status": "/apim/management/status",
    "service": "/apim/management/service",
    "apis": "/apim/management/apis",
    "products": "/apim/management/products",
}


def _inspection_plan() -> dict[str, Any]:
    return {
        "effect": "read",
        "atomic": False,
        "requires_confirmation": False,
        "requests": [{"name": name, "method": "GET", "path": path} for name, path in _INSPECTIONS.items()],
    }


async def _inspect(args: argparse.Namespace, client: httpx.AsyncClient) -> int:
    observations = {}
    complete = True
    for name, path in _INSPECTIONS.items():
        try:
            response = await _request(
                client,
                "GET",
                path,
                tenant_key=args.tenant_key if name != "health" else None,
                bearer_token=args.bearer_token if name != "health" else None,
            )
            try:
                body = response.json()
            except ValueError:
                body = response.text
            if not response.is_success:
                body = _redact_value(body, args)
            observations[name] = {"path": path, "status_code": response.status_code, "body": body}
            complete = complete and response.is_success
        except httpx.HTTPError:
            observations[name] = {"path": path, "error": "transport_error"}
            complete = False
    print(
        json.dumps({"schema_version": 1, "atomic": False, "complete": complete, "observations": observations}, indent=2)
    )
    return 0 if complete else 1


def _plan_headers(command: _Command, args: argparse.Namespace) -> dict[str, str]:
    if not command.requires_etag:
        return {}
    if not re.fullmatch(r'"[\x21\x23-\x7e]*"', args.if_match):
        raise CliUsageError("--if-match must be a specific strong quoted ETag")
    return {"If-Match": args.if_match}


def _request_plan(args: argparse.Namespace) -> dict[str, Any]:
    base_url = str(httpx.URL(args.base_url).copy_with(username=None, password=None))
    if args.command == "inspect":
        return {"schema_version": 1, "command": "inspect", "base_url": base_url, **_inspection_plan()}
    command = _COMMANDS[args.command]
    fields = {key: quote(str(value), safe="") for key, value in vars(args).items()}
    params = {}
    if args.command == "policy":
        if args.product_id and not args.effective:
            raise CliUsageError("--product-id requires --effective")
        if args.effective:
            params["effective"] = "true"
        if args.product_id:
            params["product_id"] = args.product_id
    headers = _plan_headers(command, args)
    plan = {
        "schema_version": 1,
        "command": args.command,
        "base_url": base_url,
        "method": command.method,
        "path": command.path.format(**fields),
        "params": params,
        "body": command.body(args) if command.body is not None else None,
        "effect": command.effect,
        "requires_confirmation": command.confirm is not None,
    }
    if headers:
        plan["headers"] = headers
    return plan


async def _dispatch(args: argparse.Namespace, client: httpx.AsyncClient) -> httpx.Response:
    """Execute the same request described by the offline plan."""
    command = _COMMANDS[args.command]
    if command.confirm is not None and not args.yes:
        raise CliUsageError(f"refusing to delete {command.confirm.format(**vars(args))} without --yes")
    plan = _request_plan(args)
    if args.command in _BUNDLE_WRITE_COMMANDS:
        args._bundle_request_body = plan["body"]
    return await _request(
        client,
        plan["method"],
        plan["path"],
        tenant_key=args.tenant_key,
        bearer_token=args.bearer_token,
        json_body=plan["body"],
        params=plan["params"],
        headers=plan.get("headers"),
    )


def _redact_credentials(value: str, args: argparse.Namespace) -> str:
    for secret in (args.tenant_key, args.bearer_token):
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def _redact_value(value: Any, args: argparse.Namespace) -> Any:
    if isinstance(value, str):
        return _redact_credentials(value, args)
    if isinstance(value, list):
        return [_redact_value(item, args) for item in value]
    if isinstance(value, dict):
        return {_redact_credentials(key, args): _redact_value(item, args) for key, item in value.items()}
    return value


def _error_detail(response: httpx.Response, args: argparse.Namespace) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    payload = _redact_value(payload, args)
    if isinstance(payload, dict) and "detail" in payload:
        return str(payload["detail"])
    return json.dumps(payload)


def _bundle_error_detail(response: httpx.Response, args: argparse.Namespace) -> str:
    generic = f"{'service' if args.command.endswith('service') else 'product'} artifact request failed"
    try:
        payload = response.json()
    except ValueError:
        return generic
    if not isinstance(payload, dict) or not isinstance(payload.get("detail"), str):
        return generic
    detail = payload["detail"]
    safe = {
        "Invalid product artifact request; check artifact and override field types",
        "Invalid service artifact request; check artifact and override field types",
        "Invalid policy or configuration in the service artifact",
        "Cannot import APIs into a legacy route-only configuration",
        "API product links must reference bundle products",
        "Removing product API links is not supported",
        "Operation-level backend settings cannot be represented; move routing to the API",
        "Legacy route-only configuration cannot be represented as a service artifact",
        "API product links reference missing products",
        "Product not found",
        "Overrides must refer to bundle dependencies",
        "Backend override must be a URL",
        "Use a backend override for APIs that reference a named backend",
        "An API switching to direct routing requires an upstream override",
        "upstream_base_url is required for a new API",
        "upstream_base_url must be an absolute HTTP(S) URL without credentials, query, or fragment",
        "Unsupported artifact schema version",
        "Artifact contains a known secret",
        "Removing product API links is not supported; include all currently linked APIs",
        "Operation-level product membership cannot be represented; move membership to the API",
        "Invalid product artifact metadata",
        "Target API has operation-level product membership; move membership to the API",
        "Bundle dependencies do not match its policies and APIs",
        "Invalid policy or named value override in the product artifact",
        "Invalid config update",
        "If-Match is required",
        "The configuration changed. Plan again before applying.",
    }
    body = args._bundle_request_body
    if detail not in safe and not _is_dependency_error(detail, body):
        return generic
    if response.status_code == 422:
        return generic
    return _redact_bundle_overrides(detail, body, args)


def _is_dependency_error(detail: str, body: dict[str, Any]) -> bool:
    artifact = body.get("artifact", {})
    if not isinstance(artifact, dict):
        return False
    for kind, label in (("backends", "Backend"), ("named_values", "Named value")):
        dependencies = artifact.get(kind, {})
        if isinstance(dependencies, (dict, list)):
            if any(detail == f"{label} {identifier} requires an override" for identifier in dependencies):
                return True
    return False


def _redact_bundle_overrides(detail: str, body: dict[str, Any], args: argparse.Namespace) -> str:
    overrides = body.get("overrides", {})
    if isinstance(overrides, dict):
        for group in overrides.values():
            if isinstance(group, dict):
                for value in group.values():
                    if isinstance(value, str) and value:
                        detail = detail.replace(value, "[redacted]")
    return _redact_credentials(detail, args)


async def _run(
    args: argparse.Namespace,
    transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None,
) -> int:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout, transport=transport) as client:
        if args.command == "inspect":
            return await _inspect(args, client)
        try:
            response = await _dispatch(args, client)
        except CliUsageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except httpx.TransportError as exc:
            detail = "transport error" if args.command in _BUNDLE_WRITE_COMMANDS else str(exc)
            print(f"error: could not reach simulator: {_redact_credentials(detail, args)}", file=sys.stderr)
            return 2

    if response.status_code >= 400:
        if args.command in _BUNDLE_WRITE_COMMANDS:
            print(f"error: {response.status_code} {_bundle_error_detail(response, args)}", file=sys.stderr)
            return 1
        print(
            f"error: {response.status_code} {_redact_credentials(_error_detail(response, args), args)}", file=sys.stderr
        )
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
    if args.command == "commands":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "commands": {
                        name: {
                            "method": command.method,
                            "path_template": command.path,
                            "effect": command.effect,
                            "requires_confirmation": command.confirm is not None,
                            **({"required_headers": ["If-Match"]} if command.requires_etag else {}),
                        }
                        for name, command in _COMMANDS.items()
                    }
                    | {"inspect": _inspection_plan()},
                },
                indent=2,
            )
        )
        return 0
    if args.dry_run:
        try:
            plan = _request_plan(args)
            if args.command in _BUNDLE_WRITE_COMMANDS and "overrides" in plan["body"]:
                plan["body"]["overrides"] = "[redacted]"
            print(json.dumps(plan, indent=2))
        except CliUsageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.tenant_key is None and args.bearer_token is None:
        args.tenant_key = os.environ.get("APIM_TENANT_KEY")
        args.bearer_token = os.environ.get("APIM_BEARER_TOKEN")
        if args.tenant_key is not None and args.bearer_token is not None:
            parser.error("APIM_TENANT_KEY and APIM_BEARER_TOKEN cannot both be set")
    if args.bearer_token is not None and (
        not args.bearer_token or any(not 33 <= ord(char) <= 126 for char in args.bearer_token)
    ):
        print("error: bearer token must contain only visible ASCII characters", file=sys.stderr)
        return 1
    return asyncio.run(_run(args, transport))


if __name__ == "__main__":
    raise SystemExit(main())
