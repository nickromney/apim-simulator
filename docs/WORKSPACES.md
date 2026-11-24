# Workspaces

Give separate teams their own slice of one simulator, in the shape of Azure API
Management
[workspaces](https://learn.microsoft.com/en-us/azure/api-management/workspaces-overview)
(Premium, Premium v2 and the other v2 tiers). A workspace owns APIs, products,
subscriptions, named values, backends and policy fragments. It has its own
policy scope, and it is served by the gateways you associate it with.

With workspaces you can:

- run a team's APIs on a dedicated workspace gateway, reached by its own host
  name, while service-level APIs stay on the managed gateway;
- add a workspace policy that runs after the global policy and before product,
  API and operation policies;
- have the simulator reject a config or management write that crosses a
  workspace boundary, as Azure would.

## Try the example

You need Docker with Compose, and port 8000 free (`make check-host-ports`).

```bash
APIM_CONFIG_SOURCE_PATH=/app/examples/workspaces/apim.json make up
```

The example, [examples/workspaces/apim.json](../examples/workspaces/apim.json),
has two workspaces and one service-level API:

| API | Owner | Served by | Host to call |
| --- | --- | --- | --- |
| `/status/echo` | Service level | Managed gateway | `localhost:8000` |
| `/payroll/echo` | `payroll` workspace | Managed gateway | `localhost:8000` |
| `/orders/echo` | `orders` workspace | `orders-gw` workspace gateway | `orders-gw.gateway.localhost:8000` |

Names under `.localhost` resolve to your own machine, so `curl` reaches the
workspace gateway without any DNS or hosts-file change.

Call the Orders API on its workspace gateway, with the Orders team's key:

```bash
curl -i http://orders-gw.gateway.localhost:8000/orders/echo \
  -H 'Ocp-Apim-Subscription-Key: orders-primary-key'
```

Expected result: `200`, with `x-apim-gateway: orders-gw` (set by the global
policy from `context.Deployment.Gateway.Id`) and `x-apim-workspace: Orders team`
(set by the Orders workspace policy from a workspace named value).

The same call on the managed gateway finds no API:

```bash
curl -i http://localhost:8000/orders/echo -H 'Ocp-Apim-Subscription-Key: orders-primary-key'
```

Expected result: `404`. And the workspace gateway does not serve the
service-level API: `curl -i http://orders-gw.gateway.localhost:8000/status/echo`
returns `404`.

The Payroll workspace shares the managed gateway:

```bash
curl -i http://localhost:8000/payroll/echo
```

Expected result: `200`, with `x-apim-gateway: local` and
`x-apim-workspace: Payroll team`.

List the workspaces and what each owns:

```bash
curl -s http://localhost:8000/apim/management/workspaces \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key'
```

Stop the stack with `make down`.

## Configure workspaces

Add a `workspaces` map, an optional `workspace_gateways` map, and a `workspace`
field on each resource a workspace owns:

```json
{
  "workspace_gateways": {
    "orders-gw": { "host_names": ["orders-gw.gateway.localhost"] }
  },
  "workspaces": {
    "orders": {
      "display_name": "Orders team",
      "gateways": ["orders-gw"],
      "policies_xml": "<policies>...</policies>",
      "policy_fragments": ["orders-logging"]
    }
  },
  "apis": { "orders": { "path": "orders", "workspace": "orders", "...": "..." } },
  "products": { "orders-starter": { "name": "Orders starter", "workspace": "orders" } }
}
```

| Setting | Meaning |
| --- | --- |
| `workspaces.<id>.gateways` | Gateways that serve the workspace's APIs. Use workspace gateway IDs, and `managed` for the default gateway (Azure's `serveOn: workspaceAndDefault`). Defaults to `["managed"]`. An empty list means the workspace's APIs are not served anywhere |
| `workspaces.<id>.policies_xml` | The workspace policy scope |
| `workspaces.<id>.policy_fragments` | IDs of the `policy_fragments` the workspace owns. Fragments are stored as plain XML, so ownership is listed here instead of on the fragment |
| `workspace_gateways.<id>.host_names` | Host names that reach this gateway. Entries may use `*` globs. Defaults to `<id>.gateway.localhost` |
| `workspace` on an API, product, subscription, named value or backend | The owning workspace. Leave it out for service-level resources |

A config with no workspaces behaves exactly as before, and saving it writes no
workspace fields.

## Which gateway serves a request

The simulator picks the gateway from the request's host. When the host matches
a workspace gateway's `host_names`, the request is on that workspace gateway.
Otherwise it is on the managed gateway. A forwarded host counts only when the
caller is a trusted proxy (`network_security.trusted_proxy_cidrs`).

- The managed gateway serves service-level APIs, and the APIs of workspaces
  whose `gateways` include `managed`.
- A workspace gateway serves only the APIs of workspaces associated with it.
  Several workspaces can share one.

An API that the current gateway does not serve gets `404`, as if it did not
exist. [Management gateway mode](MANAGEMENT-GATEWAY.md) keeps the caller's
`Host` header, so workspace gateways work through it too.

## Policy order

Workspace APIs run global → workspace → product → API → operation. Each
`<base />` inherits the scope above it, as for the other scopes. Service-level
APIs skip the workspace scope.

