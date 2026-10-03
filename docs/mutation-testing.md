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

Add `--reconcile` to either script invocation to re-run, out of process, any
mutant the forked worker could not report on.

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
  `test_container_hardening.py`, `test_catalog_metadata.py`,
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
| `app/effective_policy.py` | 67 | 1 | 0 | 98% |
| `app/backend_pool.py` | 242 | 4 | 0 | 98% |
| `app/apim_expr.py` | 536 | 12 | 0 | 97% |
| `app/urls.py` | 19 | 2 | 0 | 90% |
| `app/named_values.py` | 118 | 2 | 0 | 98% |

The last two are reconciled scores: mutmut's forked worker crashed on those
mutants, and `--reconcile` re-ran them one at a time in a fresh process. See
"Reconciling out of process" below.

`app/named_values.py` scored 52% the first time it could be scored at all, and
every one of the 54 mutants the forked worker had been crashing on turned out to
be a survivor. That is what the crash was hiding. `tests/test_named_values_unit.py`
took it to 98%. Masking was the part worth reaching first: a survivor in
`mask_secret_data` means nothing in the suite would notice a secret being written
out in the clear.

Two of its survivors are worth repeating, because both were tests that passed
while proving nothing. Three mutants blanked the `name` and `env_var_name` of the
resolved value, and survived because the resolver builds its result on three
separate paths while the assertions covered only one. A fourth changed the
characters trimmed from the normalised environment variable name, and the test
written to kill it used a lower-case letter -- but the trim runs before the
upper-casing, so that letter could never have been affected either way.

`app/apim_expr.py` went from 64% to 97% in the same pass. Everything the
gateway knew about the expression evaluator it learned through policy documents
that happened to use one accessor or another, so the translation table was
covered where some example needed it and nowhere else.
`tests/test_apim_expr_unit.py` drives the module directly: one case per
translation rule, per validation refusal, and per branch of the request
normaliser. All twelve remaining survivors are recorded equivalents.

`app/effective_policy.py` went from 83% to 98% in this pass. Its eleven
survivors were three gaps, not eleven: nothing asserted the tag of the merged
root, nothing drove a document that failed to parse or an empty scope group
ahead of a populated one, and nothing built a target carrying
`policies_xml_documents`. Five tests in `tests/test_effective_policy.py` close
all three. The one remaining survivor is an accepted equivalent, recorded
against the test that would otherwise be expected to kill it.

`app/backend_pool.py` was the first module worked this way. Its first run
reported 72% with **106 mutants no test reached at all**: the module read as 88% line-covered, but
every mutant in `_apply_auth_type`, `_apply_authorization_header` and
`_apply_credential_pairs` was untouched. Those functions were exercised only
incidentally, by gateway requests that happened to pass through them.
`tests/test_backend_pool_unit.py` drives them directly, and the module now
scores 98% with nothing unreached and 98% line coverage.

That 72% figure came from the same broken scorer as everything else, so treat it
as indicative of the direction rather than as a measurement. The 106 unreached
mutants were counted correctly, and they were the point.

Its 28 survivors were worked in a later pass and are now four, all recorded
equivalents. Three groups accounted for almost all of them. The health map
handed back a repaired entry without storing it, and nothing noticed because no
test mutated what it was given. The rotation walked forward past an open
circuit, and no test had an open circuit to walk past, so walking backwards
scored the same. And every credential is rendered as a policy value, able to
read the request and to resolve a named value from the gateway config, but each
test exercised only one of those two, so a mutant that dropped the other went
unnoticed. Every credential value in the suite now depends on both.

That last one is the trap worth naming. The first attempt at these tests scored
95%, not 98%: six mutants survived precisely because each new test proved half
of what it looked like it proved.

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

## What it found next: a leading negation that would not parse

`@(!false)` raised `IndentationError`. The translator rewrites a leading `!`
into `" not "`, and `ast.parse` reads that leading space as an indent, so every
expression that opened with a negation failed before it was evaluated. The
translated text is stripped before parsing now.

