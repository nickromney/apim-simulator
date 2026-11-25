# Core reusable policy fragments

Run one shared gateway where the platform team publishes policy fragments
and each API team switches them on, off, or tunes them, without copying policy
XML between APIs.

| Fragment | Section | What it does |
| --- | --- | --- |
| [`core-rate-limit`](fragments/core-rate-limit.xml) | inbound | Call-count limit per subscription and API: 429 with `Retry-After` when exceeded |
| [`core-token-budget-check`](fragments/core-token-budget-check.xml) | inbound | Refuses model calls once the caller's token budget for the hour, day or month is spent: 403 |
| [`core-token-budget-record`](fragments/core-token-budget-record.xml) | outbound | Reads the token usage the model reports and adds it to the caller's budget |
| [`core-pii`](fragments/core-pii.xml) | inbound | Finds personal data with Azure AI Language, then redacts it (the default), refuses the request with 403, or only records it |
| [`core-content-safety`](fragments/core-content-safety.xml) | inbound or outbound | Screens prompts, or completions, with Azure AI Content Safety through `llm-content-safety`: 403 when blocked |

Azure API Management fragments take no parameters. These fragments use the
usual convention instead: each one reads context variables that the including
policy sets first, and falls back to platform defaults kept in named values.

## How a setting is chosen

For each setting, the fragment uses the first value it finds:

1. A context variable set before the fragment runs, such as
   `<set-variable name="core-rate-limit-calls" value="@(3)" />`.
2. The platform default named value, such as `{{core-rate-limit-default-calls}}`.

The platform team changes a default for every API by updating one named value.
An API team overrides it for one API with one `set-variable`. To turn a
fragment off for every API, set its `*-enabled` named value to `false`.

When the fragment is included at global scope, an API overrides it by setting
the variable *before* `<base />`, because `<base />` is where the global policy
runs:

```xml
<inbound>
  <set-variable name="core-rate-limit-enabled" value="false" />
  <base />
</inbound>
```

Every setting is listed in a comment at the top of its fragment file.

## What the example configures

[apim.json](apim.json) loads the fragments from [fragments/](fragments)
with `policy_fragments_directory`. The global policy includes `core-rate-limit`
for every API:

| API | Path | Fragment settings | Result |
| --- | --- | --- | --- |
| `orders` | `/orders/health` | `core-rate-limit-calls` = 3 | The fourth call in a minute returns 429 |
| `internal` | `/internal/health` | `core-rate-limit-enabled` = `false` | No limit; no `x-ratelimit-remaining` header |
| `chat` | `/chat/chat/completions`, `/chat/responses`, `/chat/messages` | Global rate-limit default (60 a minute), `core-token-budget-limit` = 100 tokens a day | `x-token-budget-remaining` on each response; 403 once 100 tokens are spent |
| `assistant` | `/assistant/chat/completions` | `core-pii` then `core-content-safety` inbound, `core-content-safety` outbound, platform defaults | Email addresses and other personal data reach the model as `*`; harmful prompts, prompt attacks and harmful completions return 403 |

Subscription keys `team-alpha-key` and `team-beta-key` have separate counters.

## Personal data and content safety

`core-pii` and `core-content-safety` call Azure AI services, configured by
named values:

| Named value | Example value | Used by |
| --- | --- | --- |
| `core-pii-language-endpoint` | `https://<language-resource>.cognitiveservices.azure.com` | `core-pii` |
| `core-pii-api-version` | `2026-05-01` | `core-pii` |
| `core-pii-language-key` | the Language resource key, as a secret or Key Vault named value | `core-pii` |
| `core-content-safety-backend-id` | an APIM backend for `https://<content-safety-resource>.cognitiveservices.azure.com` | `core-content-safety` |

Choose what `core-pii` does with `core-pii-action` (or the platform default
`core-pii-default-action`):

| Action | Result |
| --- | --- |
| `redact` (default) | The request body is replaced by the service's `redactedText`. Each entity's characters become `*`, so the body keeps its length and stays valid JSON |
| `block` | 403 `Request contains personal data.` when any entity is found |
| `detect` | The request is unchanged; `core-pii-entity-count` records what was found |

Limit detection to some categories with `core-pii-categories`, for example
`Email,PhoneNumber`, or leave the default `all`.

Include `core-pii` before `core-content-safety`, so the Content Safety service
never receives the personal data either.

Behaviour to plan for:

