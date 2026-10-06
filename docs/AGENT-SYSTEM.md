# Agent operating model

This is the entrypoint for understanding, changing, and operating the simulator.
The system turns authored APIM-shaped intent into local request behavior and
bounded evidence. The optimization target is a correct, reproducible outcome
per unit of observation, execution, and maintenance cost.

## Abstraction tower and authority

| Layer | Question answered | Authority and implementation |
| --- | --- | --- |
| Intent and fidelity | What result may we promise? | [Scope](SCOPE.md), [fidelity contracts](FIDELITY-CONTRACTS.md), [contract owners](../contracts/contract_matrix.yml) |
| Authored state | What has the operator specified? | `app/config.py`, configuration files, `app/management_service.py`; [lifecycle](LOCAL-LIFECYCLE.md) explains persistence |
| Effective state | What will execute in this request context? | `app/resource_projection.py`, `app/proxy.py`, `app/effective_policy.py`, `app/policy_inspection.py` |
| Execution | How does intent become a result? | `app/main.py` composes; `app/request_pipeline.py` orchestrates; `app/policy.py` evaluates; security, backend, and HTTP helpers own their semantics |
| Observation | Why did this request produce this result? | Replay and traces in `app/management_api.py`, `app/telemetry.py`, local monitoring; [console](OPERATOR-CONSOLE.md) |
| Evidence and learning | Which claims survive another run? | Owner tests, focused regressions, dated reports, ADRs, concise [agent guidance](../AGENTS.md) |

Each layer exposes a smaller vocabulary than its internals. Interfaces adapt
into shared application behavior: CLI, console, portal, import, and direct HTTP
must not acquire competing policy, authorization, or persistence engines.
A change is complete when authored state, effective state, observed behavior,
and its stated fidelity boundary agree.

Distinguish seeded configuration, current in-memory state, persisted state,
and time-dependent runtime state (caches, counters, breakers, traces). Reading
a seed file does not establish what a running gateway currently does. A trace
is evidence for one request and context; a report is evidence for its recorded
revision and environment. Capability inventory is navigation, contracts bound
claims, and local tests do not establish live Azure parity.

## Operate with a tight feedback loop

1. State the desired observable result and select its contract. Record unknowns
   explicitly. Find the owner test before reading large implementation files.
2. Identify the target: base URL, stack/project/slot, API and operation IDs,
   credentials required, persistence mode, and external dependencies. Read
   [local addresses](LOCAL-ADDRESSES.md) and lifecycle only when operating stacks.
3. Observe narrowly: `apimsim status`, then `api ID` and `operations ID` for the
   relevant resource. Use `summary` only when cross-resource relationships are
   needed; it can include sensitive subscription data and require broader access.
   For initial cross-resource orientation, `inspect` collects five metadata GETs
   in one report. Check `complete` and each observation before drawing conclusions;
   `atomic: false` means the reads can see different instants or workers.
4. Inspect effective policy for the relevant scope and product. Capture the
   authored resource before replacement. Preview the intended CLI request with
   `--dry-run`; compare its target, payload, and effect with the intended change.
5. Apply one bounded change. Read back the changed resource. Verify a positive
   case and the relevant negative or boundary case; inspect the returned trace
   when causality is uncertain. Replay can invoke backend side effects and
   modify counters/caches even when its HTTP method is GET.
6. Retain the smallest durable result: regression for behavior, contract update
   for a promise, ADR for a design tradeoff, or an agent rule for a recurring
   repository trap. Record commands, revision, environment, observed result,
   and remaining uncertainty in reports. Keep credentials and sensitive bodies
   in local temporary artifacts rather than committed evidence.

Completion requires the expected result, applicable focused checks, a reviewed
diff, and an explicit statement of skipped verification. Preview checks local
argument/file construction only; it does not validate server state, policy XML,
authorization, concurrency, or Azure compatibility. A preview is not a lock:
re-read state before replacing a resource when another writer may have edited it.
ETags and multi-writer coordination remain outside the current contract.

## Machine interface

Run from the repository with `uv run apimsim`. Global flags precede the verb.
Credentials can come from `APIM_TENANT_KEY`; target from `APIM_BASE_URL`.

```bash
uv run apimsim commands
uv run apimsim status
uv run apimsim inspect
uv run apimsim api weather
uv run apimsim policy --effective --product-id starter api weather
uv run apimsim --dry-run set-policy api weather --file policy.xml
uv run apimsim set-policy api weather --file policy.xml
```

