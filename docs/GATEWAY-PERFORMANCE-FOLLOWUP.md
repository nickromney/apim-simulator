# Further gateway optimization

This report records up to three additional optimization iterations on
4 October 2026, following the changes in [the initial report](GATEWAY-PERFORMANCE.md).
The starting source revision is `36cacd6`. Each lever is profiled, checked
against unchanged behavior oracles, measured, and committed separately.

## Iteration 1 Reuse the final versioned operation match

The 100-route profile made 150,000 operation matches for 1,000 requests:
50 candidate matches and 100 repeated matches of the selected operation per
request. `RouteConfig.match` consumed 4.151 of 14.742 profiled seconds.
Retaining the final match in the existing request-local version cache reduces
those 100 repeated matches to one, preserving outer route eligibility checks.

| Opportunity | Impact | Confidence | Effort | Score |
| --- | --- | --- | --- | --- |
| Retain the final operation match with the selected route | 3 | 5 | 1 | 15 |

- Ordering preserved: outer declaration indexes and eligibility checks remain.
- Tie-breaking unchanged: selection still uses the same precedence and index.
- Floating-point: N/A.
- RNG seeds: N/A.
- Golden outputs: both existing SHA-256 checks pass without fixture changes.

The final match is a pure function of the already-selected route and fixed
request method, path, and query. The cache still lives only within one request
and host-candidate group; parameters are not reused between requests.

The first comparison increased median throughput from 550 to 814 requests/s.
Because its baseline was variable, a second comparison used a frozen copy of
the preceding source. This confirms a 38.7% throughput increase and 27.8% lower
p95 latency. The table uses the more conservative confirmation results.

| Metric | Before | After |
| --- | ---: | ---: |
| Median p50 (ms) | 1.671 | 1.203 |
| Median p95 (ms) | 2.061 | 1.487 |
| Median p99 (ms) | 3.027 | 1.813 |
| Median requests/s | 563.8 | 782.2 |
| Median peak RSS (MiB) | 101.33 | 101.59 |

The operation-match count fell from 150,000 to 51,000 per 1,000 requests.
All 24 focused tests pass. A seeded differential probe compared 2,000 routing
cases with `36cacd6` and found zero mismatches; an independent review found
no correctness issues. Raw measurements, including both comparisons, are in
[the results file](gateway-performance-followup-results.json).

Rollback is `git revert 7171902`.

## Iteration 2 Cache immutable path segments

The fresh profile after iteration 1 puts `_path_segments` third by self time:
453,000 calls consume 0.361 of 4.236 profiled seconds cumulatively. API prefixes,
operation paths, and the incoming path are repeatedly normalized and split.
The latest ten-run iteration 1 confirmation is the baseline for this iteration.

| Opportunity | Impact | Confidence | Effort | Score |
| --- | --- | --- | --- | --- |
| Bound a pure path-segment cache to 1,024 immutable entries | 2 | 5 | 1 | 10 |

- Ordering preserved: cached tuples contain the original segment order.
- Tie-breaking unchanged: matching and precedence rules remain unchanged.
- Floating-point: N/A.
- RNG seeds: N/A.
- Golden outputs: both existing SHA-256 checks pass without fixture changes.

Only immutable tuples are retained. Callers concatenate, slice, and read
segments; each match still allocates fresh parameter dictionaries. Raw path
strings are cache keys, so edits to route fields select their new string values.
Normalization, case folding, percent escapes, and interior empty segments keep
their existing semantics. A 1,024-entry LRU bounds retained paths.

The first comparison showed a 2.6% median throughput increase. A fresh
comparison against the frozen iteration 1 source confirms a 3.7% increase;
the more modest gain warranted a second measurement before keeping it.

| Metric | Before | After |
| --- | ---: | ---: |
| Median p50 (ms) | 1.206 | 1.156 |
| Median p95 (ms) | 1.415 | 1.382 |
| Median p99 (ms) | 1.598 | 1.556 |
| Median requests/s | 798.7 | 828.1 |
| Median peak RSS (MiB) | 101.27 | 101.63 |

