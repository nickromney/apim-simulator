# Gateway performance

The local gateway benchmark with 100 versioned operation routes improved from
35 to 550 requests per second, a 15.7-fold increase, on 3 October 2026. Its p95
request latency fell from 33.9 to 2.1 milliseconds. Two isolated optimizations
reuse expression compilation and version selection while retaining the captured
expression, policy, trace, and routing outcomes.

## Workload and measurements

The benchmark runs the complete FastAPI gateway through HTTPX's ASGI transport,
with an in-memory backend that echoes the request body. The expression scenario
executes nine expressions per POST: header and query reads, variable writes,
a preserved body read, a conditional header, and an outbound header. The
passthrough scenario uses the same gateway and backend without custom policies.
The versioned-routing scenario has two versions of a catalog API, each with
50 GET operation templates including a path parameter and a required query
parameter. It selects the last operation in v2 by header. Each process warms
the gateway with 20 requests, then measures 100 routing requests or 1,000
expression requests.

Measurements use Python 3.14.8 on macOS 26.6.2 arm64. Hyperfine performs three
warmups and ten measured process runs. These measure simulator CPU work with
no backend network delay; they do not establish Azure or remote backend performance.

| Versioned-routing metric | Before | After |
| --- | --- | --- |
| p50 request latency | 27.19 ms | 1.76 ms |
| p95 request latency | 33.90 ms | 2.07 ms |
| p99 request latency | 40.15 ms | 2.23 ms |
| Throughput | 34.95 requests/s | 549.97 requests/s |
| Peak RSS | 100.98 MiB | 100.98 MiB |

These are medians of the ten measured runs' individual metrics. Hyperfine's
whole-process mean, which also includes imports, application startup, and
gateway warmups, fell from 4.275 ± 0.442 seconds to 0.868 ± 0.029 seconds.

The expression workload's median p50 fell from 1.566 to 1.258 ms, and median
throughput increased from 540 to 672 requests/s (24%). Its median p95 and p99
rose slightly in these unpaired runs, so no tail-latency improvement is claimed
for that workload. Raw measured runs and Hyperfine results are retained in
[gateway-performance-results.json](gateway-performance-results.json).

## Profile and opportunity

Before editing, cProfile attributed 2.167 of 7.593 seconds to expression
evaluation across 9,000 evaluations. Translation took 0.826 seconds and AST
validation took 0.990 seconds. Compilation appeared among the five largest
contributors by internal time. Existing policy XML parsing was already cached.

| Opportunity | Impact | Confidence | Effort | Score |
| --- | --- | --- | --- | --- |
| Reuse expression translation, validation, and compilation | 4 | 5 | 2 | 10.0 |
| Reuse version selection within a request and host group | 5 | 5 | 2 | 12.5 |

The fresh profile after the change contains no translation, AST validation, or
compilation calls during the measured requests: the warmups populate the cache.
Expression evaluation remains active, and fresh context construction becomes
the largest expression-related cost.

The second profile found that 100 versioned operation routes triggered 1,000
full route-table scans and 100,000 candidate checks for ten requests. Version
selection consumed 1.124 of 1.178 profiled seconds (95.4%). At 500 routes, it
consumed 99% of profiled time. The implementation now retains each version
selection within its request and host group, including a missing selection.
The fresh 100-route profile has one full scan and 100 candidate checks per
request. The remaining routing work is linear for operations sharing a version
set and API prefix; the original quadratic repetition is gone.

## Expression compilation behavior proof

The cache holds at most 1,024 entries. Its key contains the stripped expression
text and a frozen set of the local names available when validation runs.
This retains unknown-name rejection
even when identical text was previously compiled in a broader scope. The cache
stores only immutable code objects. Each evaluation constructs a fresh
environment and the same `TryGetValue` closure over its current local scope.

- Ordering preserved: compilation reuse does not reorder expression or policy execution.
- Tie-breaking unchanged: routing and policy precedence are untouched.
- Floating-point identical: the same translator, AST transformer, compiler,
  and arithmetic helpers run on cache misses; hits execute their resulting code.
