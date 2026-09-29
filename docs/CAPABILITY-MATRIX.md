# APIM Simulator Capability Matrix

This document maps simulator features to Azure APIM concepts and their Terraform resource equivalents.

The management surface below is available when `tenant_access.enabled` is `true`.

## Legend

| Status | Meaning |
|--------|---------|
| Yes | Fully implemented |
| Partial | Basic support, not all options |
| No | Not implemented |
| N/A | Not applicable to simulator |

## Gateway / Service Level

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Health endpoint | Yes | N/A | `/apim/health` |
| Startup probe | Yes | N/A | `/apim/startup` |
| Gateway error envelope | Yes | N/A | Public API routes return `{"statusCode": <int>, "message": "<text>"}` with `application/json`; `/apim/management`, `/apim/portal`, and health/probe routes retain their local FastAPI response shapes |
| Config reload | Yes | N/A | `/apim/reload` + file watcher |
| CORS | Yes | `azurerm_api_management` | Gateway API CORS comes only from the `cors` policy; `allowed_origins` in config now only covers the simulator's own `/apim/*` endpoints |
| Public network access | Partial | `azurerm_api_management.public_network_access_enabled` / AzAPI `properties.publicNetworkAccess` | Imported into service metadata only; local reachability is not enforced |
| Client cert (mTLS) | Yes | `azurerm_api_management.client_certificate_enabled` | `client_certificate.mode` |
| Negotiate client cert | Yes | `azurerm_api_management.hostname_configuration.negotiate_client_certificate` | Via proxy headers |
| SKU selection | N/A | `azurerm_api_management.sku_name` | Simulator is single-instance |
| Zones / HA | N/A | `azurerm_api_management.zones` | Not applicable |
| Virtual network type | Partial | `azurerm_api_management.virtual_network_type` / AzAPI `properties.virtualNetworkType` | Imported into service metadata only; use docker/k8s networking for actual topology |
| Gateway error responses | Yes | N/A | Errors raised by the gateway itself use APIM's `{"statusCode","message"}` body. Missing and invalid subscription keys, keys for inactive subscriptions, and keys that don't cover the API all return 401 with APIM's messages and a `WWW-Authenticate: AzureApiManagementKey` challenge. An unreachable backend returns `500 Internal server error` |
| Backend request headers | Yes | N/A | The backend gets its own `Host` and an `X-Forwarded-For` with the client address appended. The subscription key is forwarded, as in APIM. Simulator identity headers (`x-apim-user-*`, `x-ms-client-principal*`, `x-user-*`, `x-apim-products`) are sent only with `inject_simulator_identity_headers: true` |
| Simulator response headers | Adapted | N/A | `x-apim-simulator` and `x-correlation-id` are sent only with `emit_simulator_response_headers` / `propagate_simulator_correlation_id`; APIM sends neither |
| Custom domains / hostnames | Partial | `azurerm_api_management.hostname_configuration` / AzAPI `properties.hostnameConfigurations` | Imported as service hostname metadata; TLS termination remains external |

## Runtime Scenarios

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Direct public compose path | Yes | N/A | [`compose.yml`](../compose.yml) + [`compose.public.yml`](../compose.public.yml) on `localhost:8000` |
| Edge HTTP compose path | Yes | N/A | [`compose.yml`](../compose.yml) + [`compose.edge.yml`](../compose.edge.yml) on `edge.apim.127.0.0.1.sslip.io:8088` |
| Edge TLS compose path | Yes | N/A | [`compose.yml`](../compose.yml) + [`compose.edge.yml`](../compose.edge.yml) + [`compose.tls.yml`](../compose.tls.yml) on `edge.apim.127.0.0.1.sslip.io:9443` |
| Private internal compose path | Yes | N/A | [`compose.yml`](../compose.yml) + [`compose.private.yml`](../compose.private.yml); smoke uses internal probe container |

