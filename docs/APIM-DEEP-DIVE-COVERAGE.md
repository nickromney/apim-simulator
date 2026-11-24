# Coverage of the "Azure API Management Deep Dive" video

A maintainer check of the simulator against John Savill's
[Azure API Management Deep Dive](https://www.youtube.com/watch?v=PXtFq5wmGt0)
(September 2024, 70 minutes). The question was whether each feature the video
teaches has a local Docker equivalent, where one is reasonable.

**Covered** means a working local equivalent exists. **Adapted** means it works
with a documented difference. **Out of scope** means a local simulation would
teach the wrong thing, or the feature belongs to another project.

| # | Video chapter | Status | Where it lives |
| --- | --- | --- | --- |
| 3 | Control plane through ARM: portal, REST, CLI, templates | Adapted | Management API under `/apim/management`, operator console, `apimsim` CLI, Terraform/OpenTofu import, APIOps extract/plan/apply ([APIOPS.md](APIOPS.md)). No ARM or Azure CLI emulation, by design |
| 4 | Managed gateway; inbound, backend and outbound policies | Covered | The gateway; [CAPABILITY-MATRIX.md](CAPABILITY-MATRIX.md#policies) |
| 5 | Gateway URL, Traffic Manager name, units, zones | Adapted | Global URL in [regional gateways](../examples/regional-gateways/README.md). Units and zones are N/A |
| 6–7 | Premium regional gateways, performance routing, regional URLs, Front Door alternative | Adapted | [Regional gateways](../examples/regional-gateways/README.md): one simulator per region (`APIM_REGION`), nginx priority failover instead of latency routing |
| 8, 10 | Backends anywhere; HTTP, REST, OpenAPI, GraphQL, SOAP pass-through | Covered | OpenAPI import, GraphQL resolvers, backends. SOAP-to-REST transforms are not implemented |
| 8, 10 | WebSocket, gRPC, OData import | Out of scope for now | Need real transports or EDMX import ([apim-coverage](apim-coverage/README.md)) |
| 9 | Private backends through VNet integration | Adapted | Internal Docker networks in the private stack and pattern labs |
| 11 | Lock backends to APIM: allow-list, credential only APIM holds | Covered | `authentication-certificate`, `authentication-managed-identity`, `authentication-basic`, backend credentials, mTLS in the [security lab](../examples/apim-security/README.md) |
| 12 | Backend load balancer: round robin, priority, weighted | Covered | `type: pool` backends ([CAPABILITY-MATRIX.md](CAPABILITY-MATRIX.md#backends)) |
| 12 | Circuit breaker | Covered | Pool member breakers |
| 12 | `retry` policy: try backend 1, then backend 2 | Adapted | Added in this pass: [RETRY-POLICY.md](RETRY-POLICY.md) |
| 12 | Route by `context.Deployment.Region` to the regional backend | Covered | [Regional gateways](../examples/regional-gateways/README.md) runs Microsoft's pattern end to end |
| 13 | Callouts with token caching, traffic mirroring, Event Hubs | Covered | `send-request`, `cache-store-value`, `send-one-way-request`, `log-to-eventhub` |
| 13 | Policy categories: rate limits, auth, validation, routing, caching, transformation, cross-domain | Covered | Rate/quota (by key), `validate-*`, `cors`, `cross-domain`, `cache-*`, `set-*`, `find-and-replace` |
| 13 | `json-to-xml`, `xml-to-json`, `xsl-transform` | Gap | Not implemented |
| 13 | `choose` and C# policy expressions, dynamic rate limiting | Covered | `@(...)`/`@{...}` evaluator; `rate-limit-by-key` with expressions |
| 13 | Copilot authoring | Out of scope | Editor feature |
| 14 | Token limits and PTU-to-pay-as-you-go spillover | Covered | `llm-token-limit`, `llm-emit-token-metric`, priority backend pools ([AI-GATEWAY.md](AI-GATEWAY.md)) |
| 14 | Model chaining (small model, then large) | Covered | `send-request` and `set-body` in policy |
| 14 | Semantic caching | Covered by sibling | AI Foundry simulator behind `make up-ai-foundry` ([ADR 0001](adr/0001-goldilocks-ai-gateway-scope.md)) |
| 15 | Credential mapping: OAuth in, key, certificate or token out | Covered | `validate-jwt` with Keycloak, then `set-header`, managed identity or certificates to the backend |
| 15 | Credential manager token renewal (`get-authorization-context`) | Gap | OAuth credential management is a candidate in [apim-coverage](apim-coverage/README.md) |
| 16 | Self-hosted gateway: pulls configuration, keeps the data path local, per-API gateway assignment | Adapted, mismatched | [Management gateway mode](MANAGEMENT-GATEWAY.md) proxies the data path to the serving instance, which is the opposite of what the video stresses. Workspace gateways do model per-API gateway assignment |
| 17 | Public or private inbound; App Gateway and WAF in front | Covered | `make up-private`; edge HTTP/TLS stacks; WAF and request limits in the security lab |
| 18 | Workspaces with their own gateway; federated model | Covered | [WORKSPACES.md](WORKSPACES.md) |
| 19 | Developer portal, customise then publish | Covered | `/apim/portal` with draft/preview/publish customisation |
| 20 | Import/export configuration between tiers | Covered | APIOps extract/apply, OpenAPI and Postman export |
| 20 | API Center inventory and linting | Covered | Local API Center catalogue ([CAPABILITY-MATRIX.md](CAPABILITY-MATRIX.md)) |
| 21 | Application Insights, Event Hubs, prompt/response capture | Adapted | OTEL/LGTM stacks, loggers and diagnostics, `log-to-eventhub`, local monitoring |

## What changed in this pass

- The `retry` policy, including retry around `forward-request` with backend
  switching ([RETRY-POLICY.md](RETRY-POLICY.md), contract `POLICY-RETRY`).
- The [regional gateways example](../examples/regional-gateways/README.md):
  two regional gateways, a global URL with failover, `context.Deployment.Region`
  routing and `retry` failover between regional backends. It was verified
  through published ports on slot 7 with `make verify`.

## Remaining gaps worth doing

1. A self-hosted gateway that pulls its configuration from the managed
   instance and serves locally, with APIs assigned to it. This is the largest
   difference from what the video teaches.
2. `json-to-xml` and `xml-to-json`, for SOAP and legacy XML backends.
3. `limit-concurrency` and `wait` (already listed as next in
   [apim-coverage](apim-coverage/README.md)).
4. Credential manager (`get-authorization-context`) against the local Keycloak.