- RNG seeds unchanged: GUID generation and clock reads execute at runtime.
- Golden outputs unchanged: request-dependent values, interpolation,
  multi-statement locals, errors, body preservation/consumption, and traced and
  untraced callouts match the captured baseline byte for byte.

The golden SHA-256 is
`b3bd11088c8fb1aa9eb10feaaa8290bc8fcecbc2570ccb164ab19799dde02e31`.
The test suite additionally checks fresh request values, local scope validation,
repeated GUID generation, consuming body reads, and eviction after 1,100 unique
expressions. Exceptions are not cached.

## Version selection behavior proof

The request-local key contains the version-set identifier and exact effective
API prefix. The source route contributes no other data to version selection.
Request method, path, query, scheme, config, and hosts stay fixed during the
scan. A separate cache for each host-candidate group preserves forwarded-host
fallback; a new cache on every request observes config changes immediately.

- Ordering preserved: every outer route retains its declaration index and
  host, protocol, online-status, and API-path eligibility checks.
- Tie-breaking unchanged: the inner scan still replaces a winner only for
  strictly greater precedence; the outer scan still prefers the earliest index.
- Floating-point: N/A.
- RNG seeds: N/A.
- Golden outputs unchanged: 63 cases cover header, query, and segment versions,
  Original, missing/unknown versions, literal/parameter/wildcard precedence,
  repeated query values, differing prefixes and casing, method restrictions,
  host fallback, protocols, offline APIs, and declaration ties.

The routing golden SHA-256 is
`6f19bf6a957a03f9576bbd8e835a7a883935f131f75b64597edf76d3d574655b`.
Tests also assert one version scan per API prefix, including negative lookups,
and check host-group separation, nested prefixes, and fresh config order.

## Reproduction

Run from the repository root:

```bash
hyperfine --warmup 3 --runs 10 \
  'uv run --extra dev python scripts/benchmark_gateway.py --requests 1000'
hyperfine --warmup 3 --runs 10 \
  'uv run --extra dev python scripts/benchmark_gateway.py \
    --scenario versioned-routes --requests 100'
uv run --extra dev python scripts/benchmark_gateway.py \
  --scenario passthrough --requests 1000
uv run --extra dev python scripts/benchmark_gateway.py \
  --requests 1000 --profile /tmp/gateway.prof
uv run --extra dev python -c \
  'import pstats; pstats.Stats("/tmp/gateway.prof").sort_stats("cumulative").print_stats(30)'
uv run --extra dev python -m scripts.golden_policy_outputs \
  --output tests/fixtures/performance/policy_outputs.json
uv run --extra dev python -m scripts.golden_route_outputs \
  --output tests/fixtures/performance/route_outputs.json
sha256sum -c tests/fixtures/performance/golden_checksums.txt
uv run --extra dev pytest -q tests/test_apim_expr_unit.py \
  tests/test_gateway_performance_golden.py tests/test_versioned_routing_performance.py
```

Use `--jsonl /tmp/gateway-runs.jsonl` to retain each process run's latency,
throughput, and peak RSS. Profiles add overhead and must be kept separate from
latency measurements. Compare source revisions using the same benchmark harness.
The baseline source revision is `6ba8e81abf8e105c37a0907fdc5f8503089497f1`.

## Verification

The full Python suite passed: 1,167 passed, one Keycloak integration test
skipped, and branch-aware coverage measured 86.86% against the 82% gate.
Both golden checksum checks passed. Repository-wide Ruff lint and formatting,
the complexity ratchet, Markdown lint for this report, and diff whitespace
checks passed. Independent adversarial reviews found no introduced bugs in
either optimization. Docker and frontend checks were not run because neither
container wiring nor frontend code changed.

## Rollback

The two levers can be reverted independently: remove compilation caching from
`app/apim_expr.py` (and its capacity test/import), or remove the request-local
version cache from `app/proxy.py` (and its scan-count assertion). Keep the
behavior oracles and benchmark harness, then rerun golden checksums and the
application suite. For committed changes, use `git revert <optimization-commit>`
for the corresponding lever.