Read and write the workspace policy through the policy endpoints, with scope
type `workspace`:

```bash
curl -s http://localhost:8000/apim/management/policies/workspace/orders \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key'
```

`GET /apim/management/policies/api/<api-id>?effective=true` includes the
workspace policy for a workspace API.

Policy expressions can read:

| Expression | Value |
| --- | --- |
| `context.Workspace.Id`, `context.Workspace.Name` | The API's workspace ID and display name. `context.Workspace` is `null` for service-level APIs |
| `context.Deployment.Gateway.Id`, `context.Deployment.GatewayId` | The workspace gateway ID, or `local` on the managed gateway |
| `context.Deployment.Gateway.IsManaged` | `false` on a workspace gateway, `true` on the managed gateway |

## Isolation rules

Every config load and management write is checked. A violation is refused:
the config fails to load, or the write returns `400` with a message that starts
`Workspace isolation:` and names each offending reference.

A resource may reference only resources with the same owner:

| Reference | From |
| --- | --- |
| Products | An API or operation, and a product-scoped subscription |
| An API | An API-scoped subscription |
| Backends | An API or operation's `backend`, a backend pool member, and `set-backend-service backend-id` |
| Named values | `{{name}}` in any policy document or fragment |
| Policy fragments | `include-fragment` in any policy document |

So a workspace cannot use another workspace's resources, a workspace cannot
use service-level named values, backends, fragments or products, and the
service level cannot use workspace resources. Users and groups stay shared, as
in Azure.

The global policy runs for workspace APIs, but it is a service-level document:
it can use service-level named values, backends and fragments only.

When `set-backend-service` picks a backend with a policy expression, the check
happens on the request instead: a workspace backend used by any API outside its
workspace answers `500 Unknown backend`.

Subscriptions follow their owner:

- a product- or API-scoped workspace subscription can only name its own
  workspace's products or APIs;
- an all-APIs subscription covers the APIs of its owner only. A workspace one
  covers that workspace; a service-level one covers service-level APIs;
- the service-scoped (built-in all-access) subscription still covers every API.

Other checks: every `workspace` field and every gateway in `gateways` must name
something that exists, a fragment can belong to one workspace only, two
workspace gateways cannot share a host name, and `managed` is reserved.

Resource IDs are unique across the whole service, as in Azure: one `apis` map
holds both service-level and workspace APIs.

## Manage workspaces through the API

| Request | Result |
| --- | --- |
| `GET /apim/management/workspaces` | Every workspace with its gateways, policy scope and owned resource IDs |
| `GET /apim/management/workspaces/{id}` | One workspace, the same shape |
| `PUT /apim/management/workspaces/{id}` | Create or update. Body fields: `display_name`, `description`, `gateways`, `policy_fragments`. Fields you leave out keep their values |
| `DELETE /apim/management/workspaces/{id}` | `409` while the workspace owns resources. Add `?delete_resources=true` to delete them with it; the response lists what was deleted |
| `GET /apim/management/workspace-gateways` and `/{id}` | Gateways with their host names and the workspaces they serve |
| `PUT /apim/management/workspace-gateways/{id}` | Create or replace. Body fields: `display_name`, `description`, `host_names` |
| `DELETE /apim/management/workspace-gateways/{id}` | `409` while a workspace still names the gateway |

To put a resource in a workspace, send `workspace` in its usual write: `PUT
/apim/management/apis/{id}`, `POST /apim/management/apis/{id}/import`, `PUT
/apim/management/products/{id}`, `PUT /apim/management/backends/{id}`, `PUT
/apim/management/named-values/{id}` and `POST /apim/management/subscriptions`.
A `PUT` that leaves `workspace` out keeps the resource's current owner.

These routes need a platform-level identity. With signed operator identities
on, an operator whose grants are scoped to one workspace cannot create, change
or delete workspaces or gateways. A grant with `workspace_id` covers the APIs
that workspace owns, in addition to any listed in
`control_plane.workspace_apis`.

## How this differs from Azure

| Azure | Simulator |
| --- | --- |
| A workspace gateway is a separate Azure resource with its own hostname, `<gateway-name>-<hash>.gateway.<region>.azure-api.net`, network and scaling | A host name on the same simulator process. There is no separate capacity, network isolation or deployment delay |
| Workspaces are created and changed through ARM, with workspace RBAC roles such as API Management Workspace Contributor | Created through the local management API or config. Local operator grants stand in for RBAC |
| Workspace child resources live under `.../workspaces/{id}/apis/{apiId}` | One flat resource map per type, with a `workspace` owner field. Management routes stay at `/apim/management/apis/{id}` |
| Deleting a workspace in the portal deletes its resources | Deletion refuses while resources remain, unless you pass `delete_resources=true` |
| `context.Deployment.Gateway.Id` returns `managed` on the managed gateway | Returns `local`, the value the simulator has always used |
| Workspaces own tags, groups, schemas and notifications too | Only APIs, products, subscriptions, named values, backends and policy fragments carry an owner. Version sets, tags and groups stay service-level |
| Whether a service-level all-APIs subscription reaches workspace APIs is not stated in the workspace docs | Inferred from the isolation rule: it does not |
