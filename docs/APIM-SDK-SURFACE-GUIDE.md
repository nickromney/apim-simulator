# APIM SDK Surface Guide

This repo now aligns its local management surface with the main Azure API
Management resource families exposed by the current .NET management SDK, while
still behaving like a local simulator rather than an ARM endpoint.

Use this guide when you are reading Azure SDK or ARM docs and want to know
which local concept to touch first.

## Core Mapping

| Azure APIM resource family | Local simulator shape |
| --- | --- |
| Service | `service` metadata in config plus `/apim/management/service` |
| APIs | `apis` map in config plus `/apim/management/apis` |
| API schemas | `apis.<id>.schemas` plus `/apim/management/apis/{api_id}/schemas` |
| API revisions | `apis.<id>.revisions` plus `/apim/management/apis/{api_id}/revisions` |
| API releases | `apis.<id>.releases` plus `/apim/management/apis/{api_id}/releases` |
| Operations | `apis.<id>.operations` plus `/apim/management/operations` |
| Products | `products` plus `/apim/management/products` |
| Product-group links | `products.<id>.groups` plus `/apim/management/products/{product_id}/groups` |
| Tags | `tags` plus `/apim/management/tags` |
| Subscriptions | `subscription.subscriptions` plus `/apim/management/subscriptions` |
| Backends | `backends` plus `/apim/management/backends` |
| Named values | `named_values` plus `/apim/management/named-values` |
| Loggers | `loggers` plus `/apim/management/loggers` |
| Diagnostics | `diagnostics` plus `/apim/management/diagnostics` |
| API version sets | `api_version_sets` plus `/apim/management/api-version-sets` |
| Policy fragments | `policy_fragments` plus `/apim/management/policy-fragments` |
| Users | `users` plus `/apim/management/users` |
| Groups | `groups` plus `/apim/management/groups` |
| Group-user links | `groups.<id>.users` plus `/apim/management/groups/{group_id}/users` |
| Policies | `/apim/management/policies/{scope_type}/{scope_name}` |
| Traces | `/apim/management/traces` and `/apim/trace/{id}` |

## What Is Intentionally Different

- Resource IDs are stable local IDs such as `service/apim-simulator/apis/hello`.
- Writes are synchronous config updates plus reload, not ARM async operations.
- Tenant key auth protects the local management API. ARM auth is out of scope.
- The simulator exposes the resource families developers use most often, not
  the full APIM control plane.

## Authoring Model

Prefer authoring new configs with:

- `service`
- `apis`
- `apis.<id>.schemas`
- `apis.<id>.revisions`
- `apis.<id>.releases`
- `apis.<id>.operations`
- `products`
- `products.<id>.groups`
- `users`
- `groups`
- `groups.<id>.users`
- `tags`
- `subscription.subscriptions`
- `backends`
- `named_values`
- `loggers`
- `diagnostics`
- `api_version_sets`
- `policy_fragments`

The gateway still materializes route matches internally so existing request
handling and JWT/subscription enforcement continue to work.

## Resource CRUD Surface

These families support local write operations through the management API:

- APIs
- operations
- products
- users
- groups
- group-user links
- tags
- subscriptions
- backends
- named values
- policy fragments
- policies

These families are read-only in this phase:

- service
- API schemas
- API revisions
- API releases
- loggers
- diagnostics
- API version sets
- traces

Imported operations can also carry descriptive metadata such as template
parameters plus request/response shapes. Those fields are surfaced through the
operation management endpoints but do not drive runtime request validation.

Users and group membership are still intentionally local-first. Passwords,
invites, and broader APIM identity flows are not enforced.

Loggers and diagnostics are intentionally inspection-only too. Their sink,
sampling, and capture settings are preserved for learning, but actual runtime
observability still comes from local traces plus OTEL/Grafana.

## Recommended Workflow

1. Author or import APIs and operations into config.
2. Attach products and subscriptions.
3. Add backends, named values, and policy fragments.
4. Verify behavior with `/apim/management/summary`, replay, and traces.
5. Use OTEL plus Grafana when you need cross-service diagnostics.
