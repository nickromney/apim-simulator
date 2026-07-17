# ADR 0001: Goldilocks scope for AI gateway, shared-gateway RBAC, AWS counterpart, and CLI

- Status: accepted
- Date: 2026-07-17
- Deciders: repo maintainer (goal set), implementation session (detail decisions)

## Context

The goal for this iteration was a "goldilocks" local simulation of Azure API
Management: as much of the product as is honest and useful in Docker, while
accepting that APIM is a very large product (far larger than AWS API Gateway)
and that full parity in a container is neither achievable nor the point.

The repository already covered a large slice before this iteration:
config-driven APIs/products/subscriptions, an XML policy subset including
`validate-jwt` and `rate-limit*`/`quota*` with real 429/403 behaviour, OIDC via
Keycloak, mTLS modes, tracing, OTEL/LGTM, a consumer portal at `/apim/portal`,
an operator console, Terraform/OpenTofu import, and a curated compatibility
harness with a contract matrix. That meant the "must have" list was mostly
about the gaps:

1. AI/LLM workload awareness — nothing in the simulator understood LLM
   traffic, token budgets, or the Azure GenAI gateway policies.
2. A believable "one gateway shared by many workloads, segregated by policy
   and RBAC" story, matching the cost-saving pattern of pointing several AKS
   workloads (each with its own managed identity) at one APIM instance.
3. An AWS API Gateway counterpart to compare against.
4. Cherries: a low-code portal surface (largely already shipped) and a
   CLI/API for the simulator itself.

## Decisions

### D1. Extend the existing simulator; do not adopt another gateway engine

Kong, Envoy, and NGINX all have AI-gateway offerings, and wrapping one of them
would have given us token limiting "for free". Rejected because the entire
value of this repo is that the gateway behaves like **APIM**: XML policies,
`Ocp-Apim-Subscription-Key`, trace shapes, management surface, Terraform
import. Bolting a Kong or NGINX container alongside would create a second
policy language and teach the wrong product. The right goldilocks move is to
implement the small, high-value subset of Azure's GenAI gateway policies
natively in the existing engine, and to *document* how Kong and NGINX solve
the same problems (see `docs/AI-GATEWAY.md`) so the concepts transfer.

### D2. AI gateway scope: `llm-token-limit` + `llm-emit-token-metric`, adapted

Azure's GenAI gateway capabilities cluster into four groups:

| Capability | Azure policy | Decision |
| --- | --- | --- |
| Token rate limiting / quotas | `llm-token-limit` (alias `azure-openai-token-limit`) | **Implemented (adapted)** |
| Token metrics | `llm-emit-token-metric` (alias `azure-openai-emit-token-metric`) | **Implemented (adapted)** |
| Semantic caching | `llm-semantic-cache-lookup` / `-store` | **Deferred** — needs an embeddings model + vector store; a fake similarity metric would teach the wrong mental model |
| Content safety | `llm-content-safety` | **Deferred** — proxies to the Azure AI Content Safety service; simulating moderation verdicts locally is misleading |
| Load-balanced backend pools + circuit breakers | backend `type: Pool` | **Deferred** — worth doing later as a backend-model feature, not policy work; the simulator's retry/`proxy_retry_statuses` already covers the failover demo |

Implementation notes for the two shipped policies (all labelled *adapted* in
the contract matrix):

- Enforcement mirrors Azure's documented semantics: sliding 60s window for
  `tokens-per-minute` (429 + `Retry-After`), fixed UTC-truncated windows for
  `token-quota`/`token-quota-period` (403), `estimate-prompt-tokens="true"`
  blocks before the backend call, `="false"` counts actual `usage` from the
  model response and lets the first overshoot through — exactly the behaviour
  the real policy documents.
- Token counting uses the response `usage` block when present (OpenAI
  `prompt_tokens`/`completion_tokens` and Anthropic-style
  `input_tokens`/`output_tokens`), and falls back to a ~4-chars-per-token
  estimate for streamed/non-JSON responses. Azure likewise falls back to
  estimation for streaming. The estimator is deliberately simple; it is for
  exercising limit behaviour, not billing.
- `llm-emit-token-metric` feeds an OTEL counter (`apim.llm.tokens`) with the
  policy's dimensions, so the LGTM overlay charts token spend per subscription
  with no extra wiring.
- A mock LLM backend (`examples/llm-backend/`) speaks both the OpenAI
  (`/v1/chat/completions`) and Azure OpenAI
  (`/openai/deployments/{id}/chat/completions`) shapes, returns deterministic
  completions with real usage numbers, and supports SSE streaming — enough to
  demo an "AI Gateway" end to end (`make up-ai && make smoke-ai`) without any
  cloud dependency or GPU.

