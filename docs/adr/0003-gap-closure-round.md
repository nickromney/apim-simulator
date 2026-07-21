# ADR 0003: Gap-closure round — product scope, backend pools, validation family

- Status: accepted
- Date: 2026-07-17
- Follows: [ADR 0001](0001-goldilocks-ai-gateway-scope.md), [ADR 0002](0002-localstack-archival-pin.md)

## Context

After the AI-gateway iteration shipped, a gap review identified what still
separated the simulator from the parts of APIM that change how users author
configs and policies. The maintainer's direction: close those gaps, but hold
`llm-semantic-cache-*` and `llm-content-safety` — both important, but they
simulate *other Azure services* and may belong in a separate AI Foundry
simulator (see "Held" below).

## Decisions

### D1. Product-scope policies (adapted)

Policies now merge global → **product** → API → operation, matching Azure's
scope order (workspaces excluded — the simulator has no workspace model). The
"which product" rule is adapted and deterministic: the first published product
granted by the authorizing subscription, else the first published product on
the route (open products, subscription bypass). Azure resolves this through
the subscription's product context; for multi-product open routes the
simulator's "first published" tiebreak is documented behaviour, not parity.
The management policy surface gains a `product` scope, and the effective
product id is exposed to policies as the `product_id` variable and the
`Product ID` default metric dimension.

### D2. Backend pools with circuit breakers (adapted)

`BackendConfig` gains `type: pool` with weighted/priority members and an
optional circuit breaker (`failure_count`/`interval_seconds`/
`trip_duration_seconds`/`error_statuses`), mirroring the ARM backend pool +
`circuitBreaker.rules` shape. Adapted choices, all deliberate:

- **Deterministic weighted round-robin** instead of random selection, so
  tests and demos are reproducible. Priority groups fail over strictly
  (lower priority number first), like Azure.
- **Failover rides the existing retry loop**: a pool re-selects a healthy
  member on each retry attempt, so `proxy_max_attempts`/`proxy_retry_statuses`
  control failover, and a breaker default (3 failures/60s, 30s open) applies
  when no rule is configured — Azure requires explicit rules; the simulator
  prefers not hammering a dead member out of the box.
- **Pool members are assumed to share auth configuration** (the common
  replica/deployment case); only the base URL is recomputed on failover.
- Exhausted pools return `503`, and responses carry `x-apim-backend-pool` /
  `x-apim-backend-id` debug headers (simulator aid, not an Azure behaviour).
- Health state is in-memory per gateway instance, like every other counter.

### D3. Streamed token counting

`llm-token-limit`/`llm-emit-token-metric` now parse SSE bodies: a final
usage chunk (OpenAI `stream_options.include_usage`, Anthropic
`message_delta.usage`) is authoritative; otherwise completion tokens are
estimated from the concatenated content deltas on top of the prompt estimate.
This upgrades the previous prompt-only fallback documented in ADR 0001.

### D4. Validation policy family (adapted)

- `validate-content`: `max-size`, per-content-type rules with
  `validate-as="json"` well-formedness, `unspecified-content-type-action`,
  and `ignore|prevent|detect` actions with `errors-variable-name`. **JSON
  Schema enforcement is deferred** — it needs a `jsonschema` dependency, and
  the repo's dependency footprint discipline makes that a deliberate choice
  for later, not a drive-by.
- `validate-parameters`: required/unspecified header and query checks against
  the operation's authored request metadata. Path parameters deferred.
- `validate-status-code`: explicit `<status-code>` rules plus the operation's
  declared responses; because outbound policies cannot short-circuit in this
  engine, `prevent` mutates the response to `502` in place.
- `emit-metric`: generic custom metrics to the OTEL counter
  `apim.policy.metric` (Azure sends to Application Insights).

### D5. Small enforcement wins

- `subscriptions_limit` on products is imported and now enforced at portal
  sign-up (`409` when reached; `0` disables self-serve). The portal already
  caps one subscription per user per product, so the limit mostly matters at
  zero.
- The shared-gateway example gains an Entra-shaped variant
  (`apim.entra-claims.json`) authorizing on the `azp` application-id claim —
  the same claim Entra v2 tokens carry — selected via
  `SHARED_APIM_CONFIG_PATH`.

### D6. Built-in groups: attempted, reverted, deliberately out

Auto-registering Azure's administrators/developers/guests groups was
implemented and reverted the same hour: injection broke Terraform-import
resource counts and config round-tripping, because real configs legitimately
author their own `developers` group. The simulator's stance is now explicit:
groups exist only when a config declares them. The capability matrix keeps
the "No" row.

## Held: a possible AI Foundry simulator

`llm-semantic-cache-lookup`/`-store` and `llm-content-safety` stay out of the
gateway. Both proxy other Azure services (an embeddings model, Azure AI
Content Safety), so faithfully simulating them means simulating *those
products*, not APIM. The shape: a sibling "AI Foundry simulator" container
(deterministic embeddings, canned moderation verdicts, model catalog shapes)
that this gateway targets the same way it targets the mock LLM backend.

Status update (same day): that sibling simulator now exists as its own
project, [aifoundry-simulator](https://github.com/nickromney/aifoundry-simulator)
— a sibling on GitHub, not necessarily adjacent on local disk. This repo's
remaining scope is integration only — the two policies as thin clients of
that service, plus a compose overlay. See `docs/NEXT-FEATURES.md`.

## Consequences

- The policy surface now covers the scope model users actually author against
  (product-level token budgets per team work without per-subscription keys).
- The AI example demonstrates load-balanced deployments end to end
  (`make smoke-ai` asserts round-robin across both mock backends).
- New contract-matrix entries: `POLICY-PRODUCT-SCOPE`, `BACKEND-POOL`,
  `POLICY-VALIDATE-CONTENT`, `POLICY-VALIDATE-PARAMETERS`,
  `POLICY-VALIDATE-STATUS-CODE`, `POLICY-EMIT-METRIC`.
- Still open after this round (unchanged from the gap review): GraphQL and
  WebSocket APIs, JSON Schema content validation, transformation policies,
  `retry`/`limit-concurrency`, credential manager, ARM/SDK wire
  compatibility, and the held AI Foundry items above.
