# 2026-09-07 Quality pass digest

Mutation testing, cyclomatic complexity, and container startup, carried over
from the same three-part exercise in the sibling `platform` repository.

The headline is not the coverage number. Coverage moved 81.7% to 82.2%, which
is nothing. What moved is whether the tests would notice a change: one module
went from 72% to 87% mutation score with its 106 unreachable mutants reduced to
zero, and the worst function in the codebase went from 147 paths to ten named
routers.

One caution up front: the first version of the mutation runner computed
`killed / (killed + survived)` and silently discarded every other status, so the
scores in the first draft of this digest were not measurements. The runner now
reconciles, and only two of the five curated modules currently produce a score
at all. See [mutation-testing.md](./mutation-testing.md).

## Result

| | before | after |
| --- | ---: | ---: |
| tests | 261 | 322 |
| coverage | 81.7% | 82.5% |
| coverage floor | 75% | 82% |
| worst function complexity | 147 | 33 (route registration) |
| worst non-registration complexity | 99 | 8 |
| functions above complexity 8 | 33 | 0 |
| complexity gate | none | enforced at 8 |
| mutation testing | none | runner in place; 2 of 5 modules scored |

`make lint` and the full suite are green throughout.

## What the complexity numbers were actually made of

Three of the worst four functions were not branchy at all.

- **`build_management_router` scored 147 for declaring 95 routes.** Ruff counts a
  nested function definition as a branch of its parent, so a router builder's
  score is its route count. Split into ten resource-scoped builders, which still
  score their own route counts and now carry a scoped `noqa` saying why.
- **`create_app` scored 42 for defining closures**, not for deciding anything.
  Its collaborators are module-level now, and directly testable as a result: the
  config watcher was previously reachable only by walking
  `create_app.__code__.co_freevars`, which is what the old test did.
- **`import_from_tofu_show_json` scored 99 for a 24-arm `if res.type == ...`
  chain** across two passes. Two dispatch tables and a shared accumulator turned
  each resource type into a small named function.

`execute_gateway_request` was the one with real branching: 44, now 14, split
into named stages (admission, policy stack, backend choice, cache, send, response).

## Behaviour locks written before the surgery

- **A golden snapshot of the OpenTofu importer.** A new fixture exercises all 24
  resource types the importer handles, and the golden pins every field of the
  resulting tenant document plus every diagnostic. A companion test fails if a
  handled resource type is missing from the fixture, so the golden cannot
  silently stop guarding a type.
- **Route ordering.** The existing inventory test pinned method and path but not
  order. The gateway catch-all matches every path, so a catch-all that drifts
  ahead of the management surface swallows it whole while every route still
  reports as present. That is now asserted.

## Mutation testing found what coverage could not

`app/backend_pool.py` read as 88% line-covered. Its first mutation run scored
72%, with **106 mutants that no test reached at all** — every mutant in the three
credential helpers. Those functions were exercised only incidentally, by gateway
requests that happened to pass through them.

`tests/test_backend_pool_unit.py` drives them directly. The module is now 98%
line-covered, scores 87%, and has nothing unreached.

Baseline and method: [mutation-testing.md](./mutation-testing.md).

## The defect mutation testing found before it scored anything

The runner refused to score `app/urls.py`, because mutmut checks that the suite
passes on unmutated source first and that check failed with
`ValueError: I/O operation on closed file`.

`app/telemetry.py` attached its stderr handler with `logging.StreamHandler()`,
which binds whichever stream is current when the handler is built. The gateway
builds it once and keeps it forever. Under the normal suite it was first built at
import time and bound the real stderr; under mutmut it was built inside a test,
bound pytest's capture buffer, and every log line after that test's teardown
raised. Anything that reopens the process's streams after startup hits the same
thing.

The handler now resolves `sys.stderr` at emit time. Fixing it also exposed two
CLI tests asserting that stderr contained *only* the CLI's error line, which held
only because the gateway's access log was going somewhere else entirely.

319 passing tests and 82% coverage said nothing about this. What found it was a
runner that refuses to print a score it cannot reconcile.

## Two traps worth naming

- **`timeout` is not on stock macOS.** The first batch run used
  `timeout 3000 mutmut run ...`, which printed "command not found" for every
  module and still left the loop exiting 0. A batch that scored nothing looked
  exactly like a batch that passed. `scripts/lib/timeout.sh` is ported from
  `platform`, which had already been bitten by this, and returns 124 on expiry
  to match coreutils.
- **mutmut forks a pytest per mutant and defaults to every core.** Unbounded, it
  crashed four Python processes on the development host. A killed child scores
  as nothing rather than as a failure, so the cap protects the score as much as
  the machine. The runner now caps concurrency at two and keeps every log, and
  reports `NOT SCORED` rather than a percentage it cannot reconcile.

## Container startup

