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
| Operations | `apis.<id>.operations` plus `/apim/management/operations` |
| Products | `products` plus `/apim/management/products` |
| Subscriptions | `subscription.subscriptions` plus `/apim/management/subscriptions` |
| Backends | `backends` plus `/apim/management/backends` |
| Named values | `named_values` plus `/apim/management/named-values` |
| API version sets | `api_version_sets` plus `/apim/management/api-version-sets` |
| Policy fragments | `policy_fragments` plus `/apim/management/policy-fragments` |
| Users | `users` plus `/apim/management/users` |
| Groups | `groups` plus `/apim/management/groups` |
| Policies | `/apim/management/policies/{scope_type}/{scope_name}` |
| Diagnostics and traces | `/apim/management/traces` and `/apim/trace/{id}` |

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
- `apis.<id>.operations`
- `products`
- `subscription.subscriptions`
- `backends`
- `named_values`
- `api_version_sets`
- `policy_fragments`

The gateway still materializes route matches internally so existing request
handling and JWT/subscription enforcement continue to work.

## Resource CRUD Surface

These families support local write operations through the management API:

- APIs
- operations
- products
- subscriptions
- backends
- named values
- policy fragments
- policies

These families are read-only in this phase:

- service
- API version sets
- users
- groups
- traces

## Recommended Workflow

1. Author or import APIs and operations into config.
2. Attach products and subscriptions.
3. Add backends, named values, and policy fragments.
4. Verify behavior with `/apim/management/summary`, replay, and traces.
5. Use OTEL plus Grafana when you need cross-service diagnostics.
