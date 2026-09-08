# Mutation Testing

Coverage tells you a line was executed. It does not tell you the assertions
would notice if that line stopped working. Mutation testing answers the second
question: change one operator in the source, run the suite, and see whether
anything fails.

A mutant that makes the suite fail is *killed*. A mutant the suite still passes
*survived*, and points at an assertion gap on that exact line.

## Running it

The runner is [mutmut](https://github.com/boxed/mutmut), configured under
`[tool.mutmut]` in `pyproject.toml`. Name the module:

```bash
make mutation MODULE=app.backend_pool
```

Then read the results:

```bash
make mutation-results
make mutation-show MUTANT=app.backend_pool.x_apply_backend_credentials__mutmut_3
```

`make mutation-clean` removes the `mutants/` working copy and the cache.

To score the whole curated module list in one bounded pass:

```bash
make mutation-baseline   # reports scores, exits 0
make mutation-gate       # fails if any mutant survived
```

### Bounding a run

`scripts/mutation-test.sh` gives each module its own budget through
`run_with_timeout` in `scripts/lib/timeout.sh`, ported from the sibling
`platform` repository. GNU coreutils `timeout` is not on stock macOS, and the
first batch run here proved why that matters: `timeout 3000 mutmut run ...`
printed "command not found" for every module and the loop still exited 0. A
batch that scored nothing looked exactly like a batch that passed.

The fallback returns 124 on expiry, matching coreutils, so callers branch on the
status without caring which of the three implementations ran.

### Naming the mutant

mutmut matches mutant names with `fnmatch`, and a mutant is named
`<module>.x_<function>__mutmut_<n>`. A bare module name matches nothing and the
run aborts with "Filtered for specific mutants, but nothing matches". The Make
target and the runner script both append the glob so that trap is not left out
in the open.

### Naming the target matters more than it looks

Run one module at a time. A run that drags in suites which cannot kill a single
one of its mutants costs minutes and buys nothing, and the whole-package run is
dominated by `app/policy.py` at 3,200 lines.

## Choosing the oracle

mutmut executes the suite inside a `mutants/` copy of the tree, which exposed two
things about this repo's tests worth fixing on their own merits.

- **The suite reads fixtures by repo-root-relative path.** `contracts/`,
  `scripts/` and `examples/` are listed in `also_copy` because without them
  collection fails before a single mutant is scored.
- **Some tests assert on repository artifacts, not on application behaviour.**
  `test_container_hardening.py`, `test_backstage_integration.py`,
  `test_release_artifacts.py` and `test_dependency_footprint.py` check the
  Dockerfile, the Backstage catalog, the release artifact and the packaging
  metadata. They are worth having and they can kill no mutant in `app/`, so they
  now carry a `repo` marker and are deselected for mutation runs.

That distinction is useful beyond mutation testing: `pytest -m "not repo"` is the
application suite, and it is what a change to `app/` actually has to satisfy.

Integration tests are deselected for the same reason. They need Keycloak on the
network, they skip without it, and a skipped test kills nothing while still
costing collection time on every mutant.

## Baseline

Score = killed / (killed + survived). "Unreached" counts mutants no test in the
suite touches at all, which coverage does not report and which is usually the
more alarming number.

| Module | Killed | Survived | Unreached | Score |
| --- | ---: | ---: | ---: | ---: |
| `app/backend_pool.py` | 218 | 28 | 0 | 88% |
| `app/effective_policy.py` | 57 | 11 | 0 | 83% |
| `app/named_values.py` | -- | -- | -- | not scored: 54 of 120 segfault |
| `app/urls.py` | -- | -- | -- | not scored: 3 of 21 segfault |
| `app/apim_expr.py` | -- | -- | -- | not scored: 3 of 571 timed out |

Only the first two are measurements. The rest are the runner declining to
report, and they are listed here so the gap is visible rather than absent.

`app/apim_expr.py` should score on the next run: its three unreconciled mutants
are timeouts, and the runner now counts a timeout as killed. The batch above ran
with the earlier logic.

`app/backend_pool.py` is the one that has been worked, and it is the only module
here with a before and after that mean anything. Its first run reported 72% with
**106 mutants no test reached at all**: the module read as 88% line-covered, but
every mutant in `_apply_auth_type`, `_apply_authorization_header` and
`_apply_credential_pairs` was untouched. Those functions were exercised only
incidentally, by gateway requests that happened to pass through them.
`tests/test_backend_pool_unit.py` drives them directly, and the module now
scores 88% with nothing unreached and 98% line coverage.

That 72% figure came from the same broken scorer as everything else, so treat it
as indicative of the direction rather than as a measurement. The 106 unreached
mutants were counted correctly, and they were the point.

## What it found first: a logging handler bound to a dead stream

Before it scored a single mutant, the runner refused to score `app/urls.py` at
all. mutmut checks that the suite passes on the unmutated source first, and that
check failed inside the `mutants/` copy:

```text
tests/test_cli.py::test_missing_tenant_key_is_a_403_error_with_exit_code_1
ValueError: I/O operation on closed file
```

The cause is real and not a mutmut artifact. `app/telemetry.py` attached its
stderr handler with `logging.StreamHandler()`, which binds whichever stream is
current **when the handler is constructed**. The gateway builds that handler once
and keeps it for the life of the process. Under the normal suite it was first
built at import time, when `sys.stderr` was the real one, and nothing went wrong.
Under mutmut the import happened later, inside a test, while pytest's capture had
replaced `sys.stderr` — and once that test tore down and closed its buffer, every
later log line raised.

The same trap applies well beyond tests: anything that reopens or replaces the
process's streams after startup leaves the gateway logging into a stream nobody
reads. `_CurrentStderrHandler` now resolves `sys.stderr` at emit time, and two
regression tests in `tests/test_gateway.py` pin it.

Fixing it also exposed an over-tight assertion. Two CLI tests asserted that
stderr contained *only* the CLI's error line, which held only because the
gateway's own access log was being written somewhere else. They now assert the
CLI's line is present.

This is the argument for mutation testing in one defect: 319 passing tests and
82% coverage said nothing about it, and the thing that found it was a runner
refusing to report a score it could not stand behind.

## What survivors usually mean

Carried over from the same exercise on the sibling `platform` repository, where
bash suites sat between 15% and 63% while fully green:

- **Return-code flips dominate.** Early-return and error paths get executed by
  the suite but their status is never asserted.
- **Logical-operator mutants survive in guard clauses** whose bail-out branch no
  test ever drives.
- **Boundary mutants need an exact-boundary input.** A `>=` that should be `>` is
  only caught by a case that lands exactly on the boundary; anything else passes
  under both.

## Reconciling the score

The runner refuses to print a percentage it cannot reconcile. mutmut reports a
status per mutant, and only four of them support a score:

| Status | Counts as |
| --- | --- |
| `killed` | killed |
| `timeout` | killed -- a mutant that makes the suite hang is one it noticed |
| `survived` | survived |
| `no tests` | reported separately as unreached |
| `segfault`, `suspicious`, `not checked` | unreconciled: no evidence either way |

If killed plus survived plus unreached does not equal the number of mutants, the
module is reported as `NOT SCORED` with the breakdown and its log, and the run
exits non-zero.

This is not hypothetical caution. The first version of this runner computed
`killed / (killed + survived)` and ignored every other status, and it reported
`app/named_values.py` at 95% while 54 of that module's 120 mutants had come back
`segfault` and been dropped on the floor. Every score in the first draft of this
document was computed that way and none of them were trustworthy.

## Open: mutmut's forked worker crashes on some modules

mutmut runs each mutant by `os.fork()` with no `exec`, then running pytest
in-process in the child. On this macOS host a large and perfectly reproducible
share of mutants in some modules come back `segfault`.

What is known:

- It is deterministic per module, but not uniform across them.
  `app/named_values.py` returns exactly 54 segfaults out of 120 on every run.
  `app/urls.py` returned 0 on one run and 3 on the next, so the rate varies
  between runs even where the module is small.
- It is not a timeout. Raising `timeout_constant` and `timeout_multiplier`
  changed nothing.
- It is not concurrency. `--max-children 1` gives the same 54.
- It is not the mutants. Running a segfaulting mutant directly, with
  `MUTANT_UNDER_TEST` set and the same pytest arguments, passes all 281 tests.
- Most are not real crashes. A run producing 54 segfaults leaves about 4 macOS
  crash reports, so the rest are `SIGKILL`, which mutmut also labels `segfault`.
- It is module-specific in degree. `app/effective_policy.py` and
  `app/backend_pool.py` reconcile fully; `app/named_values.py` loses 45% of its
  mutants.

Until this is understood, treat any module the runner declines to score as
unmeasured rather than as passing.

## Accepted equivalents

Some mutants change the source without changing any behaviour an honest test
could observe. Record those next to the test that would otherwise be expected to
kill them. Never drop one silently from the score: a survivor you cannot explain
is a gap, and a survivor you can explain is documentation.

## Holding the line

A new function in `app/` arrives with its own tests, and a re-mutation is what
proves those tests are worth having. Sixteen passing tests is not the same as
sixteen tests that would notice.
