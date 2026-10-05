# Gateway optimization learnings for agents

Use these notes when changing expression compilation, route matching, or their
benchmarks. They capture semantic dependencies and verification traps found in
the October 2026 optimization work. Use your existing optimization skill for
the general workflow. Measurements and historical proofs live in
[the initial report](GATEWAY-PERFORMANCE.md) and
[the follow-up report](GATEWAY-PERFORMANCE-FOLLOWUP.md).

## Expression validation is part of the cache key

In `app/apim_expr.py`, identical expression text can be valid inside a
multi-statement scope and invalid outside it. `_validate_ast` depends on the
available local **names**, so `_compile_expression` keys compilation by
`(stripped_expression, frozenset(local_names))`. A text-only key can reuse code
that should have failed unknown-name validation.

Cache the compiled code, then construct the evaluation environment and
`TryGetValue` closure for every evaluation. Those closures reference the current
local dictionary; GUIDs, clock reads, and consuming body reads remain runtime
effects. Interpolation continues through its existing evaluation path.

Transfer check: execute the same text with different local-name sets, then with
the same names and different values. Verify both rejection behavior and fresh
results. See `tests/test_apim_expr_unit.py` for scope, GUID, body, and eviction
cases. If validation acquires another dependency, extend the key before reusing
compilation across that dependency.

## Routing reuse belongs inside the eligibility boundary

The outer route loop formerly repeated full version-selection scans. Within
one synchronous request and host-candidate group, `_resolve_versioned_route`
depends on the source route's version-set ID and effective API prefix. The
method, incoming path, query, scheme, configuration, and candidate hosts are
fixed for that scope. Its key is therefore
`(route.api_version_set, route.api_path_prefix or route.path_prefix)`.

Keep these less-obvious boundaries when reusing a winner:

- Check each outer route's host, protocol, online status, and API prefix before
  consulting the cache. A cached winner does not make another source route
  eligible.
- Allocate the cache inside each host-group loop. A forwarded-host miss must
  allow a fresh selection for the direct host.
- Use membership checks to distinguish an absent entry from a cached `None`.
  Unknown or missing versions otherwise keep triggering full scans.
- Retain exact prefix spelling. Segment versioning reconstructs the upstream
  path with that spelling; case-folded matching does not imply interchangeable
  output paths.
- Keep outer declaration indexes and inner strict-greater precedence checks.
  Reusing a winner must preserve both layers of tie-breaking.

Retain the selected operation's final `RouteMatch` with its `ResolvedRoute`.
Caching only the selected route left a winner rematch for every outer route.
These objects contain mutable configuration and parameter dictionaries, so
their reuse stays inside the request/host group. The request pipeline copies
matched parameters before policy execution; preserve that ownership boundary.

Transfer check: cover header/query/segment versions, misses, host fallback,
prefix casing, exact ties, and a changed config on the next request. Count both
selection scans and final operation matches. A reduced scan count alone missed
the second source of repeated work. See
`tests/test_versioned_routing_performance.py`.

## Immutable values let live configuration stay live

`RouteConfig` fields can change between calls. Cache pure helpers using their
current string inputs, leaving effective-prefix selection outside the cache:

- `_path_segments(path)` retains tuples of strings. All callers were audited
  for read-only operations before changing its private return type. A cached
  list would let one caller modify later matches.
- `_matches_api_path(prefix, path)` retains a boolean. Its caller only needs
  eligibility, so caching a whole `RouteMatch` would unnecessarily share a
  mutable parameters dictionary.

Preserve the supplied strings and existing normalization: interior empty
segments, percent escapes, dot segments, backslashes, and captured parameter
casing still matter. Literal case folding remains in the matching helpers.
Each match continues to allocate fresh parameter dictionaries.

The process-wide caches each cap entries at 1,024; that caps entry count, not
bytes. Transfer checks must exceed capacity, revisit evicted inputs, mutate
route fields, and mutate one returned match's parameters before matching again.
See `tests/test_routing_path_cache.py` and
`tests/test_routing_prefix_cache.py`.

## A differential oracle can share the bug under test

Loading an old `proxy.py` beside the current `RouteConfig` can make both routers
call the same changed matching helpers. Agreement then establishes selection
equivalence while missing a helper regression.

Freeze the baseline's configuration helpers too, or run baseline and candidate
apps in separate processes. Compare operation matches, prefix matches, path
segment contents, and the current `matches_api_path` wrapper against the old
prefix helper independently. Compare selected route, rewritten path, matched
parameters, query names, and exception type/message, including misses.

For expression and policy oracles, freeze nondeterministic clock/GUID outputs
while recording body state after evaluation and repeated consuming reads.
Returned values alone cannot detect a changed body-consumption side effect.
The committed golden generators and fixtures provide reusable cases; the
large randomized differential probe reported in the follow-up was a temporary
validation artifact, not a checked-in test command.

## Archived Python code needs explicit import selection

In this editable installation, setting `PYTHONPATH` alone did not reliably
select the archived app when executing the current benchmark script. Insert
the archived root at `sys.path[0]` before importing `app`, and verify the loaded
`app.__file__` belongs to that root. Otherwise a baseline run can silently
measure the candidate twice.

`scripts/benchmark_varying_gateway.py --app-root <archived-root>` implements
the early path insertion and runs the same current harness for both sources.
Keep the harness and backend behavior identical; change only the selected app
source. The follow-up report has archive and invocation commands.

## Benchmark output has two distinct warmup layers

The core harness excludes 20 internal gateway warmups from request samples.
Hyperfine's three process warmups still execute the command and append JSONL
rows. Use a fresh JSONL file per condition, discard its first three rows, and
assert ten measured rows remain. Reusing an append file can silently mix runs.

Hyperfine wall time includes imports, startup, and gateway warmups. The JSON
request metrics isolate the measured gateway loop. These two timers support
different claims, especially after routing gets fast enough that startup is a
large fraction of process time.

The varying-path wrapper changes IDs during warmups and measurement, reducing
cross-request reuse while retaining within-request repetition. It also asserts
that the intended URL substitution actually ran. A repeated URL can otherwise
hide the cost of new keys and cache churn.

Compare workload variants separately. Multiplying the original fixed-URL
speedup by the varying-ID follow-up gain mixes experiments. The approximately
27-fold combined routing figure is a historical fixed-URL comparison across
different days and request batch sizes; expression throughput is a separate
workload. Keep those qualifications when summarizing the reports.

## Forwarded-protocol smoke tests need the intended trust settings

`network_security.allow_simulated_forwarded_headers` defaults to false.
A localhost client sending `X-Forwarded-Proto: https` does not by itself enable
simulated forwarding. The initial real-HTTP fixture omitted that setting and
correctly got a 404 for an HTTPS-only API.

Set the explicit local-simulation option when that is the scenario under test,
or configure the intended trusted proxy. Keep default rejection as a separate
case. Changing protocol checks to satisfy an incorrectly configured fixture
would weaken the gateway's trust boundary. See the protocol test in
`tests/test_routing.py` and forwarding logic in `app/network_security.py`.
