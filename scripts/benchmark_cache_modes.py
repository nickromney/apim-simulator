"""Compare three bounded pure-helper caches using the same in-memory gateway.

This is measurement-only instrumentation. Uncached mode bypasses compilation,
path-segment and API-prefix LRU wrappers, while request-local routing reuse stays
unchanged. No Azure or remote backend requests are made.
"""

from __future__ import annotations

import argparse
import json
import sys
import tracemalloc
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@contextmanager
def cache_mode(mode):
    import app.apim_expr as expressions
    import app.config as config

    helpers = [(expressions, "_compile_expression"), (config, "_path_segments"), (config, "_matches_api_path")]
    originals = [getattr(module, name) for module, name in helpers]
    if mode not in {"cached", "uncached"}:
        raise ValueError(f"unknown cache mode: {mode}")
    try:
        with ExitStack() as stack:
            for (module, name), helper in zip(helpers, originals, strict=True):
                helper.cache_clear()
                if mode == "uncached":
                    stack.enter_context(patch.object(module, name, helper.__wrapped__))
            yield
    finally:
        for helper in originals:
            helper.cache_clear()


@contextmanager
def allocation_profile(report):
    if report is None:
        yield
        return
    tracemalloc.start()
    try:
        yield
        current, peak = tracemalloc.get_traced_memory()
        rows = []
        for entry in tracemalloc.take_snapshot().statistics("lineno"):
            frame = entry.traceback[0]
            filename = Path(frame.filename)
            if filename.is_relative_to(ROOT / "app"):
                rows.append(
                    {
                        "file": filename.relative_to(ROOT).as_posix(),
                        "line": frame.lineno,
                        "retained_bytes": entry.size,
                        "retained_blocks": entry.count,
                    }
                )
        report.write_text(
            json.dumps(
                {
                    "traced_current_bytes": current,
                    "traced_peak_bytes": peak,
                    "app_retained_at_workload_end": rows,
                    "scope": "Python allocations after imports through app construction/request loop/context cleanup; peak is not retained cache size",
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        tracemalloc.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--cache-mode", choices=["cached", "uncached"], default="cached")
    parser.add_argument("--varying-paths", action="store_true")
    parser.add_argument(
        "--allocation-report",
        type=Path,
        help="Separate tracemalloc run; excludes imports, includes app construction and gateway requests",
    )
    options, remaining = parser.parse_known_args()
    import httpx

    import app
    from scripts import benchmark_gateway as benchmark

    assert Path(app.__file__).resolve().parent.parent == ROOT, "benchmark imported a different app checkout"
    original_measure = benchmark._measure
    original_request = httpx.AsyncClient.request
    counter = 0

    async def request(client, method, url, *args, **kwargs):
        nonlocal counter
        if options.varying_paths and client.base_url.host == "gateway.test" and "/42?" in str(url):
            counter += 1
            url = str(url).replace("/42?", f"/{counter}?", 1)
        return await original_request(client, method, url, *args, **kwargs)

    async def measure(*args, **kwargs):
        with allocation_profile(options.allocation_report):
            result = await original_measure(*args, **kwargs)
        if options.varying_paths:
            assert counter == result["requests"] + 20, "varying URL workload did not run for every request"
        return {
            **result,
            "cache_mode": options.cache_mode,
            "varying_paths": options.varying_paths,
            "app_root": str(ROOT),
        }

    sys.argv = [str(ROOT / "scripts/benchmark_gateway.py"), *remaining]
    with (
        cache_mode(options.cache_mode),
        patch.object(httpx.AsyncClient, "request", request),
        patch.object(benchmark, "_measure", measure),
    ):
        benchmark.main()


if __name__ == "__main__":
    main()
