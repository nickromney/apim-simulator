# AI Gateway

Azure API Management positions itself as an "AI gateway" in front of LLM
backends: it meters token spend, enforces token budgets per caller, emits
token metrics, and load-balances model deployments. This document describes
the slice the simulator ships, how it maps to Azure, and how the same
problems look in Kong and NGINX.

Scope decision and rationale: [ADR 0001](adr/0001-goldilocks-ai-gateway-scope.md).

## Run it

```bash
make up-ai
make smoke-ai
```

The stack pairs the gateway with a mock LLM backend
([examples/llm-backend](../examples/llm-backend/main.py)) that speaks both API
shapes and returns deterministic completions with real `usage` numbers:

- Azure OpenAI shape: `POST /openai/deployments/{deployment}/chat/completions`
- OpenAI-compatible shape: `POST /llm/v1/chat/completions`

Both are subscription-protected by the `ai-workloads` product in
[examples/ai-gateway/apim.json](../examples/ai-gateway/apim.json). The Azure
OpenAI-shaped API carries a `tokens-per-minute` limit; the OpenAI-compatible
API carries a daily `token-quota`. The `api-key` header (Azure OpenAI
convention) is accepted as a subscription key header alongside
`Ocp-Apim-Subscription-Key`.

```bash
curl -s http://localhost:8000/openai/deployments/gpt-demo/chat/completions \
  -H "api-key: ai-team-alpha-key" \
  -H "content-type: application/json" \
  -d '{"model":"gpt-demo","messages":[{"role":"user","content":"hello"}]}' | jq .usage
```

Repeat the call and watch `x-apim-remaining-tokens` fall until the gateway
returns `429` with `Retry-After`. Each subscription gets its own counter, so
`ai-team-beta-key` keeps working after `ai-team-alpha-key` is throttled.

The Azure OpenAI-shaped API sits behind a **backend pool** (`llm-pool`) that
round-robins across two mock deployments with an adapted circuit breaker —
watch the `x-apim-backend-id` response header alternate between `llm-backend`
and `llm-backend-b`, and see [ADR 0003](adr/0003-gap-closure-round.md) for
the pool semantics.

## llm-token-limit

Adapted implementation of the Azure policy (alias:
`azure-openai-token-limit`). Supported attributes:

`counter-key` (expressions allowed), `tokens-per-minute`, `token-quota`,
`token-quota-period` (`Hourly|Daily|Weekly|Monthly|Yearly`),
`estimate-prompt-tokens`, `retry-after-header-name`,
`retry-after-variable-name`, `remaining-tokens-header-name`/`-variable-name`,
`remaining-quota-tokens-header-name`/`-variable-name`,
`tokens-consumed-header-name`/`-variable-name`.

Behaviour follows the documented Azure semantics:

- `tokens-per-minute` uses a sliding 60-second window; exceeding it returns
  `429` with `Retry-After`. `token-quota` uses fixed windows truncated to the
  UTC period start; exceeding it returns `403`.
- `estimate-prompt-tokens="true"` estimates the prompt cost before the
  backend call and can block up front. `="false"` counts actual usage from
  the model response, so the first overshooting request reaches the backend
  and subsequent requests are blocked — the same trade-off Azure documents.
- Consumption comes from the response `usage` block (OpenAI
  `prompt_tokens`/`completion_tokens`/`total_tokens`, or Anthropic-style
  `input_tokens`/`output_tokens`). SSE streams are parsed too: a final usage
  chunk (`stream_options.include_usage`, Anthropic `message_delta`) is
  authoritative, otherwise completion tokens are estimated from the content
  deltas on top of the prompt estimate. Error responses (4xx/5xx without
  usage) are not counted.

Adapted, not parity:

- Token estimation is a ~4-characters-per-token heuristic, not a model
  tokenizer. Use it to exercise limit behaviour, not to predict billing.
- Counters live in gateway memory (like the other `rate-limit*` policies):
  restart resets them, and there is no cross-instance aggregation.
- Image-input token counting is not modelled.

## llm-emit-token-metric

