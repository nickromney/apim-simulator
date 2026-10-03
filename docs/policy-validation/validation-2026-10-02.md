# Local policy guide validation — 2026-10-02

The earlier eleven Get Started tutorial changes are committed as `be578e3` on
`codex/apim-tutorial-validation`. This follow-on validates the thirteen policy
guides explicitly listed in the request. Their source inventory contains 48
code blocks and 33 XML specimens, with hashes, attribution and recorded local
adaptations. It does not recursively add the policy-reference catalog.

The local lab uses gateway port 8900 and companion port 8901 in a separate
Compose project. Assertions run from the host through those published ports;
Python gateway tests provide additional boundary and failure-path coverage.

## Outcomes

| Guide | Verified local behavior |
|---|---|
| Policies overview | Inheritance ordering, replacement, base suppression, cross-domain file, identical traced/untraced bodies |
| Set/edit | Management save/read/update, effective ordering, atomic invalid-save rejection and source IP allow/deny |
| Copilot authoring | Locally authored prompt outcomes: five requests/second, response-header removal and JWT-validated role-dependent response fields |
| VS Code debugging | Backend/header authoring, scoped debug token, trace variables/steps, body-preserving replay and 100 requests/minute |
| Expressions | Source expressions, user/deployment metadata, dates, encoding, JWT inspection and supported C# expression forms |
| Fragments | Owner/region context, shared update across APIs, inclusion, dependency protection and documented structural/512 KB checks |
| Error handling | LastError context, policy identifiers and on-error response status/body/header transforms |
| Advanced logging | Local topic delivery, request/response correlation, partition metadata, credential filtering and unchanged request/response bodies |
| Advanced throttling | Subscription/IP/JWT/custom-key isolation, classic sliding windows, injected-clock token-bucket refill, deferred increments and bandwidth quota |
| External services | Active/inactive/null reference tokens, retained incoming body, background notification and four-service dashboard composition with query parameters |
| Service Bus | Queue delivery, topic fanout to two independent subscribers, payload/properties/TTL, immediate 201 and sender-only authorization |
| Named values | Secret masking, value updates, display-name rename propagation and live vault rotation without policy reload |
| GraphQL resolvers | Protected schema/resolver authoring, HTTP field resolution, arguments/variables, nested parent data, schema projection and invalid-query rejection |

## Fixes exposed by execution

Body transforms and HTTP callouts must not retain incoming Content-Length.
The host-port test exposed this in ordinary forwarding, and review extended the
fix to callouts and GraphQL resolvers. On-error execution now treats status,
body and header changes as response actions. Reading and restoring an absent
global policy preserves its default backend forwarding instead of saving an
explicit empty backend. Event Hub logging honors the configured sender identity.

Named-value secret resolution uses one snapshot per HTTP request, shared by
policy execution and redaction. Subsequent requests resolve rotated secrets,
while a secret used earlier in a request remains available for masking that
request's trace. Malformed and oversized secret responses fail resolution.

## Adaptations and boundaries

Service Bus and Event Hub use a separate, simple HTTP pub/sub container backed
by SQLite. The lab exercises publishers, queues, topics, independent subscribers
and consumers. It does not promise AMQP, distributed counters/delivery or Azure's
identity infrastructure. Namespace and allowed sender identity grants are
explicit local configuration. The same companion container supplies reference
introspection and a secret store with separate administrator and reader roles.

The AI/editor guides use local management authoring and debugging APIs. The
resulting policy behavior is tested; no Azure CLI emulator, VS Code extension
or Copilot service integration is introduced. Trace inspection and replay are
the local debugging equivalent.

Advanced logging replaces the guide's C# LINQ HTTP serialization block with a
read-only `ToHttpMessage` helper. XML repairs, placeholder endpoints, and local
backend substitutions are recorded in the source inventory. The expression
engine remains a bounded C# compatibility layer; the extensive .NET member
reference table is not an assertion that every member works. GraphQL validation
covers the guide's HTTP resolver examples; SQL/Cosmos links are separate work.

See [the runnable lab](../../examples/apim-policies/README.md),
[the source inventory](INVENTORY.md), [throttling findings](throttling-findings.md),
and [expression/resolver findings](scopes-auth-findings.md) for the individual
assertions and source mappings.

## Reproduce

```sh
make -C examples/apim-policies smoke
make -C examples/apim-policies verify
make -C examples/apim-policies down
uv run --extra dev pytest -q
uv run --extra dev ruff check app tests examples/apim-policies
uv run --extra dev ruff format --check app tests examples/apim-policies
make test-shell
```

Both consecutive host-port runs passed every guide journey. Their assertions
and results are preserved in [live-results-2026-10-02.json](live-results-2026-10-02.json).
The final Python suite passed **927 tests**, with one optional Keycloak integration
test skipped. Ruff lint and formatting, Markdown lint, YAML lint, Compose configuration and
diff whitespace checks passed. The shell suite passed all 41 tests. The optional
external Keycloak integration test is excluded unless `RUN_INTEGRATION=1`.

The adversarial review found and fixed logging identity selection, query logging,
streamed-response quota accounting and resolver Content-Length handling. Focused
regressions cover each; no unresolved review finding remains.