- **The whole body is one document.** `core-pii` sends the raw request body to
  the Language service, so JSON field names are scanned too. The service limits
  document size; check the
  [Language service limits](https://learn.microsoft.com/en-us/azure/ai-services/language-service/concepts/data-limits)
  for your prompt sizes.
- **Failing calls refuse the request.** If the Language call fails or times out,
  or the service reports it could not process the body (an oversized body is
  reported this way, with status 200 and no document), `core-pii` answers 503
  instead of forwarding unchecked personal data.
- **Key or managed identity.** The fragment sends the key in
  `Ocp-Apim-Subscription-Key`. To use the gateway's managed identity instead,
  replace that header with
  `<authentication-managed-identity resource="https://cognitiveservices.azure.com" />`
  inside `send-request`, and grant the identity the Cognitive Services User role.
  For a working keyless pairing with the AI Foundry simulator, see
  [Go keyless](../ai-gateway/README.md#go-keyless).
- **Content Safety 403 body.** Microsoft documents the 403 status but not the
  body. The simulator's body is its own.
- **Local Language endpoint.** The sibling
  [aifoundry-simulator](https://github.com/nickromney/aifoundry-simulator)
  serves Content Safety and, from its Language PII release, PII detection at
  `/language/:analyze-text`. Its detectors are deterministic patterns, so they
  find fewer formats than Azure. Use `[simulate:pii=Person:Jane Doe]` in a
  prompt to force a Person entity.

## Check the behaviour with the tests

The tests load this example config and its fragment files, and call the
gateway in-process with mock model, Content Safety and Language services:

```bash
uv run --extra dev pytest tests/test_core_policy_fragments.py
```

All tests should pass.

## Run it in Docker

Docker Compose must be able to reach a running Docker Engine. This uses the AI
gateway stack, which supplies the mock model backend:

```bash
docker compose version && docker info >/dev/null
```

```bash
STACK_SLOT=7 AI_APIM_CONFIG_PATH=/app/examples/policy-fragments/apim.json make up-ai
```

Slot 7 publishes the gateway on port 8700, so it leaves a stack on port 8000
alone.

Call the chat API four times through slot 7's published port. Each mock
completion reports 34 tokens, so `x-token-budget-remaining` reads 66, 32, then
0, and the fourth call returns 403:

```bash
for i in 1 2 3 4; do
  curl -s -o /dev/null -D - -X POST http://127.0.0.1:8700/chat/chat/completions \
    -H 'Ocp-Apim-Subscription-Key: team-alpha-key' -H 'Content-Type: application/json' \
    -d '{"model":"gpt-demo","messages":[{"role":"user","content":"hi"}]}' \
    | grep -iE '^HTTP|x-token-budget-remaining'
done
```

Stop the stack with `STACK_SLOT=7 make down`.

## Keeping fragments as files

Set `policy_fragments_directory` in a gateway config to load every fragment in
that directory. The path is relative to the config file. Two layouts work:

- `<fragment-id>.xml`, as in this example
- `<fragment-id>/policy.xml`, the layout the APIOps extractor writes for
  `policyFragments/`

A fragment ID defined both inline in `policy_fragments` and as a file is an
error.

## Behaviour to plan for in Azure

- **The token budget is soft.** The check runs before the model call, and the
  usage is added afterwards. Calls that start while budget remains can
  overspend it, as the second call in the example does.
- **Use an external cache for budgets.** The built-in APIM cache belongs to
  each gateway unit and region. With more than one unit, each holds its own
  count. Configure an external Redis-compatible cache so every unit reads one
  budget. The simulator keeps one in-memory cache.
- **Streamed responses are not metered.** `core-token-budget-record` only reads
  JSON bodies. To meter streams, use `llm-token-limit`, or read the final
  usage chunk that `stream_options.include_usage` adds.
- **Counters are shared by key.** `rate-limit-by-key` counters with the same
  key are shared across every API that uses that key. The default key includes
  the API ID to keep APIs apart; set `core-rate-limit-key` to share one limit
  across APIs on purpose.
- **Policy XML formats.** These files escape `<`, `>` and `"` inside policy
  expressions (`&lt;JObject&gt;`, `&quot;`), so they are valid XML. The
  [Azure parity lab](../azure-validation/README.md) publishes them as they are.
  The simulator cannot read policies that leave those characters unescaped,
  as the Azure portal allows.
