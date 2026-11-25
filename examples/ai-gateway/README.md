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
- `POST /ptu/openai/deployments/{deployment}/chat/completions` — PTU first,
  pay-as-you-go spillover: `llm-backend-ptu` has a small tokens-per-minute
  capacity and answers 429 with `Retry-After`; the `llm-ptu-paygo` pool's
  breaker then sends calls to `llm-backend-b` until `Retry-After` passes

## Variant: fronting the sibling AI Foundry simulator

[apim.foundry.json](apim.foundry.json) points the same gateway policies at
the sibling [aifoundry-simulator](https://github.com/nickromney/aifoundry-simulator)
instead of the mock backend, adding semantic-cache and content-safety
behaviour behind the gateway. `/cached/openai` adds the gateway's own
`llm-semantic-cache-lookup`/`-store` policies, which embed each prompt with
Foundry's `text-embedding-demo` deployment. Start that simulator first
(`make up` in its checkout), then:

```bash
make up-ai-foundry
make smoke-ai-foundry
```

## Go keyless

This is Azure's recommended setup: the gateway calls Foundry with its managed
identity instead of an `api-key`, and Foundry checks the identity's role
assignment. Here, the managed identity is a JWT the gateway signs with a local
key. Foundry trusts the gateway's issuer and fetches its public key from
`http://apim-simulator:8000/.well-known/workload-identity/jwks.json` over the
shared `aifoundry` network. No Foundry key appears in
[apim.foundry.keyless.json](apim.foundry.keyless.json).

1. Start Foundry in keyless mode. In your aifoundry-simulator checkout, run:

   ```bash
   make up-keyless
   ```

   Foundry now trusts the gateway's issuer and gives the `system-assigned`
   identity the `Cognitive Services User` role. Its API keys still work.

2. Check the prerequisites. The `aifoundry` network exists and Foundry answers
   on its published port:

   ```bash
   docker network inspect aifoundry --format '{{.Name}}'
   ```

   ```bash
   curl -fsS http://127.0.0.1:8020/foundry/health
   ```

   Expected: `aifoundry`, then a JSON health document.

3. Start the gateway. This generates the signing key on first run (into
   `examples/ai-gateway/.runtime/`, which git and Docker builds ignore) and then
   replaces any key-based gateway from `make up-ai-foundry`:

   ```bash
   make up-ai-foundry-keyless
   ```

   Expected: `generated workload identity key: …` the first time,
   `kept existing workload identity key: …` after that.

4. Check the pairing:

   ```bash
   make smoke-ai-foundry-keyless
   ```

   Expected: `keyless ai foundry smoke passed`. The smoke checks that the
   gateway publishes its issuer and JWKS, and that a chat completion through
   APIM returns 200. The trace must show `authentication-managed-identity`
   and the client's `api-key` removed. Finally, Foundry must reject a JWT it
   can't verify and an APIM subscription key, both with 401.

The policy deletes the incoming `api-key` header because clients send their
APIM subscription key in it. Without the delete, the gateway would forward the
subscription key to Foundry next to the token.

To see the role check fail, remove `system-assigned` from
`auth.entra.role_assignments` in Foundry's `examples/foundry.keyless.json` and
restart Foundry with `make up-keyless`. A chat call through the gateway then
returns 401 with error code `PermissionDenied`, naming the data action the
identity lacks. Azure OpenAI returns the same when the role assignment is
missing. Put the role back and restart Foundry to restore 200s.

To go back to the key-based pairing, run `make up-ai-foundry`.

Full policy semantics, adapted-behaviour notes, and the Kong/NGINX comparison
live in [docs/AI-GATEWAY.md](../../docs/AI-GATEWAY.md).