`commands` is offline, versioned JSON describing implemented CLI verbs, HTTP
paths, effects (`read`, `write`, `execute`), and deletion confirmation. It is
CLI discovery, not a runtime capability or permission claim. `--dry-run` is
offline JSON built by the execution planner, without sending HTTP or emitting
the tenant credential or credentials embedded in the displayed base URL.
The preview identifies the base URL separately from the relative request path.
It includes the supplied body verbatim, which may be
sensitive. Deletion previews work without `--yes`; execution still requires it.
`inspect` returns a versioned report of health, status, service, APIs, and products,
with each path, HTTP status and body, or a bounded transport-error label.
It continues after failed observations and exits 1 if any observation failed.
It excludes subscription collections, policy content, and traces; returned
metadata can still be sensitive. It is not a configuration export, permission
inventory, inference readiness test, or transactional snapshot.
Server responses for individual commands retain their existing JSON shapes. Success exits 0, command
or HTTP failure exits 1, and transport failure exits 2; argparse usage errors
also exit 2. A successful replay command means the replay endpoint succeeded;
check `response.status_code` to determine the gateway request outcome.

## Find context by task

| Task | Read next | Cheapest useful verification |
| --- | --- | --- |
| Policy/auth behavior | Relevant fidelity contract, owner tests, containing policy helper | Focused owner/regression tests; effective policy plus positive/negative request |
| Routing, expression cache, benchmark | [Optimization notes](GATEWAY-OPTIMIZATION-LEARNINGS.md) | Focused equivalence tests; benchmark only for a performance claim |
| Management/import | `management_service.py`, corresponding projection/import module | Round-trip and failure-preservation tests |
| Console/portal | [Console](OPERATOR-CONSOLE.md), [portal adoption](PORTAL-ADOPTION.md), nearest package manifest | Package check and relevant interaction test |
| Stack/lifecycle | Example README/Makefile, [lifecycle](LOCAL-LIFECYCLE.md) | Compose config; smoke through published host ports |
| New compatibility claim | [Contract matrix](../contracts/contract_matrix.yml), [next features](NEXT-FEATURES.md) | Named subset and exclusions, owner regression, separate Azure evidence if obtained |

Start with focused Python checks using `uv run --extra dev pytest PATH` and
`uv run --extra dev ruff check CHANGED_FILES`; broaden according to dependencies
and repository gates. Reuse a running minimal stack when its identity and state
are known. Add OIDC, TLS, OTEL, LocalStack, or cloud validation only when the
claim requires it. Bound retries; after unchanged failures, revise the causal
hypothesis rather than repeating expensive runs.

## Design constraints for subsequent work

Preserve stable resource IDs across authored, projected, executed, and observed
views. New control surfaces should expose target, effect, inputs, failure, and
verification without requiring browser reconstruction. Observations should
preserve causality and request bodies without changing execution semantics.
Configuration persistence failures must preserve the last valid state.
Keep new cross-cutting semantics in their owning module and adapt every client
to it. Accretive improvement means adding verified knowledge while retiring
superseded guidance; dated historical evidence stays dated.

## Foundry composition and sibling comparison — 2026-10-06

Reviewed AI Foundry simulator GitHub HEAD `e7be021b34` and its agent-model
change `387fd1dbf0`, matching the local checkout. Adopted its bounded inspection
report pattern: explicit non-atomicity, per-observation failure preservation,
and versioned JSON. APIM inspection stays on APIM metadata rather than copying
Foundry deployment/cache/safety semantics. Existing narrow reads remain the
cheapest choice when the target is already known.

APIM owns routing, gateway authentication, policy evaluation, and gateway token
budgets; Foundry owns deployments, model outputs/usage, service-side semantic
cache, and content filtering. For a discrepancy, compare the same payload
and deployment directly and through APIM, checking status, headers, usage,
error body, and SSE terminal markers. Gateway subscription credentials and
Foundry inference/admin credentials have separate authority. A successful
health observation establishes neither management authorization nor model
readiness. Process-local state is independent across services and workers.

Start Foundry before attaching APIM to its `aifoundry` network; stop APIM before
Foundry. The [Foundry-owned pairing lab](https://github.com/nickromney/aifoundry-simulator/blob/e7be021b34/examples/apim-integration/README.md)
uses host port 8030 and tests broader forwarding, including Responses; its smoke
flushes Foundry's semantic cache. APIM's `make smoke-ai-foundry` exercises its
own token-budget fixture. Pick the lab matching the claim and account for smoke
side effects. Service-side semantic caching/content filtering do not implement
the deferred APIM policy elements. This comparison inspected source and remote
history; it did not rerun sibling tests or container pairing smoke.
