# Scope

This repository is not trying to clone all of Azure API Management. It is a local learning and iteration tool with a deliberate bias toward gateway behavior, policy experimentation, and auth/networking scenarios.

## Supported Now

- Config-driven gateway routing and upstream proxying
- Products, subscriptions, subscription key rotation, and tenant access keys
- OIDC and JWT validation through static JWKS or JWKS endpoints
- Route-level scope, role, and claim checks
- Host matching, API version set routing, and forwarded-header-aware tracing
- A limited but useful XML policy subset:
  - `set-header`
  - `rewrite-uri`
  - `return-response`
  - `choose`
  - `check-header`
  - `ip-filter`
  - `cors`
  - `rate-limit`
  - `quota`
  - `set-variable`
  - `set-query-parameter`
  - `set-body`
  - `include-fragment`
  - `validate-jwt`
  - `set-backend-service`
  - `send-request`
- Config reload, trace capture, trace summaries, replay, Terraform/OpenTofu import, and static compatibility reporting
- Curated Azure-Samples/APIM sample compatibility fixtures with documented supported, adapted, and unsupported cases
- Compose-backed direct public, edge HTTP, edge TLS, private/internal, OIDC, and MCP scenarios
- A focused operator console UI for policies, traces, products, routes, and subscriptions

## Currently Deferred

- External cache backends for `cache-*`
- `quota-by-key` bandwidth enforcement
- Full APIM policy expression compatibility
- Broader management-plane emulation
- Broader Azure-Samples/APIM fixture coverage beyond the curated set

## Not The Goal

- Full APIM parity across every SKU and management-plane feature
- A complete implementation of the APIM policy language and expression engine
- The full Microsoft developer portal CMS, email, or notification surface
- Production deployment guidance
