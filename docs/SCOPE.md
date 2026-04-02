# Scope

This repository is not trying to clone all of Azure API Management. It is a local learning and iteration tool with a deliberate bias toward gateway behavior, policy experimentation, and auth/networking scenarios.

## Supported Now

- Config-driven gateway routing and upstream proxying
- Products, subscriptions, subscription key rotation, and tenant access keys
- OIDC and JWT validation through static JWKS or JWKS endpoints
- Route-level scope, role, and claim checks
- Host matching and API version set routing
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
- Config reload, trace capture, and `tofu show -json` import
- Compose-backed example stacks for OIDC and MCP streamable HTTP scenarios

## Planned Next

- Better sample compatibility coverage for Azure-Samples/Apim-Samples
- More policy primitives such as `set-variable`, `set-query-parameter`, `set-body`, and fragment inclusion
- Docker overlays for TLS and App-Gateway-like reverse proxy behavior
- A focused developer console UI for policies, traces, products, and subscriptions

## Not The Goal

- Full APIM parity across every SKU and management-plane feature
- A complete implementation of the APIM policy language and expression engine
- The full Microsoft developer portal CMS, email, or notification surface
- Production deployment guidance