### D3. Shared gateway with RBAC segregation: configuration, not new engine code

The "pay for one APIM, share it across AKS workloads via managed identity"
pattern maps cleanly onto capabilities the simulator already had:

- Each AKS workload's managed identity is, at the wire level, just an OIDC
  client-credentials client whose JWT carries identifying claims. Locally,
  Keycloak client-credentials clients play that role.
- Per-API segregation is `validate-jwt` plus required role/claim checks —
  workload A's token opens `/team-a/*`, workload B's opens `/team-b/*`, and a
  shared API accepts both.

So the deliverable is a worked example (`examples/shared-gateway/`) and
documentation, not new gateway features. This is a deliberate goldilocks
call: inventing a fake IMDS/token-endpoint for "real" managed identity would
add moving parts without changing what the gateway itself does — validate a
JWT and enforce claims.

### D4. AWS API Gateway counterpart: LocalStack, not a homegrown simulator

Building our own AWS API Gateway simulator fails the "good taste" test: it is
someone else's product, LocalStack already simulates it well in the community
edition, and this repo's AWS value is the *mapping* (already captured in
`docs/MIGRATING-FROM-AWS-API-GATEWAY.md` and the migration example config).

Decision: ship an opt-in `compose.aws.yml` overlay running LocalStack with an
init script that creates a REST API (fixed ID via LocalStack's `_custom_id_`
tag) proxying to the same mock backend the simulator fronts. That gives a
side-by-side: the same upstream reached through an APIM-shaped gateway on
:8000 and an AWS-shaped gateway on :4566. The overlay is deliberately not part
of `up-all` or CI — it pulls a large third-party image and is a comparison
aid, not a core stack.

### D5. CLI and portal cherries

- The low-code/no-code portal ask is already substantially met by
  `/apim/portal` (consumer sign-up/try-it) and the operator console (`make
  up-ui`). Building a CMS-style portal editor remains explicitly out of scope
  (see `docs/SCOPE.md`). No new work beyond documentation.
- A small `apimsim` CLI (console script over the existing tenant-key
  management API) closes the "API/CLI for this new product" ask: `status`,
  `apis`, `products`, `subscriptions`, `trace`, `replay` against any running
  simulator. It is a thin HTTP client by design — the management API stays the
  single source of truth, and the CLI never grows private back doors.

## How Kong and NGINX informed the AI-gateway shape

- **Kong AI Gateway** normalises many providers behind `ai-proxy`, then layers
  plugins: token-based rate limiting, prompt guarding/templating, semantic
  caching. Its token-rate-limiting design (count from provider usage in the
  response, optionally estimate on request) matches the shape we implemented.
- **NGINX / F5 AI Gateway** splits "core" reverse proxying from AI
  "processors" (prompt security, PII redaction) that run out-of-band. Open
  source NGINX has no token awareness; you get `limit_req` on requests only.
  This reinforced keeping request-rate limiting (`rate-limit*`) and
  token-rate limiting (`llm-token-limit`) as separate policies, as Azure does.
- **Azure APIM GenAI policies** are the reference semantics; where the three
  disagree, the simulator follows Azure because that is the product being
  simulated.

## Consequences

- The simulator can now demo an AI Gateway locally: subscription-keyed token
  budgets, 429/403 with `Retry-After`, remaining/consumed token headers, token
  metrics in Grafana, streaming passthrough — with zero cloud calls.
- Semantic caching, content safety, and backend pools are documented
  deferrals in `docs/SCOPE.md`/`docs/NEXT-FEATURES.md`, keeping the repo's
  "adapted, honestly labelled" discipline (new contract-matrix entries:
  `POLICY-LLM-TOKEN-LIMIT`, `POLICY-LLM-EMIT-TOKEN-METRIC`).
- The token estimator is intentionally crude (~4 chars/token). Tests and docs
  say so. Anyone using it to predict real Azure billing is holding it wrong.
- LocalStack is a new (opt-in) third-party dependency; it is isolated to one
  overlay and one smoke script and can be deleted without touching the core.
- The AWS overlay uses LocalStack community, which covers REST API v1 HTTP
  proxy integrations well; v2 HTTP APIs and authorizers vary by LocalStack
  version and are out of scope for the smoke test.
- Amended by [ADR 0002](0002-localstack-archival-pin.md): the LocalStack
  community line was archived in March 2026, and the pin is now a deliberate
  frozen-image decision with documented revisit triggers.
- Amended by [ADR 0003](0003-gap-closure-round.md): backend pools/circuit
  breakers and streamed token counting moved from deferred to shipped
  (adapted); semantic cache and content safety are now held for a possible
  AI Foundry simulator rather than plain-deferred.
