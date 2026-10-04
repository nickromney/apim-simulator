"""Run the same gateway benchmark with a unique parameter ID per request.

Examples (run only while other timing workloads are idle):
  uv run --extra dev python scripts/benchmark_varying_gateway.py \
    --app-root /private/tmp/apim-baseline --scenario versioned-routes \
    --requests 1000 --routes 100 --jsonl /private/tmp/varying-before.jsonl
  uv run --extra dev python scripts/benchmark_varying_gateway.py \
    --scenario versioned-routes --requests 1000 --routes 100 \
    --jsonl /private/tmp/varying-after.jsonl
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--app-root", type=Path, default=REPO)
    parser.add_argument("--harness", type=Path, default=REPO / "scripts/benchmark_gateway.py")
    args, benchmark_args = parser.parse_known_args()
    if not (args.app_root / "app").is_dir():
        parser.error("--app-root must contain an app directory")
    sys.path.insert(0, str(args.app_root))
    import httpx

    original = httpx.AsyncClient.request
    counter = 0

    async def varying_request(client, method, url, *positional, **kwargs):
        nonlocal counter
        if client.base_url.host == "gateway.test" and "/42?" in str(url):
            counter += 1
            url = str(url).replace("/42?", f"/{counter}?", 1)
        return await original(client, method, url, *positional, **kwargs)

    sys.argv = [str(args.harness), *benchmark_args]
    httpx.AsyncClient.request = varying_request
    try:
        runpy.run_path(str(args.harness), run_name="__main__")
        assert counter > 20, "The harness did not execute the varying-ID routing workload"
    finally:
        httpx.AsyncClient.request = original


if __name__ == "__main__":
    main()