## APIs and Operations

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| API definition | Yes | `azurerm_api_management_api` | `apis` map in config |
| API revision metadata | Partial | `azurerm_api_management_api.revision` / `revision_description` | Imported and exposed read-only; multiple APIM revisions collapse into one active local API for runtime behaviour |
| API releases | Partial | `azurerm_api_management_api_release` | Imported and exposed read-only via `/apim/management/apis/{api_id}/releases` |
| Operations | Yes | `azurerm_api_management_api_operation` | `operations` within API |
| API schemas | Partial | `azurerm_api_management_api_schema` | Imported and exposed read-only via `/apim/management/apis/{api_id}/schemas`; write endpoints are not implemented |
| Operation descriptions and template params | Yes | `azurerm_api_management_api_operation` | Imported from operation metadata blocks and projected through management APIs |
| Operation request metadata | Partial | `azurerm_api_management_api_operation.request` | Imported and projected through management APIs, and can be authored on operation PUT; request validation is not enforced at runtime |
| Operation response metadata | Partial | `azurerm_api_management_api_operation.response` | Imported and projected through management APIs, and can be authored on operation PUT; runtime uses them for `mock-response` examples but not full schema enforcement |
| Path routing | Yes | - | Operations match their full URL template segment by segment: `{param}` takes one segment, `{*param}` and `/*` take the rest, query-string template parts must match, literal segments beat parameters, and the API URL suffix matches case-insensitively. An unmatched request is `404 {"statusCode":404,"message":"Resource not found"}`. An API with no operations serves nothing, as in APIM. Tie-breaking between equally specific templates is not documented by Microsoft; declaration order decides. Routes declared directly under `routes` (outside `apis`) keep simulator prefix matching |
| Method routing | Yes | - | Per-operation `method` |
| Template parameters | Yes | - | Matched values are exposed as `context.Request.MatchedParameters` |
| API Version Sets | Yes | `azurerm_api_management_api_version_set` | Header/Query/Segment schemes; a version identifier is required, except an API with `api_version: null` models APIM's unversioned Original API. No simulator-only default-version fallback |
| API protocols | Yes | `azurerm_api_management_api.protocols` | `ApiConfig.protocols` accepts `http`/`https`; the request scheme uses the first `X-Forwarded-Proto` value when present, and a disallowed scheme returns the existing 404 Resource not found envelope because Learn documents the property but not the rejection response |
| OpenAPI import | Partial | `azurerm_api_management_api` (import block) | Supports inline/link OpenAPI and Swagger JSON import through Terraform/OpenTofu and `/apim/management/apis/{api_id}/import`; full schema/request/response extraction is narrower than explicit APIM resources |
| GraphQL | No | `azurerm_api_management_api` | Not implemented |
| WebSocket | No | `azurerm_api_management_api` | Not implemented |

## Products and Subscriptions

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Products | Yes | `azurerm_api_management_product` | `products` map |
| Product-API association | Yes | `azurerm_api_management_product_api` | `products` list on route/API |
| Product-group association | Yes | `azurerm_api_management_product_group` | Descriptive link resources under `/apim/management/products/{product_id}/groups` |
| Product publish state | Adapted | `azurerm_api_management_product.published` | `state`: `published`, `not_published`; the simulator rejects product-context access whose only configured products are unpublished with an enveloped 403. API-, all-APIs-, and service-scoped subscriptions do not select product context and are not blocked by product publication. APIM documents that unpublishing hides a product from the developer portal but does not invalidate existing keys or product-context access. Config-authored products default to `published` |
| Subscriptions | Yes | `azurerm_api_management_subscription` | `subscription.subscriptions`; product scope remains the `products` list, API scope uses `api_id`, all-APIs scope uses `all_apis`, and the service-scoped all-access form uses explicit `service_scoped: true` (never enabled by default). Terraform import maps `api_id` and `product_id`; when neither is present it imports all-APIs scope. Unknown or conflicting scope fields are rejected. |
| Primary/secondary keys | Yes | - | `keys.primary`, `keys.secondary` |
| Subscription state | Yes | `azurerm_api_management_subscription.state` | `active`, `suspended`, `cancelled`, `submitted`, `rejected`, `expired`; only `active` keys authenticate, and inactive keys return APIM's 401 invalid-key envelope |
| Subscription key names and forwarding | Yes | `azurerm_api_management_api.subscription_key_parameter_names` | Defaults are `Ocp-Apim-Subscription-Key` and `subscription-key`; keys are forwarded to backends by default and can be removed by inbound policy |
| Subscription scope policy context | Yes | `azurerm_api_management_subscription.api_id` / `product_id` | API-, all-APIs-, and service-scoped keys authorize without a product and do not apply product-scope policies; `context.Subscription` still identifies the accepted subscription. |
| Key rotation | Yes | - | `/apim/management/subscriptions/{id}/rotate` |
| Require subscription | Yes | `azurerm_api_management_product.subscription_required` | Per-product toggle. When an API is in an open product, a key that can't be accepted is ignored and no key is needed; the request is served in the open product's context (a valid key still supplies the subscription context). The key header wins over the query parameter even when empty; an empty value counts as a missing key. See [subscriptions](https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions) |
| Subscription bypass | Yes | - | Header conditions |
| Approval required | Yes | `azurerm_api_management_product.approval_required` | `approval_required` on products; pending subscriptions stay `submitted` until approved |
| Subscription limits | Partial | `azurerm_api_management_product.subscriptions_limit` | Enforced at portal sign-up (409 when reached; 0 disables self-serve) |

