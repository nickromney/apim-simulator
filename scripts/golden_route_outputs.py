"""Emit deterministic route-selection evidence for performance changes.

Run from the repository root with ``uv run --extra dev python -m
scripts.golden_route_outputs --output /private/tmp/apim-routing-golden.json``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request

from app.config import ApiConfig, ApiVersioningScheme, ApiVersionSetConfig, GatewayConfig, RouteConfig
from app.proxy import resolve_route


def _route(name: str, template: str, version: str | None = "v2", **kwargs) -> RouteConfig:
    prefix = kwargs.pop("api_path_prefix", "/catalog")
    return RouteConfig(
        name=name,
        path_prefix=prefix,
        api_path_prefix=prefix,
        url_template=template,
        upstream_base_url="http://backend.test",
        api_version_set="public",
        api_version=version,
        **kwargs,
    )


def _config(scheme: ApiVersioningScheme) -> GatewayConfig:
    return GatewayConfig(
        allow_anonymous=True,
        api_version_sets={
            "public": ApiVersionSetConfig(
                display_name="Public",
                versioning_scheme=scheme,
                version_header_name="api-version",
                version_query_name="version",
            )
        },
        apis={
            "offline": ApiConfig(
                name="Offline", path="catalog", upstream_base_url="http://backend.test", is_online=False
            )
        },
        routes=[
            _route("admin", "/items/me", host_match=["admin.test"], methods=["GET"]),
            _route("parameter", "/items/{id}", methods=["GET"]),
            _route("literal-first", "/items/me", methods=["GET"]),
            _route("literal-tie", "/items/me", methods=["GET"]),
            _route("literal-other-case", "/ITEMS/ME", api_path_prefix="/CATALOG", methods=["GET"]),
            _route("post", "/items/{id}", methods=["POST"]),
            _route("query-literal", "/search?term=fixed", methods=["GET"]),
            _route("query-parameter", "/search?term={term}", methods=["GET"]),
            _route("wildcard", "/items/{*rest}", methods=["GET"]),
            _route("v1", "/items/{id}", "v1", methods=["GET"]),
            _route("original", "/items/{id}", None, methods=["GET"]),
            _route("nested-prefix", "/items/{id}", api_path_prefix="/catalog/child", methods=["GET"]),
            _route("https", "/secure", api_protocols=["https"], methods=["GET"]),
            _route("offline", "/offline", api_id="offline", methods=["GET"]),
        ],
    )


def _request(path: str, *, query=None, headers=None, method="GET", scheme="http") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": scheme,
            "path": path,
            "raw_path": path.encode(),
            "query_string": urlencode(query or [], doseq=True).encode(),
            "headers": [
                (key.lower().encode(), value.encode())
                for key, value in {"host": "public.test", **(headers or {})}.items()
            ],
            "server": ("public.test", 80),
            "client": ("127.0.0.1", 12345),
        }
    )


def _output(config: GatewayConfig, label: str, path: str, **kwargs) -> dict:
    request = _request(path, **kwargs)
    result = resolve_route(config, request)
    selected = None
    if result is not None:
        selected = {
            "route": result.route.name,
            "upstream_path": result.upstream_path,
            "api_version": result.api_version,
            "parameters": result.matched_parameters,
            "query_parameters": sorted(result.matched_query_parameters),
        }
    return {"case": label, "path": path, "normalized_path": request.scope["path"], "result": selected}


def _version_selector(
    versioning_scheme: ApiVersioningScheme, suffix: str, version: str | None, **kwargs
) -> tuple[str, dict]:
    query = list(kwargs.pop("query", []))
    headers = dict(kwargs.pop("headers", {}))
    prefix = kwargs.pop("prefix", "/catalog")
    if version is not None and versioning_scheme == ApiVersioningScheme.Header:
        headers["api-version"] = version
    if version is not None and versioning_scheme == ApiVersioningScheme.Query:
        query.append(("version", version))
    if version is not None and versioning_scheme == ApiVersioningScheme.Segment:
        prefix += f"/{version}"
    return prefix + suffix, {"query": query, "headers": headers, **kwargs}


def _scheme_outputs(scheme: ApiVersioningScheme) -> list[dict]:
    config = _config(scheme)
    cases = [
        ("literal-precedence-and-first-tie", "/items/me", "v2", {}),
        ("parameter", "/items/42", "v2", {}),
        ("method", "/items/42", "v2", {"method": "POST"}),
        ("wildcard", "/items/a/b", "v2", {}),
        (
            "case-and-trailing-slash",
            "/ITEMS/ME/",
            "V2" if scheme == ApiVersioningScheme.Segment else "v2",
            {"prefix": "/CATALOG"},
        ),
        ("query-literal", "/search", "v2", {"query": [("term", "fixed")]}),
        ("query-parameter-first-repeated", "/search", "v2", {"query": [("term", "first"), ("term", "second")]}),
        ("query-missing", "/search", "v2", {}),
        ("v1", "/items/42", "v1", {}),
        ("original", "/items/42", None, {}),
        ("unknown-version", "/items/42", "v3", {}),
        ("forwarded-host-first", "/items/me", "v2", {"headers": {"x-forwarded-host": "admin.test"}}),
        ("forwarded-host-fallback", "/items/me", "v2", {"headers": {"x-forwarded-host": "unknown.test"}}),
        ("different-prefix-same-version-set", "/items/42", "v2", {"prefix": "/catalog/child"}),
        ("protocol-denied", "/secure", "v2", {}),
        ("protocol-allowed", "/secure", "v2", {"scheme": "https"}),
        ("forwarded-protocol", "/secure", "v2", {"headers": {"x-forwarded-proto": "https,http"}}),
        ("offline-api", "/offline", "v2", {}),
        ("wrong-method", "/items/42", "v2", {"method": "DELETE"}),
    ]
    outputs = []
    for label, suffix, version, kwargs in cases:
        path, arguments = _version_selector(scheme, suffix, version, **kwargs)
        outputs.append(_output(config, f"{scheme.value}:{label}", path, **arguments))
    no_original = config.model_copy(
        update={"routes": [route for route in config.routes if route.api_version is not None]}
    )
    outputs.append(_output(no_original, f"{scheme.value}:missing-version-without-original", "/catalog/items/42"))
    no_version_set = config.model_copy(update={"api_version_sets": {}})
    outputs.append(_output(no_version_set, f"{scheme.value}:missing-version-set", "/catalog/items/42"))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write JSON here instead of stdout")
    args = parser.parse_args()
    payload = [row for scheme in ApiVersioningScheme for row in _scheme_outputs(scheme)]
    output = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
