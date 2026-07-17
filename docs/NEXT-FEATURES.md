# Next Features

This file tracks open areas that would materially expand the simulator. It is not an acceptance log for work that has already shipped.

## Highest-Value Next Work

### Broader Sample Compatibility Coverage

Keep growing the curated APIM sample fixture set under [`tests/fixtures/apim_samples/`](../tests/fixtures/apim_samples/).

Good additions are:

- widely used policy patterns
- behaviours that are easy to verify locally
- cases where the simulator needs to clearly label support as `supported`, `adapted`, or `unsupported`

### Better Import Fidelity

Improve Terraform/OpenTofu and OpenAPI projection where it increases day-to-day usefulness:

- richer OpenAPI schema and request/response metadata projection
- clearer compatibility-report output for partially supported resources
- tighter mapping between imported metadata and the management API surface

### Broader Local Management Workflows

Expand low-risk local CRUD and operator-console workflows where they make the simulator easier to use:

- better editing flows for descriptive resources
- stronger persistence ergonomics for config-authored resources
- clearer management summaries for large imported configs

### More End-To-End Example Coverage

Prefer new examples that exercise shipped capabilities rather than speculative parity work:

- mixed auth flows
- richer mTLS examples
- policy-heavy examples that pair runtime behaviour with traces and OTEL

### AI Foundry Simulator Integration

`llm-semantic-cache-*` and `llm-content-safety` stay out of this repo (see
[ADR 0003](adr/0003-gap-closure-round.md)): both proxy other Azure services.
The AI Foundry simulator now exists as a sibling project,
[aifoundry-simulator](https://github.com/nickromney/aifoundry-simulator) —
sibling on GitHub, not necessarily adjacent on disk. Its
`examples/apim-integration/` already defines the hand-off: copy its APIM
config into this repo's `examples/` and start it with
`HELLO_APIM_CONFIG_PATH=/app/examples/ai-foundry-backend.json make up-hello`
(the Foundry backend is reachable from the gateway container at
`host.docker.internal:8020`). This repo's future work is the *integration*:
implement the two policies as thin adapted clients targeting that service —
the same way the AI gateway example targets the mock LLM backend — plus a
compose overlay that runs both simulators side by side. Do not implement
embeddings or moderation logic here, and do not assume the two checkouts
share a parent directory.

## Still Deferred

- External cache backends
- Full APIM expression-engine compatibility
- `quota-by-key` bandwidth enforcement
- `llm-semantic-cache-lookup`/`-store` and `llm-content-safety` (both simulate other Azure services; see [ADR 0001](adr/0001-goldilocks-ai-gateway-scope.md))
- Load-balanced backend pools with circuit breakers (the natural next AI-gateway step if demand appears)
- Developer portal CMS, theming, email, and notification features (the adapted consumer workflows ship at `/apim/portal`)
- Full ARM or SDK wire compatibility

## Bar For New Work

1. The feature must be testable locally.
2. The feature must improve learning, debugging, or iteration speed.
3. The feature must document any adapted behaviour instead of implying Azure parity.
