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

Rollback uses `git revert` on the commit titled
`Reuse the selected versioned operation match`.
