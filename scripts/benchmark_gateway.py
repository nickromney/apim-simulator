"""Measure local gateway CPU work with deterministic, in-memory HTTP transports.

Run with ``uv run --extra dev python scripts/benchmark_gateway.py``. The mock
backend removes network latency; these numbers are not Azure performance claims.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import json
import logging
import math
import resource
import sys
import time
from pathlib import Path
from xml.sax.saxutils import quoteattr

import httpx

from app.config import ApiConfig, ApiVersioningScheme, ApiVersionSetConfig, GatewayConfig, OperationConfig, RouteConfig
from app.main import create_app


def _expression_policy() -> str:
    expressions = [
        'context.Request.Headers.GetValueOrDefault("X-Client", "unknown")',
        'context.Request.Headers.GetValueOrDefault("X-Region", "local").ToUpper()',
        'context.Request.Url.Query.GetValueOrDefault("page", "1")',
        "context.Request.Method.ToLower()",
        "context.Request.Body.As<string>(preserveContent: true)",
        'context.Variables.GetValueOrDefault("client", "unknown")',
    ]
    variables = "".join(
        f'<set-variable name="{name}" value={quoteattr(f"@({expression})")} />'
        for name, expression in zip(["client", "region", "page", "method", "body", "copy"], expressions, strict=True)
    )
    return (
        f"<policies><inbound><base />{variables}"
        '<choose><when condition=\'@(context.Variables["region"] == "LOCAL")\'>'
        '<set-header name="X-Client-Label" exists-action="override">'
        '<value>@(context.Variables["client"].ToUpper())</value></set-header>'
        "</when></choose></inbound><backend><base /></backend>"
        '<outbound><base /><set-header name="X-Page" exists-action="override">'
        '<value>@(context.Variables["page"])</value></set-header></outbound></policies>'
    )


def _backend(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=request.content, headers={"content-type": "application/json"})


def _gateway_config(scenario: str, routes: int) -> GatewayConfig:
    if scenario == "versioned-routes":
        apis = {
            version: ApiConfig(
                name=f"Catalog {version}",
                path="catalog",
                upstream_base_url="http://backend.test",
                api_version_set="catalog",
                api_version=version,
                operations={
                    f"resource-{index}": OperationConfig(
                        name=f"Resource {index}",
                        method="GET",
                        url_template=f"/resource-{index}/{{id}}?expand={{expand}}",
                    )
                    for index in range(routes // 2)
                },
            )
            for version in ("v1", "v2")
        }
        return GatewayConfig(
            allow_anonymous=True,
            apis=apis,
            api_version_sets={
                "catalog": ApiVersionSetConfig(
                    display_name="Catalog",
                    versioning_scheme=ApiVersioningScheme.Header,
                    version_header_name="api-version",
                )
            },
        )
    route = RouteConfig(name="bench", path_prefix="/api", upstream_base_url="http://backend.test")
    if scenario == "expressions":
        route.policies_xml = _expression_policy()
    return GatewayConfig(allow_anonymous=True, routes=[route])


def _request_details(scenario: str, routes: int) -> tuple[str, str, bytes, dict[str, str]]:
    headers = {"X-Client": "demo", "X-Region": "local", "X-Correlation-Id": "benchmark"}
    if scenario == "versioned-routes":
        headers["api-version"] = "v2"
        return "GET", f"/catalog/resource-{routes // 2 - 1}/42?expand=details", b"", headers
    return "POST", "/api/echo?page=2", b'{"message":"hello"}', headers


def _percentile(samples: list[float], fraction: float) -> float:
    return sorted(samples)[max(0, math.ceil(len(samples) * fraction) - 1)] * 1000


async def _measure(scenario: str, requests: int, profile_path: Path | None, routes: int) -> dict:
    cfg = _gateway_config(scenario, routes)
    method, url, payload, headers = _request_details(scenario, routes)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_backend)) as backend:
        app = create_app(config=cfg, http_client=backend)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
            ) as client:
                for _ in range(20):
                    response = await client.request(method, url, content=payload, headers=headers)
                    response.raise_for_status()
                profile = cProfile.Profile() if profile_path else None
                if profile:
                    profile.enable()
                samples = []
                start = time.perf_counter()
                for _ in range(requests):
                    request_start = time.perf_counter()
                    response = await client.request(method, url, content=payload, headers=headers)
                    samples.append(time.perf_counter() - request_start)
                    assert response.status_code == 200 and response.content == payload
                elapsed = time.perf_counter() - start
                if profile:
                    profile.disable()
                    profile.dump_stats(profile_path)
    return {
        "scenario": scenario,
        "routes": routes if scenario == "versioned-routes" else 1,
        "requests": requests,
        "elapsed_seconds": elapsed,
        "requests_per_second": requests / elapsed,
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "p99_ms": _percentile(samples, 0.99),
        "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        / (1048576 if sys.platform == "darwin" else 1024),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["passthrough", "expressions", "versioned-routes"], default="expressions")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--routes", type=int, default=100, help="Even route count for the versioned-routes scenario")
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jsonl", type=Path, help="Append one result per run for repeated benchmarks")
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("--requests must be positive")
    if args.routes < 2 or args.routes % 2:
        parser.error("--routes must be a positive even number of at least two")
    logging.disable(logging.CRITICAL)
    result = asyncio.run(_measure(args.scenario, args.requests, args.profile, args.routes))
    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    if args.jsonl:
        with args.jsonl.open("a", encoding="utf-8") as file:
            file.write(json.dumps(result) + "\n")
    print(output, end="")


if __name__ == "__main__":
    main()
