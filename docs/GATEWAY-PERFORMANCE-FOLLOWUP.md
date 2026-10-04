# Further gateway optimization

This report records up to three additional optimization iterations on
4 October 2026, following the changes in [the initial report](GATEWAY-PERFORMANCE.md).
The starting source revision is `36cacd6`. Each lever is profiled, checked
against unchanged behavior oracles, measured, and committed separately.

## Iteration 1 Reuse the final versioned operation match

The 100-route profile made 150,000 operation matches for 1,000 requests:
50 candidate matches and 100 repeated matches of the selected operation per
request. `RouteConfig.match` consumed 4.151 of 14.742 profiled seconds.
Retaining the final match in the existing request-local version cache removes
the 100 repeated matches without removing any outer route eligibility checks.

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
Rollback uses `git revert` on the commit titled
`Cache immutable routing path segments`.