Two pieces of dead code came out of the same module, both found because their
mutants could not be killed. The interpolation scanner guarded against a `{`
whose predecessor was also a `{`, which cannot happen: the earlier brace opens
an expression and moves the cursor past it. Escaped `{{` is therefore not
supported, and never was. The AST validator and the `eval` locals both named
`True` and `False`, which parse as `ast.Constant` and never reach a name lookup.

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

## Reconciling out of process

`scripts/mutation-test.sh --reconcile` takes the mutants mutmut recorded with an
exit code it cannot turn into a verdict, and re-runs each one on its own in a
fresh process. Out of the fork, they do not crash, and each returns a verdict
that is folded back into the score. It costs one suite run per mutant -- about
fifteen minutes for the 54 in `app/named_values.py` -- so it is opt-in rather
than part of the default pass.

The mutated tree has to be on the import path for this, and getting that wrong
fails silently in the worst possible direction. `PYTHONPATH` must point at
`mutants/`: without it pytest imports the installed package instead, the
trampoline never sees its own module, the suite passes, and **every mutant is
reported as a survivor**. The check that catches this is to re-run a mutant
mutmut already scored as killed: it must fail.

## Open: mutmut's forked worker crashes on some modules

mutmut runs each mutant by `os.fork()` with no `exec`, then running pytest
in-process in the child. On this macOS host a large and perfectly reproducible
share of mutants in some modules come back `segfault`.

What is known:

- It is deterministic per module, but not uniform across them.
  `app/named_values.py` returns exactly 54 segfaults out of 120 on every run.
  `app/urls.py` returned 0 on one run, then 3, then 2, so the rate varies
  between runs even where the module is small.
- It is not a timeout. Raising `timeout_constant` and `timeout_multiplier`
  changed nothing.
- It is not concurrency. `--max-children 1` gives the same 54.
- It is not the mutants. Running a segfaulting mutant directly, with
  `MUTANT_UNDER_TEST` set and the same pytest arguments, passes all 281 tests.
- They are real segfaults. Every one of the 54 exits on signal 11, read from the
  exit codes mutmut writes to `mutants/app/<module>.py.meta`. An earlier draft of
  this document guessed most were `SIGKILL` from the child's CPU-time limit,
  which mutmut also labels `segfault`; the recorded codes say otherwise.
- It is not macOS fork safety. `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` gives
  the same 54.
- It correlates with survivors. All 54 turned out to survive, and a surviving
  mutant is the one whose child runs the whole suite rather than exiting at the
  first failure. Whatever the crash is, it is reached late in a full run.
- It is module-specific in degree. `app/effective_policy.py` and
  `app/backend_pool.py` reconcile fully; `app/named_values.py` loses 45% of its
  mutants.

Until this is understood, `--reconcile` is the way round it. Treat any module
the runner declines to score, and that has not been reconciled, as unmeasured
rather than as passing.

## Accepted equivalents

Some mutants change the source without changing any behaviour an honest test
could observe. Record those next to the test that would otherwise be expected to
kill them. Never drop one silently from the score: a survivor you cannot explain
is a gap, and a survivor you can explain is documentation.

Three are recorded so far. `policy_xml_documents_for_target` reads its list with
`getattr(target, "policies_xml_documents", [])` and passes the result through
`or []`, so changing that default to `None` cannot change the returned list for
any target. The note sits beside the test in
`tests/test_effective_policy.py`.

The expression evaluator keeps two. `eval` is handed an empty `__builtins__`,
and four mutants weaken or remove it and survive, because `_validate_ast`
refuses every name outside the allowlist before `eval` is ever reached: nothing
that gets that far can name a builtin. The empty globals stay as a second line
of defence rather than because a test can observe them. Separately, spelling an
encoding "UTF-8", changing the maxsplit of a split whose `[0]` is all anyone
reads, and upper-casing a literal inside a pattern compiled with
`re.IGNORECASE` are all the same source in different words.

## Holding the line

A new function in `app/` arrives with its own tests, and a re-mutation is what
proves those tests are worth having. Sixteen passing tests is not the same as
sixteen tests that would notice.
