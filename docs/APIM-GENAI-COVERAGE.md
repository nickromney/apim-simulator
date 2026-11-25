# Coverage of the "Azure API Management with Generative AI" video

A maintainer check of this simulator and the sibling
[aifoundry-simulator](https://github.com/nickromney/aifoundry-simulator)
against John Savill's
[Azure API Management with Generative AI](https://www.youtube.com/watch?v=l_8dTUwrqNw)
(September 2024, 39 minutes). The question was whether each feature the video
teaches has a local Docker equivalent, split between the gateway (this repo)
and the model service behind it (the Foundry simulator).

**Covered** means a working local equivalent exists. **Adapted** means it works
with a documented difference. **Out of scope** means a local simulation would
teach the wrong thing.

| # | Video chapter | Status | Where it lives |
| --- | --- | --- | --- |
| 1 | App swaps only the endpoint; model key becomes an APIM subscription key | Covered | `api-key` is accepted as a subscription key header in [examples/ai-gateway](../examples/ai-gateway/README.md); per-subscription limits and metrics |
| 1 | Entra ID (JWT) from the app instead of a key | Covered | `validate-jwt` with Keycloak ([walkthrough-oidc-gateway.md](walkthrough-oidc-gateway.md)) |
| 2 | Azure OpenAI and Azure AI Model Inference API backends | Covered | Foundry simulator serves `/openai/deployments/...` and `/models/chat/completions`; the mock LLM backend speaks the OpenAI shapes |
| 3 | Managed identity from APIM to the model | Covered | `make up-ai-foundry-keyless` ([MANAGED-IDENTITY-TO-FOUNDRY-PLAN.md](MANAGED-IDENTITY-TO-FOUNDRY-PLAN.md)) |
| 4 | Portal onboarding that creates token policies | Out of scope | Portal wizard. The policies it writes are in the example configs |
| 5 | Token limit per subscription, IP or expression; estimate prompt tokens; retry and remaining headers | Adapted | `llm-token-limit` ([AI-GATEWAY.md](AI-GATEWAY.md#llm-token-limit)). Token counts use a heuristic, not a tokenizer |
| 6 | Emit token metrics with default and custom dimensions | Adapted | `llm-emit-token-metric` feeds the OTEL counter `apim.llm.tokens` instead of Application Insights |
| 7 | Load-balanced pool: round robin, weighted, priority | Covered | `type: pool` backends |
| 7 | PTU first, circuit breaker on 429 honouring `Retry-After`, spill to pay-as-you-go | Covered | `/ptu/openai` in [examples/ai-gateway/apim.json](../examples/ai-gateway/apim.json) with a capacity-limited mock deployment; `make smoke-ai` checks it ([AI-GATEWAY.md](AI-GATEWAY.md#ptu-first-pay-as-you-go-spillover)) |
| 7 | Per-subscription fairness in front of shared capacity | Covered | `llm-token-limit` keyed by `context.Subscription.Id` |
| 8 | Log prompts and completions through diagnostic settings; not for streamed responses | Adapted | Diagnostic body logging ([DIAGNOSTIC-LOGGING.md](DIAGNOSTIC-LOGGING.md)). Streamed bodies are recorded as `body_unavailable: streamed`, matching the video's caveat. There is no separate LLM log table |
| 9–12 | Semantic caching: embeddings backend, vector cache, score threshold, ignore system messages, max message count, cache duration, trace shows miss then hit | Adapted | `llm-semantic-cache-lookup`/`-store` call the Foundry simulator's embeddings deployment; `/cached/openai` in [apim.foundry.json](../examples/ai-gateway/apim.foundry.json); `make smoke-ai-foundry` checks it ([AI-GATEWAY.md](AI-GATEWAY.md#llm-semantic-cache-lookup-and-llm-semantic-cache-store)). Vectors live in gateway memory instead of Redis Enterprise |
| 13 | Chaining a small model before the large one | Covered | Not a built-in policy in Azure either; `send-request` plus `set-body` |
| 13 | AI-Gateway samples: content safety, streaming, function calling | Covered | `llm-content-safety` against the Foundry simulator; SSE pass-through in `make smoke-ai` |

## Notes

- The video describes a lower score threshold as a looser match. Microsoft's
  policy reference defines `score-threshold` the other way: lower values need
  closer prompts. The simulator follows the reference, as a cosine-distance
  ceiling.
- The video's demo uses a threshold of 0.4 with a neural embeddings model.
  The Foundry simulator's embeddings are lexical, so 0.4 would serve the
  time-off answer to "What is the parental leave policy?" (distance 0.33).
  The example uses 0.2. The wrong-answer case is the semantic-cache risk the
  video warns about; it shows up at a different number here.