## Tags

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Tags | Yes | `azurerm_api_management_tag` | Global tag registry plus `/apim/management/tags` |
| API tags | Yes | `azurerm_api_management_api_tag` | Descriptive link resources under `/apim/management/apis/{api_id}/tags` |
| Product tags | Yes | `azurerm_api_management_product_tag` | Descriptive link resources under `/apim/management/products/{product_id}/tags` |
| Operation tags | Partial | `azurerm_api_management_api_operation_tag` | Imported and exposed through operation tag links; operation tag creation is adapted into the simulator's global tag registry |
| Tag descriptions | No | `azurerm_api_management_api_tag_description` | Not implemented |

## Users and Groups

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Users | Partial | `azurerm_api_management_user` | Local CRUD + Terraform import; descriptive only, no auth or password enforcement |
| Groups | Partial | `azurerm_api_management_group` | Local CRUD + Terraform import; still descriptive only |
| Group membership | Partial | `azurerm_api_management_group_user` | Terraform import plus descriptive link CRUD under `/apim/management/groups/{group_id}/users` |
| Built-in groups | No | - | Administrators/Developers/Guests |

## Authentication / Authorization

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| OIDC/JWT validation | Yes | `azurerm_api_management.sign_in` / policies | Multi-issuer support |
| JWKS fetching | Yes | - | Via `jwks_uri` |
| Static JWKS | Yes | - | Inline `jwks` in config |
| Audience validation | Yes | - | Per OIDC provider |
| Issuer validation | Yes | - | Auto-selects by token `iss` |
| Scope enforcement | Yes | - | `authz.required_scopes` |
| Role enforcement | Yes | - | `authz.required_roles` |
| Claim enforcement | Yes | - | `authz.required_claims` |
| OAuth2 authorization server | No | `azurerm_api_management_authorization_server` | Use external IdP |
| OpenID Connect provider | Partial | `azurerm_api_management_openid_connect_provider` | Via `oidc_providers` |
| Identity provider | No | `azurerm_api_management_identity_provider_*` | Use external IdP |