Every compose service runs with `read_only: true`, so the container can never
write a `.pyc`: without precompilation the interpreter recompiles the whole
dependency tree in memory on every start. The image now compiles bytecode for
both the dependency tree (`uv sync --compile-bytecode`) and `app/`
(`compileall`), and declares a healthcheck against `/apim/startup` rather than
`/apim/health`, because the latter answers before the app can serve traffic.

Measured by building both Dockerfiles against the same source tree and
importing `app.main` inside a container with a read-only root filesystem. Runs
were interleaved, six of each, because the absolute numbers move with whatever
else the host is doing:

| | before | after |
| --- | ---: | ---: |
| image size | 198 MB | 217 MB |
| cold import, median | 1369 ms | 965 ms |
| cold import, range | 1282-1848 ms | 846-1101 ms |

So roughly 400 ms off every container start, about 30%, for 19 MB of image. The
image holds 1,045 precompiled dependency modules and 22 of its own, and the
healthcheck reaches `healthy` on a read-only container.

Treat the absolute figures as indicative and the ratio as the result. An earlier
non-interleaved measurement on a quiet host read 991 ms against 609 ms, the same
38% on much lower absolute numbers.

Building it also caught a defect in the change itself: the hardened runtime
image ships no `/bin/sh`, so the shell-form `RUN` for `compileall` failed with
`stat /bin/sh: no such file or directory`. It is exec form now, and a test
asserts that, because nothing else would catch it before a build.

## Verified against the running stacks

Unit tests do not prove a gateway serves traffic. Every stack was brought up and
smoke-tested against the refactored code:

| Check | Result |
| --- | --- |
| `smoke-hello` anonymous / subscription / oidc-jwt / oidc-subscription | pass |
| `smoke-oidc` (role-based authz: user 200, user-on-admin 403, admin 200) | pass |
| `smoke-todo`, `test-todo-e2e` (3 Playwright browser tests) | pass |
| `verify-todo-otel` (metrics, Loki logs, Tempo traces, route tags) | pass |
| `smoke-mcp`, `smoke-edge`, `smoke-private` | pass |
| `smoke-ai` (token limits, pool round-robin, per-subscription counters, SSE) | pass |
| `smoke-shared`, `smoke-aws` | pass |
| `make local-ci` | exit 0 |

`smoke-ai` is the one that matters most: it exercises the token-limit policies,
the backend pool failover and the SSE streaming passthrough in a single run, and
those are the three areas this pass rewrote most heavily.

## Three defects the end-to-end run found

None were regressions from this work; all three predate it and all three are
fixed here.

1. **The Playwright suite could not pass at all.** `playwright.config.ts` set no
   `ignoreHTTPSErrors`, and the observability spec opens Grafana over https with
   an mkcert certificate. `mkcert -install` satisfies curl and the system trust
   store, but Playwright's bundled Chromium carries its own. The spec failed
   before any page loaded, reporting only `chrome-error://chromewebdata/`.
2. **The same spec then hit a strict-mode violation.** Grafana renders the
   dashboard title twice, once as a breadcrumb and once as a heading, so
   `getByText` matched two elements. It matches the heading now.
3. **`make verify-otel` could not run without the MCP SDK.** `verify_otel.py`
   imported one TLS helper from `smoke_mcp.py`, which imports `mcp` at module
   scope, so an OTEL check died with `ModuleNotFoundError: No module named
   'mcp'`. The helper now lives in `scripts/tls_verify.py` and four scripts
   import it from there.

## Also changed

The Python suite now separates tests of the application from tests of the
repository's shipped artifacts. `test_container_hardening`,
`test_backstage_integration`, `test_release_artifacts` and
`test_dependency_footprint` assert on the Dockerfile, the Backstage catalog, the
release artifact and the packaging metadata; they carry a `repo` marker and are
deselected for mutation runs, because they can kill no mutant in `app/`.

`pytest -m "not repo"` is the application suite, and it is what a change to
`app/` actually has to satisfy.

## What is left

- **Nothing remains above complexity 8.** The gate enforces it, and
  `make complexity` covers `app`, `scripts`, `tests` and `examples`.
- **Three of the five curated modules do not produce a score**, because mutmut's
  forked worker returns a large share of their mutants as `segfault`. That is
  the first thing to fix: no other mutation work is worth doing until a run can
  be trusted.
- **`app/policy.py` has no mutation score.** At 3,200 lines a run is long enough
  that nobody does it twice; splitting it is a prerequisite, not a follow-up.
- **`app/urls.py` became scoreable** once the logging defect below was fixed,
  which is what proved the fix. It has not scored cleanly since: a later run
  returned three segfaults out of its 21 mutants.
- **The AI, OIDC, MCP and edge compose stacks are unexercised.** Only the base
  image was built and run; the smoke targets need their stacks up.
