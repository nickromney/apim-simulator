# AI Gateway Example

One simulator gateway in front of the mock LLM backend
([examples/llm-backend](../llm-backend/main.py)), with token-limit and
token-metric policies applied per subscription.

```bash
make up-ai
make smoke-ai
```

- `POST /openai/deployments/{deployment}/chat/completions` — Azure OpenAI
  shape, `tokens-per-minute` limited (429 + `Retry-After` when exceeded)
- `POST /llm/v1/chat/completions` — OpenAI-compatible shape, daily
  `token-quota` limited (403 when exhausted)
- `POST /v1/responses` — OpenAI Responses API shape (mock backend only)
- `POST /v1/messages` — Anthropic Messages API shape (mock backend only)
- Subscription keys `ai-team-alpha-key` and `ai-team-beta-key` each get their
  own token counters; the `api-key` header works as a subscription key header
- The Azure OpenAI-shaped API load-balances across two mock deployments via
  the `llm-pool` backend; `x-apim-backend-id` shows the member that served
  each call

Full policy semantics, adapted-behaviour notes, and the Kong/NGINX comparison
live in [docs/AI-GATEWAY.md](../../docs/AI-GATEWAY.md).