## Policies

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Inbound policies | Yes | `azurerm_api_management_api_policy` | XML format |
| Policy expressions | Partial | - | C#-style single statements and a focused multi-statement subset (`var`/explicit local declarations, assignment, `if`/`else`, `return`), ternaries, supported request/response/variable context members, selected string/dictionary members, interpolation, and `JObject` body conversion. Unsupported C# 7 syntax and .NET members remain unimplemented. Microsoft documents runtime exceptions for expression failures but is silent on the exact invalid-JSON message; the simulator reports `The body is not valid JSON.` See [policy expressions](https://learn.microsoft.com/en-us/azure/api-management/api-management-policy-expressions), [conditional operator](https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/operators/conditional-operator), and [arithmetic operators](https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/operators/arithmetic-operators). |
| Outbound policies | Yes | - | `<outbound>` section |
| On-error policies | Partial | - | Inbound and backend policy errors, outbound exceptions, expression failures, backend connection failures and `fail-on-error-status-code` jump to `on-error` with `context.LastError` (`Source`, `Reason`, `Message`, `Scope`, `Section`) and the error status in `context.Response.StatusCode`. Refusals from `rate-limit`, `quota`, `ip-filter`, `check-header` and `validate-jwt` count as errors. Without a `return-response`, the caller gets the error's own response plus headers set in `on-error`. Not modelled: `LastError.Path` and `PolicyId`; refusal `Reason` is inferred from the message; outbound refusal responses do not enter `on-error`. See [error handling](https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies) |
| Policy inheritance | Yes | - | Per-section global -> product -> API -> operation; each child `<base />` is replaced in place by the parent section, while omitting it suppresses that parent section. A section that is present without `<base />` (including an empty one such as `<backend />`) drops the parent section. A section omitted from the document inherits the parent as if it held `<base />`: Learn says `base` is included by default in each section but does not state the omitted case, so this is inferred. A scope with no document is skipped. Workspace scope is not modeled; product selection is adapted (see ADR 0003). Based on [policy scopes](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-policies) and [`base`](https://learn.microsoft.com/en-us/azure/api-management/set-edit-policies). |
| `set-header` | Yes | - | Add/override/delete modes |
| `rewrite-uri` | Yes | - | Path rewriting |
| `set-variable` | Yes | - | Writes to request-scoped `variables` |
| `set-query-parameter` | Yes | - | Mutates outbound upstream query only |
| `set-body` | Yes | - | Literal text is preserved verbatim; request/response data uses APIM policy expressions. The request body is changed in inbound/backend and the response body in outbound ([set-body](https://learn.microsoft.com/en-us/azure/api-management/set-body-policy)) |
| `include-fragment` | Yes | - | Config-backed via `policy_fragments` |
| `return-response` | Yes | - | All children are optional (default `200` with an empty body); supports document-order `set-status`, `set-header`, and `set-body`, and can start from a `response-variable-name` response object. `set-status` reason is carried in the local `ResponseSpec`; ASGI has no reason-phrase field, so the live HTTP response uses the server's standard phrase. The legacy simulator-only `<body>` child remains accepted. ([return-response](https://learn.microsoft.com/en-us/azure/api-management/return-response-policy), [set-status](https://learn.microsoft.com/en-us/azure/api-management/set-status-policy)) |
| `choose`/`when`/`otherwise` | Yes | - | Conditional logic |
| `check-header` | Yes | - | Requires the header; with `<value>` children, any one match passes (`ignore-case` selects case-insensitive compare); no children means presence only. `name`, `failed-check-httpcode`, `failed-check-error-message` and `ignore-case` are all required and may be expressions; the non-APIM `value` attribute is rejected. Failure: the configured status with `{"statusCode":N,"message":"<failed-check-error-message>"}` as `application/json`. The docs are silent on the body envelope (it follows the other policies) and on how a repeated header is compared (compared as the gateway received it). See [check-header](https://learn.microsoft.com/en-us/azure/api-management/check-header-policy) |
| `ip-filter` | Yes | - | `action` (required, `allow`\|`forbid`, may be an expression) with `<address>` (single IPv4/IPv6 address) and inclusive IPv4/IPv6 `<address-range from to>`; at least one is required, and the non-APIM `<cidr>` element and CIDR text in `<address>` are rejected. Fails closed with `403` when the caller address is missing or unparseable (`Failed to establish IP address for the caller. Access denied.`); otherwise `Caller IP address {ip} is not allowed. Access denied.` (allow, no match) or `Caller IP address is blocked. Access denied.` (forbid, match), as `{"statusCode":403,"message":...}` JSON. Status and body are not stated in the ip-filter docs; messages come from the [predefined policy errors](https://learn.microsoft.com/en-us/azure/api-management/api-management-error-handling-policies). See [ip-filter](https://learn.microsoft.com/en-us/azure/api-management/ip-filter-policy) |
| `cors` | Yes | - | Origins (incl. `*`), methods, headers, expose-headers, `allow-credentials`, `preflight-result-max-age`, `terminate-unmatched-request` (default `true`; the docs contradict themselves), preflight answered from the policy before auth/backend, bypassed when an operation defines `OPTIONS`; `allowed-headers` not enforced as required; requested method/headers not checked on preflight (docs silent); product-scope `cors` is not consulted on preflight (APIM consults it when the key is in the query string) |
| `rate-limit` | Yes | - | Sliding window per subscription and per policy scope; skipped without a subscription key. Over the limit: `429` with `{"statusCode":429,"message":"Rate limit is exceeded. Try again in N seconds."}` and `Retry-After`. Supports `retry-after-header-name`, `retry-after-variable-name`, `remaining-calls-header-name`, `remaining-calls-variable-name`, `total-calls-header-name`, and nested `<api>`/`<operation>` limits. `renewal-period` is capped at 300 s. Implements the classic tiers' sliding window, not the v2 token bucket. See [rate-limit](https://learn.microsoft.com/en-us/azure/api-management/rate-limit-policy) |
| `rate-limit-by-key` | Yes | - | One sliding-window counter per `counter-key` across all scopes. Expression-valued `increment-condition`/`increment-count` are evaluated after the response, so the 429 lands one call later, as documented. Same 429 body and header attributes as `rate-limit`. See [rate-limit-by-key](https://learn.microsoft.com/en-us/azure/api-management/rate-limit-by-key-policy) |
| `quota` | Yes | - | `calls` and/or `bandwidth` (KB of request plus response body) per subscription and per policy scope; skipped without a subscription key. `renewal-period="0"` never renews. Over quota: `403` with `{"statusCode":403,"message":"Out of call volume quota. Quota will be replenished in hh:mm:ss."}` (or `Out of bandwidth quota`) and `Retry-After`. Adapted: APIM anchors periods to the subscription's start date, which the local model doesn't store, so periods start at the first counted call. See [quota](https://learn.microsoft.com/en-us/azure/api-management/quota-policy) |
| `quota-by-key` | Partial | - | Call quotas per `counter-key`, anchored at `first-period-start`, minimum period 300 s, incremented once per request even when several policies share a key. Same 403 body as `quota`. `bandwidth` is rejected as unsupported. See [quota-by-key](https://learn.microsoft.com/en-us/azure/api-management/quota-by-key-policy) |
| `validate-jwt` | Partial | - | OpenID config (one-hour process cache), inline Base64 symmetric keys, RSA n/e keys, supported PS256/RS256/RS512/ES256 and HMAC validation, audiences, issuers, required claims, output token variables, clock skew, and unsigned tokens only when explicitly enabled. JWE `decryption-keys` are rejected at policy parse time because the simulator has no APIM certificate/private-key store. See [validate-jwt](https://learn.microsoft.com/en-us/azure/api-management/validate-jwt-policy) |
| `authentication-basic` | Partial | - | Inbound policy sets and replaces `Authorization: Basic ...`; backend `auth_type: basic` also replaces caller authorization. See [authentication-basic](https://learn.microsoft.com/en-us/azure/api-management/authentication-basic-policy) |
| `authentication-certificate` | Partial | - | Policy and backend configuration are parsed; the local gateway uses simulator certificate marker headers because it has no APIM certificate store or mTLS client-certificate transport. See [authentication-certificate](https://learn.microsoft.com/en-us/azure/api-management/authentication-certificate-policy) |
| `authentication-managed-identity` | Partial | - | Sets `Authorization: Bearer <token>` and honors `resource`, static `client-id`, `output-token-variable-name`, and `ignore-error`. The token is a deterministic opaque simulator token, not a Microsoft Entra token. Nested `send-request` and backend managed identity use the same adaptation. See [authentication-managed-identity](https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy) |
| `forward-request` | Partial | - | Applies `timeout` (default 300 s), `timeout-ms`, `follow-redirects`, `buffer-request-body`, `buffer-response` and `fail-on-error-status-code`. The effective backend section controls whether the backend is called. A config with no global policy document uses APIM's default global `<forward-request />`; a configured backend section without forwarding returns a simulator/APIM-consistent empty `200` response unless another policy supplies a response. The ordinary no-forward response is not specified by Learn. See [forward-request](https://learn.microsoft.com/en-us/azure/api-management/forward-request-policy) and [return-response](https://learn.microsoft.com/en-us/azure/api-management/return-response-policy) |
| `set-backend-service` | Yes | - | Supports `backend-id` and `base-url` overrides in inbound/backend. Service Fabric `sf-*` attributes are rejected at parse time because Service Fabric resolution is not implemented. See [set-backend-service](https://learn.microsoft.com/en-us/azure/api-management/set-backend-service-policy) |
| `cache-lookup` | Partial | - | Supports local internal cache; `prefer-external` is adapted to local cache and `external` is unsupported. The documented `vary-by-developer` and `vary-by-developer-groups` attributes are required; developer variation uses the subscription owner and group variation uses that owner's groups. Only GET is eligible, and requests with `Authorization` are skipped unless `allow-private-response-caching` is true. `downstream-caching-type` is limited to `none`, `private`, or `public`, with `must-revalidate` applied as documented. Microsoft documents GET-only eligibility but is silent on GET request bodies; the simulator follows the method check without an extra body exclusion. |
| `cache-store` | Partial | - | Supports local internal response cache for GET responses. The default stores only `200 OK`; `cache-response="true"` stores other response statuses too. `duration` is interpreted as seconds. |
| `cache-lookup-value` | Partial | - | Supports local internal value cache plus default-value; `prefer-external` is adapted and `external` is unsupported |
| `cache-store-value` | Partial | - | Stores to local in-memory value cache; `prefer-external` is adapted and `external` is unsupported. APIM stores asynchronously; the local adaptation writes synchronously without emulating latency. |
| `cache-remove-value` | Partial | - | Removes from local in-memory value cache; `prefer-external` is adapted and `external` is unsupported. Supports `fail-on-cache-removal-error`; the local dictionary has no removal failure path, so this option cannot fire locally. |
| `mock-response` | Partial | - | Valid in inbound and outbound (outbound replaces the response). Supports `status-code` and `content-type`, returning the first matching authored response example for the current operation |
| `send-request` | Partial | - | Supports `new\|copy`, headers/body, timeout, ignore-error, evaluated response-variable names, managed identity, and certificate placeholder. `mode=new` requires `set-url` and `set-method`; outbound `mode=copy` does not copy the request body. The documented `<proxy>` child is rejected at parse time because the shared local HTTP client cannot apply per-policy proxies. Managed identity uses a deterministic opaque local token, not a real Microsoft Entra token. See [send-request](https://learn.microsoft.com/en-us/azure/api-management/send-request-policy) |
| `llm-token-limit` | Partial | - | Adapted: sliding-minute and quota-period windows, estimate/actual usage counting, Vertex `usageMetadata` plus OpenAI/Anthropic usage parsing, APIM JSON error envelopes with `Retry-After`, and retry/remaining headers. Local remaining-quota values are exact counters while Azure documents them as estimates; see [AI-GATEWAY.md](AI-GATEWAY.md) |
| `azure-openai-token-limit` | Partial | - | Alias of `llm-token-limit` |
| `llm-emit-token-metric` | Partial | - | Adapted: emits OTEL counter `apim.llm.tokens` with the documented `API Management` default namespace, evaluated dimension names, documented default dimensions, and at most five configured dimensions instead of Application Insights metrics |
| `azure-openai-emit-token-metric` | Partial | - | Alias of `llm-emit-token-metric` |
| `llm-semantic-cache-lookup`/`-store` | No | - | Policy not implemented; the sibling AI Foundry simulator provides service-side semantic caching behind `make up-ai-foundry` — see ADR 0003 and [AI-GATEWAY.md](AI-GATEWAY.md) |
| `llm-content-safety` | No | - | Policy not implemented; the sibling AI Foundry simulator serves the Content Safety API behind `make up-ai-foundry` — see ADR 0003 and [AI-GATEWAY.md](AI-GATEWAY.md) |
| `emit-metric` | Partial | - | Adapted: emits the OTEL counter `apim.policy.metric` with double values (including zero), the documented `API Management` default namespace, evaluated dimension names, documented default dimensions, and at most five configured dimensions |
| `validate-content` | Partial | - | Size (refused with 400 in inbound and 502 in outbound, per the validation error table), content-type map, optional content type with JSON well-formedness, and APIM-shaped structured validation errors for request/response bodies; JSON/XML/SOAP schema enforcement and schema override attributes are deferred/rejected |
| `validate-parameters` | Partial | - | Required/unspecified headers, query, and operation-template path parameters with per-parameter overrides; value/schema enforcement is deferred |
| `validate-headers` | Partial | - | Response required/unspecified header checks against operation response metadata with per-header overrides; header value/schema enforcement is deferred |
| `validate-status-code` | Partial | - | Declared operation responses are always valid (a per-code override does not apply to them), then explicit codes, then the unspecified action; `prevent` returns 502 to the client and to the cache |
| `log-to-eventhub` | No | - | Use observability stack |

## Backends

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Backend definitions | Yes | `azurerm_api_management_backend` | `backends` map incl. credentials import |
| Backend URL | Yes | - | `url` field |
| Basic auth | Yes | - | `auth_type: basic`; backend credentials replace a caller's `Authorization` header, matching [authentication-basic](https://learn.microsoft.com/en-us/azure/api-management/authentication-basic-policy) |
| Client cert auth | Partial | - | `auth_type: client_certificate`; local marker headers stand in for APIM's certificate transport |
| Managed identity | Partial | - | `auth_type: managed_identity`; sends `Authorization: Bearer <token>` using a deterministic opaque local token, not a real Microsoft Entra token. See [authentication-managed-identity](https://learn.microsoft.com/en-us/azure/api-management/authentication-managed-identity-policy) |
| Circuit breaker | Partial | - | Adapted per-member breaker on pool backends with `failureCondition.count`, `interval`, `statusCodeRanges`, `errorReasons`, `tripDuration`, and `acceptRetryAfter`; legacy `error_statuses` remains supported. Retry statuses outside the failure condition do not trip a breaker. See [backends](https://learn.microsoft.com/en-us/azure/api-management/backends) and ADR 0003 |
| Load balancing | Partial | - | `type: pool` backends with deterministic weighted round-robin, priority failover, and cookie-based `session_affinity`; cookie attributes are adapted because Learn documents the mechanism but not the exact emitted value/attributes |

## Management Plane

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Management status | Yes | - | `/apim/management/status` |
| Tenant access keys | Yes | `azurerm_api_management.tenant_access` | Primary/secondary |
| Management summary | Yes | - | `/apim/management/summary` |
| API CRUD | Yes | `azurerm_api_management_api` | `/apim/management/apis` |
| OpenAPI import via management API | Partial | `azurerm_api_management_api` | `/apim/management/apis/{api_id}/import`; imports operations and upstream URL, but not full schema/request/response parity |
| Operation CRUD | Yes | `azurerm_api_management_api_operation` | `/apim/management/apis/{api_id}/operations` |
| Product CRUD | Yes | `azurerm_api_management_product` | `/apim/management/products` |
| Backend CRUD | Yes | `azurerm_api_management_backend` | `/apim/management/backends` |
| Named value CRUD | Yes | `azurerm_api_management_named_value` | `/apim/management/named-values` |
| API schema inspection | Yes | `azurerm_api_management_api_schema` | `/apim/management/apis/{api_id}/schemas` and `/apim/management/apis/{api_id}/schemas/{schema_id}` |
| API revision CRUD | Partial | `azurerm_api_management_api` | `/apim/management/apis/{api_id}/revisions`; revision metadata and current-release bookkeeping are supported, but runtime revision branching remains collapsed to one active API |
| API release CRUD | Partial | `azurerm_api_management_api_release` | `/apim/management/apis/{api_id}/releases`; descriptive metadata only |
| API version set CRUD | Yes | `azurerm_api_management_api_version_set` | `/apim/management/api-version-sets` |
| Logger inspection | Yes | `azurerm_api_management_logger` | `/apim/management/loggers` and `/apim/management/loggers/{logger_id}` |
| Diagnostic inspection | Yes | `azurerm_api_management_diagnostic` | `/apim/management/diagnostics` and `/apim/management/diagnostics/{diagnostic_id}` |
| Policy fragment CRUD | Yes | - | `/apim/management/policy-fragments` |
| User CRUD | Partial | `azurerm_api_management_user` | `/apim/management/users`; password and confirmation flows remain descriptive only |
| Group CRUD | Partial | `azurerm_api_management_group` | `/apim/management/groups` |
| Group-user link CRUD | Yes | `azurerm_api_management_group_user` | `/apim/management/groups/{group_id}/users` |
| Product-group link CRUD | Yes | `azurerm_api_management_product_group` | `/apim/management/products/{product_id}/groups` |
| Tag inspection and CRUD | Yes | `azurerm_api_management_tag` | `/apim/management/tags` plus nested API/product/operation tag link endpoints |
| Policy inspection/update | Yes | - | `/apim/management/policies/{scope_type}/{scope_name}` |
| Replay | Yes | - | `/apim/management/replay` |
| Subscription CRUD | Yes | `azurerm_api_management_subscription` | List, create, update, delete, and rotate via API; payloads expose `scope`, `api_id`, `all_apis`, `service_scoped`, and existing product fields |
| Config import | Yes | - | Terraform/OpenTofu JSON import via management API and `make import-tofu` |
| Git integration | No | `azurerm_api_management.management.git_configuration_enabled` | Use GitOps |

## Observability

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Correlation ID | Yes | - | `X-Correlation-Id` header |
| Trace header | Yes | - | `X-Apim-Trace: true` |
| Trace lookup | Yes | - | `/apim/trace/{id}` |
| Trace summaries | Yes | - | `/apim/management/traces` |
| Logger resources | Partial | `azurerm_api_management_logger` | Imported and exposed read-only; sink configuration is descriptive only |
| Diagnostic resources | Partial | `azurerm_api_management_diagnostic` | Imported and exposed read-only; sampling and logger routing are descriptive only |
| Forwarded-header trace fields | Yes | - | `incoming_host`, `forwarded_host`, `forwarded_proto`, `forwarded_for`, `client_ip`, `upstream_url` |
| Policy execution trace | Yes | - | Includes policy steps, variable writes, JWT validation, send-request activity, selected backend, cache/throttle actions |
| Application Insights | No | `azurerm_api_management.application_insights` | Use external APM |
| Diagnostic logs | No | `azurerm_api_management_diagnostic` | Use container logs and OTEL for actual runtime logging |

## Named Values / Secrets

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Named values | Yes | `azurerm_api_management_named_value` | `{{name}}` references are substituted in policy attributes and text before execution; unknown references are rejected at config load, management save, and Terraform/OpenTofu import. Values are single-pass and cannot nest ([named values](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-properties)) |
| Secret values | Yes | - | Masked in traces |
| Key Vault refs | Partial | - | Imported and resolved via local env overrides (`APIM_NAMED_VALUE_*`) |

## Developer Console

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Operator console | Yes | N/A | [`ui/`](../ui/) Vite + React app |
| Policy editor | Yes | N/A | Uses management policy endpoints |
| Trace viewer | Yes | N/A | Uses trace lookup and trace summary endpoints |
| Replay console | Yes | N/A | Uses management replay endpoint |
| Subscription key inspection/rotation | Yes | N/A | Uses management subscription endpoints |
| Subscription approval queue | Yes | N/A | Approve/reject submitted subscriptions from the console |

## Developer Portal

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| Consumer catalog | Adapted | N/A | `/apim/portal` static page plus JSON endpoints; enable with `portal.enabled` |
| Product visibility by group | Adapted | `azurerm_api_management_product_group` | Products with no group links are visible to all portal users |
| Subscription sign-up | Adapted | N/A | `POST /apim/portal/subscriptions`; lands `submitted` when the product requires approval |
| Try-it console | Adapted | N/A | Calls the gateway with a chosen subscription key; no parameter substitution UI |
| Portal identity | Adapted | N/A | Acting user is a config-defined user in `X-Apim-Portal-User`; there is no sign-in |
| Portal CMS, theming, email | No | `azurerm_api_management_portal_*` | Explicitly out of scope |

## Certificates

| Feature | Simulator | Terraform Resource | Notes |
|---------|-----------|-------------------|-------|
| CA certificates | Partial | `azurerm_api_management_certificate` | Trusted cert config |
| Client certificates | Partial | - | Via proxy headers; each trusted identity requires all configured thumbprint/subject/issuer claims to match exactly, while identities are ORed. The `validate-client-certificate` policy's certificate-chain, revocation, and validity attributes are not yet implemented |
| Gateway certificates | No | `azurerm_api_management_gateway_certificate_authority` | Use TLS terminator |

## Not Planned

These features are explicitly out of scope for the simulator:

- Developer Portal CMS, theming, and content management (`azurerm_api_management_portal_*`); the adapted consumer workflows live at `/apim/portal`
- Email templates (`azurerm_api_management_email_template`)
- Notifications (`azurerm_api_management_notification_*`)
- Self-hosted gateway (`azurerm_api_management_gateway`)
- Global/workspace policies distinction
- External cache backends for `cache-*` policies
- `quota-by-key` bandwidth enforcement
- Tag descriptions
