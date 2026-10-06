# Next Features

Apply the [agent operating model](AGENT-SYSTEM.md) to every roadmap item: identify its contract and owning abstraction, expose authored and effective state through shared management behavior, make action effects inspectable, and attach focused outcome evidence. The completed [agent system plan](AGENT-SYSTEM-PLAN.md) establishes this operating foundation.

This file tracks open areas that would materially expand the simulator. It is not an acceptance log for work that has already shipped.

Feature status and evidence boundaries live in
[FIDELITY-CONTRACTS.md](FIDELITY-CONTRACTS.md) and the owner-test matrix in
[`contracts/contract_matrix.yml`](../contracts/contract_matrix.yml). A roadmap
item becomes supported only after its named subset, exclusions, Microsoft Learn
reference, and owner tests are explicit.

## Highest-Value Next Work

### Broader Sample Compatibility Coverage

Keep growing the curated APIM sample fixture set under [`tests/fixtures/apim_samples/`](../tests/fixtures/apim_samples/).

Good additions are:

- widely used policy patterns
- behaviours that are easy to verify locally
- cases where the simulator needs to clearly label support as `supported`, `adapted`, or `unsupported`

### Better Import Fidelity

The bounded OpenAPI 2 JSON and 3.0.x importer now projects operation IDs,
parameters, schemas, request/response representations, and examples. It follows
first-HTTPS server selection, required-query translation, body omission for
GET/HEAD/OPTIONS, and documented reimport operation matching. See the
[implemented contract](FIDELITY-CONTRACTS.md#openapi-import--verified-subset).

Further work needs separate contracts for additional serialization, parameter
schema constraints, referenced components, `x-ms-paths`, and OpenAPI 3.1.
External references and inferred security definitions remain excluded. Add
Azure comparison fixtures before extending fidelity claims beyond the
Microsoft documentation and local regressions.

### Broader Local Management Workflows

Expand low-risk local CRUD and operator-console workflows where they make the simulator easier to use:

- better editing flows for descriptive resources
- stronger persistence ergonomics for config-authored resources
- clearer management summaries for large imported configs
- explicit concurrent-edit conflict handling (atomic save failure preservation
  is implemented; ETags and multi-writer coordination remain outside scope)

### Runtime revision fidelity

Management-created revisions now support independent snapshots, `;rev=N`
routing, offline state, promotion and local release notes. Remaining work is
reconstructing complete snapshots from Terraform-imported revisions, arbitrary
source-API cloning, and broader live Azure differential fixtures. See the
[revision contract](FIDELITY-CONTRACTS.md#revisions-and-releases--local-snapshot-contract).

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
sibling on GitHub, not necessarily adjacent on disk. The compose overlay
landed: `make up-ai-foundry` attaches the gateway to that simulator's
`aifoundry` Docker network with
[examples/ai-gateway/apim.foundry.json](../examples/ai-gateway/apim.foundry.json),
and `make smoke-ai-foundry` asserts the integration end to end (see
[AI-GATEWAY.md](AI-GATEWAY.md)). The remaining work is the two policies as
thin adapted clients targeting that service — the same way the AI gateway
example targets the mock LLM backend. Do not implement embeddings or
moderation logic here, and do not assume the two checkouts share a parent
directory.

## Still Deferred

- External cache backends
- Full APIM expression-engine compatibility
- `quota-by-key` bandwidth enforcement
- `llm-semantic-cache-lookup`/`-store` and `llm-content-safety` (both simulate other Azure services; see [ADR 0001](adr/0001-goldilocks-ai-gateway-scope.md))
- Developer portal CMS, theming, email, and notification features (the adapted consumer workflows ship at `/apim/portal`)
- Full ARM or SDK wire compatibility

## Bar For New Work

1. The feature must be testable locally.
2. The feature must improve learning, debugging, or iteration speed.
3. The feature must document any adapted behaviour instead of implying Azure parity.