The fresh profile has 16.984 million calls, down from 19.702 million. All 31
focused tests pass, including mutable parameters, config edits, unusual path
forms, and eviction. The enhanced seeded differential probe found zero
mismatches across 2,000 routing cases, 67,504 generated helper comparisons, and
608 fixed path-shape comparisons. Independent review found no issues.
Rollback is `git revert 386b6bd`.

## Iteration 3 Cache API-prefix eligibility

The fresh iteration 2 profile puts `_match_path_prefix` second by self time:
150,000 calls consume 0.325 of 4.132 profiled seconds cumulatively. All are API
eligibility checks, which discard the match object and only need a boolean.
The latest ten-run iteration 2 confirmation is the baseline for this iteration.

| Opportunity | Impact | Confidence | Effort | Score |
| --- | --- | --- | --- | --- |
| Bound pure API-prefix eligibility to 1,024 boolean entries | 2 | 5 | 1 | 10 |

- Ordering preserved: route iteration and outer eligibility checks remain.
- Tie-breaking unchanged: matching and precedence rules remain unchanged.
- Floating-point: N/A.
- RNG seeds: N/A.
- Golden outputs: both existing SHA-256 checks pass without fixture changes.

The effective prefix is read from the current route before every lookup. Keys
contain its exact string and the incoming path. Positive and negative results
are pure booleans; cached values contain no match parameters, route, or request
objects. Configuration edits and different paths naturally use different keys.
`_match_path_prefix` remains unchanged for callers that need a fresh match.
Rollback uses `git revert` on the commit titled `Cache API-prefix eligibility`.

| Metric | Before | After |
| --- | ---: | ---: |
| Median p50 (ms) | 1.141 | 1.015 |
| Median p95 (ms) | 1.366 | 1.204 |
| Median p99 (ms) | 1.524 | 1.305 |
| Median requests/s | 838.5 | 947.0 |
| Median peak RSS (MiB) | 101.34 | 101.62 |

Throughput increased by 12.9%, and p95 fell by 11.8%. The fresh profile removes
all 150,000 prefix-match calls after the repeated URL is warmed; varying URLs
still require one computation per new prefix/path pair. All 43 focused tests
pass. The enhanced probe found zero mismatches across 2,000 routing cases,
101,256 generated helper comparisons, and 969 path-shape comparisons.
Independent reviews of this lever and the combined changes found no issues.

## Combined check with varying request IDs

A second workload gives every request a new parameter ID, including warmups,
using [the same benchmark](../scripts/benchmark_varying_gateway.py). Both the
frozen `36cacd6` app and final app use the identical wrapper and core harness.
This reduces reuse across requests while preserving repeated work within each
request. Ten measured runs follow three discarded process warmups for every
comparison; each process also performs 20 gateway warmups. No tests or profiles
run concurrently with timing.

| Metric | Starting source | After three iterations |
| --- | ---: | ---: |
| Median p50 (ms) | 1.685 | 1.037 |
| Median p95 (ms) | 1.912 | 1.247 |
| Median p99 (ms) | 2.153 | 1.395 |
| Median requests/s | 578.0 | 921.0 |
| Median peak RSS (MiB) | 101.61 | 102.13 |

This confirms 59.3% more throughput and 34.7% lower p95, with 0.52 MiB higher
peak RSS. Both new caches cap entry counts at 1,024. These measurements describe
local gateway CPU work with an in-memory backend and 100 versioned routes;
network latency and other route distributions can change the benefit.
Raw per-run measurements are retained in
[the results file](gateway-performance-followup-results.json).

To reproduce, freeze the baseline app and run the same harness against both:

```bash
mkdir -p /private/tmp/apim-baseline
git archive 36cacd6 app | tar -x -C /private/tmp/apim-baseline
UV_CACHE_DIR=/private/tmp/apim-uv-cache hyperfine --warmup 3 --runs 10 \
  'uv run --extra dev python scripts/benchmark_varying_gateway.py \
    --app-root /private/tmp/apim-baseline --scenario versioned-routes' \
  'uv run --extra dev python scripts/benchmark_varying_gateway.py \
    --scenario versioned-routes'
```

The final profile places repeated request-scheme reads among the remaining
hotspots. This pass stops at the requested three additional iterations.
