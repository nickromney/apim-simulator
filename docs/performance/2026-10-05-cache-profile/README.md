# Local cache profile, 2026-10-05

This is a fresh measurement and correctness baseline before considering further optimization. No production gateway behavior changes in this PR. Synthetic HTTPX ASGI and MockTransport requests stay in memory; these results make no Azure or remote-runtime performance claim.

The `cached` condition retains the existing bounded `_compile_expression`, `_path_segments`, and `_matches_api_path` LRU wrappers. `uncached` bypasses exactly those three wrappers through their `__wrapped__` functions. Request-local routing reuse remains enabled in both conditions. The context clears original caches at entry and exit and restores wrappers after exceptions. Each process asserts it imported this checkout's app.

The expressions workload uses one route and the existing policy/header/body expressions. The versioned-routing workload uses 100 routes with varying resource IDs, so it does not measure one repeatedly cached literal URL. Every process executes 20 internal request warmups before timing 1,000 requests, asserting status and response payload for every measured request.

Each condition has three discarded process warmups followed by ten measured process runs: 52 serial process runs total. `runs.json` retains all rows with `warmup` flags, and the collection asserts ten measured rows per condition. Conditions ran in the order shown below. The table takes the median of each per-run metric; it is not a pooled request percentile.

| Scenario | Mode | requests/s | p50 ms | p95 ms | p99 ms | peak RSS MiB | start 1-minute load |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| expressions | uncached | 342.310 | 2.309 | 5.719 | 8.138 | 101.500 | 7.17–10.36 |
| expressions | cached | 767.289 | 1.147 | 2.178 | 3.431 | 101.664 | 10.47–12.29 |
| versioned-routes | uncached | 527.391 | 1.659 | 3.237 | 4.989 | 102.070 | 8.49–9.36 |
| versioned-routes | cached | 819.059 | 1.112 | 1.582 | 2.405 | 102.531 | 6.74–7.90 |

This shared Apple M4 macOS host had other portfolio work running. In particular, Platform's mocked host gate overlapped the early conditions and finished during collection. Recorded load differs by condition, and conditions were grouped rather than randomized. These figures establish an observed local baseline, not a causal cache speedup, regression threshold, or isolated comparison. No CPU affinity, power/governor tuning, network, database, or deployment was introduced. Peak RSS is whole-process high-water RSS, not retained cache memory or allocation attribution.

`profiles.json` contains one separate cProfile run per condition with the same request inputs. Profiled timings are excluded from the table. It ranks overall cumulative, application cumulative, and application self time. Cumulative parents overlap and must not be summed; coroutine resumes also affect call counts. The expression trace puts policy evaluation under the gateway request pipeline; the versioned trace puts route candidate resolution under admission. Those are inspection leads, not evidence to change an optimizer. Allocation, I/O, lock contention, and off-CPU sampling were not collected.

The new correctness tests run four workers with independently owned routes through 40 changing prefix generations and coordinated cache eviction. They check fresh parameter dictionaries and stale-prefix misses in both cache modes. This proves eviction at controlled barriers with concurrent workers; it does not claim atomic shared configuration mutation during a request. Both modes also reproduce the committed policy and route golden bytes, rather than comparing two implementations that share an oracle.

Verification: all 1,194 non-integration tests passed (one integration test deselected), including seven new cases; Ruff check and format check pass. The existing Starlette TestClient deprecation warning remains. No live Keycloak integration test ran.

Reproduce from the checkout root with the frozen offline development environment:

```sh
uv run --frozen --offline --extra dev pytest -q -m 'not integration'
uv run --frozen --offline --extra dev python scripts/benchmark_cache_modes.py \
  --cache-mode cached --scenario expressions --requests 1000 --routes 100
uv run --frozen --offline --extra dev python scripts/benchmark_cache_modes.py \
  --cache-mode uncached --scenario versioned-routes --requests 1000 --routes 100 --varying-paths
```

For each of the four scenario/mode combinations, run the corresponding command 13 times serially with `--jsonl fresh-condition.jsonl`; discard the first three rows and assert exactly ten remain. Record host load before each process as in `runs.json`. Run the same command separately with `--profile /tmp/condition.pstats` for profiling, excluding that run from timing. `fingerprint.json` captures Python, host, base source revision, and hashes of the measured source files. The committed JSON profiles retain ranked output; binary cProfile dumps were left outside the repository.

Before a future optimizer change, repeat under a controlled idle host with randomized/interleaved conditions and verify the same frozen goldens. This report deliberately makes no historical fixed-vs-varying workload comparison.