Adapted implementation (alias: `azure-openai-emit-token-metric`). Declares a
`namespace` and `<dimension>` children like Azure. Dimensions without a
`value` resolve the common defaults (`API ID`, `Operation ID`,
`Subscription ID`, `Client IP address`). Instead of Application Insights,
token counts feed the OTEL counter `apim.llm.tokens` with attributes
`apim.llm.token.type` (`prompt|completion|total`), the namespace, and each
dimension — so any OTEL-enabled stack (for example `make up-otel`) can chart
token spend immediately.

Every gateway trace (`x-apim-trace: true`) also records
`llm-token-limit`/`llm-emit-token-metric` steps with the counted tokens,
which is the quickest way to see the policies work.

## Fronting the sibling AI Foundry simulator

The mock LLM backend exercises the gateway policies, but the deferred
concerns — semantic caching and content safety — simulate other Azure
services, and those now exist as their own project:
[aifoundry-simulator](https://github.com/nickromney/aifoundry-simulator)
(a sibling on GitHub, not necessarily adjacent on local disk). Its `make up`
publishes model deployments, a semantic cache, and the Azure AI Content
Safety API on a Docker network named `aifoundry`; this repo's
`compose.ai-foundry.yml` overlay attaches the gateway to that network and
loads [examples/ai-gateway/apim.foundry.json](../examples/ai-gateway/apim.foundry.json),
which proxies `/openai` and `/contentsafety` to
`http://aifoundry-simulator:8000` and injects the Foundry backend key via
`set-header`.

```bash
# in your aifoundry-simulator checkout
make up

# in this repo
make up-ai-foundry
make smoke-ai-foundry
```

The smoke test asserts, end to end through the gateway: subscription 401s,
completions with real `usage` numbers feeding `llm-token-limit` (429 after
the budget), `x-semantic-cache` miss-then-hit on a repeated prompt,
`[simulate:violence=6]` returning Azure's 400 `content_filter` body
unchanged, deterministic embeddings, and the Content Safety
`text:analyze` / `text:shieldPrompt` operations.

The `llm-semantic-cache-*` and `llm-content-safety` *policies* remain
unimplemented (see the capability matrix): caching and filtering happen
service-side in the Foundry simulator, which is where Azure hosts them too.
Gateway-side thin clients of that service are the natural next step —
tracked in [NEXT-FEATURES.md](NEXT-FEATURES.md).

## How Kong and NGINX solve the same problems

| Concern | Azure APIM | Kong AI Gateway | NGINX / F5 |
| --- | --- | --- | --- |
| Provider normalisation | LLM APIs imported per schema (OpenAI, Anthropic, Vertex) | `ai-proxy` plugin translates one request shape to many providers | Plain reverse proxy; no translation in OSS NGINX |
| Token rate limiting | `llm-token-limit` (usage from response, optional estimation) | `ai-rate-limiting-advanced` counts provider-reported tokens, optional cost multipliers | OSS: `limit_req` on requests only; F5 AI Gateway adds token awareness |
| Token metrics | `llm-emit-token-metric` → App Insights | Prometheus metrics per plugin | OTEL/Prometheus from proxy metrics |
| Prompt controls | `llm-content-safety` (Azure AI Content Safety) | `ai-prompt-guard`, `ai-semantic-prompt-guard` | F5 AI Gateway "processors" (prompt security, PII) |
| Semantic caching | `llm-semantic-cache-*` | `ai-semantic-cache` | Not built in |
| Multi-deployment balancing | Backend pools + circuit breaker | Upstream targets/balancers | `upstream` blocks |

The simulator implements the first three rows and multi-deployment balancing
(all adapted) because they change how you *author APIM configs and policies*;
prompt controls and semantic caching are held because they simulate other
services rather than gateway behaviour (see ADR 0001 and ADR 0003).

## What this teaches

- Put token limits at the gateway, keyed per consumer
  (`counter-key="@(context.Subscription.Id)"`), so one noisy workload cannot
  starve the shared model deployment.
- Request-rate limits (`rate-limit-by-key`) and token limits
  (`llm-token-limit`) are different budgets; AI APIs usually need both.
- Token spend belongs in your observability stack with per-consumer
  dimensions — that is what makes internal chargeback possible.
