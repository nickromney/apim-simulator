# Adopting the Consumer Portal and Approval Flows

This note is for downstream consumers of the simulator (for example, a
platform repo deploying the container with a mounted config). It summarises
the product-lifecycle, subscription-approval, and consumer-portal features
added after v0.4.0, and what a consumer must change to use them.

Nothing here is breaking. All new config fields default to the previous
behaviour: existing configs load unchanged, products default to `published`,
and the portal is off unless enabled.

## What Changed

### Gateway semantics

- Products have a `state` (`published` or `not_published`) and an
  `approval_required` flag. Only published products authorize gateway
  traffic; a subscription whose product is unpublished gets
  `403 Product is not published`.
- Subscriptions support the full APIM state set: `active`, `suspended`,
  `cancelled`, `submitted`, `rejected`, `expired`. Only `active` keys
  authenticate; rejections name the state, for example
  `403 Subscription is not active (state: submitted)`.
- `approval_required` is only valid on products with
  `require_subscription: true`; config load fails loudly otherwise.

### Management surface

- `PUT /apim/management/products/{id}` accepts `state` and
  `approval_required`; product projections include both.
- Approval is a state transition, mirroring Azure's ARM shape:
  `PATCH /apim/management/subscriptions/{id}` with `{"state": "active"}`
  (or `"rejected"`). There are no separate approve/reject endpoints.
- The operator console's Subscriptions panel shows Approve/Reject buttons on
  `submitted` subscriptions.

### Consumer portal

A consumer-facing surface at `/apim/portal`, served by the gateway itself
(no extra container):

| Endpoint | Purpose |
| --- | --- |
| `GET /apim/portal` | Static portal page: catalog, sign-up, try-it console |
| `GET /apim/portal/users` | Active config users the page can act as |
| `GET /apim/portal/catalog` | Published products visible to the acting user, with APIs and operations |
| `GET /apim/portal/subscriptions` | The acting user's portal-created subscriptions |
| `POST /apim/portal/subscriptions` | Request a subscription; lands `submitted` when the product requires approval |

Identity is simulator-grade and documented as adapted: the acting user is a
config-defined user passed in the `X-Apim-Portal-User` header. There is no
sign-in, and the portal CMS, theming, and email surface remain out of scope.

Terraform import maps `azurerm_api_management_product.published` and
`.approval_required`, plus `azurerm_api_management_subscription.state`.

## How To Enable It

Add to the gateway config (see [`examples/basic.json`](../examples/basic.json)
for a working reference):

```json
{
  "portal": { "enabled": true },
  "users": {
    "demo-dev": { "id": "demo-dev", "name": "Demo Developer", "state": "active" }
  },
  "groups": {
    "developers": { "id": "developers", "name": "Developers", "users": ["demo-dev"] }
  }
}
```

Visibility rules: unpublished products never appear; products with `groups`
links are visible only to members of those groups; products with no group
links are visible to every portal user (adapted from Azure's built-in
groups).

To demonstrate the approval loop, give a product both flags:

```json
{
  "products": {
    "premium": {
      "name": "Premium",
      "require_subscription": true,
      "approval_required": true
    }
  }
}
```

## Deployment Notes For Downstream Consumers

- The portal shares the gateway port; exposing the gateway exposes
  `/apim/portal` too. The acting-user header is not authentication — treat
  the portal like the rest of the simulator: local and lab-only, never
  internet-facing.
- Portal subscription sign-up persists through the same path as management
  edits. With `APIM_CONFIG_PATH` set, every write workflow (sign-up, product
  edits, policy saves) writes back to that file — if the file is a read-only
  mount (a typical ConfigMap), those requests fail with
  `500 Unable to persist config update`. For write workflows on Kubernetes,
  copy the mounted config to a writable path (for example an `emptyDir`) at
  startup and point `APIM_CONFIG_PATH` there; changes then survive pod
  restarts only as long as that volume does. Without `APIM_CONFIG_PATH`,
  writes apply in memory and reset on restart.
- Releases publish versioned images (`ghcr.io/<owner>/apim-simulator`); pin a
  release tag rather than tracking `latest` so these semantic changes arrive
  deliberately.

## Where The Detail Lives

- End-to-end loop (publish, request, approve, call):
  [tutorial 09](tutorials/apim-get-started/09-customise-developer-portal.md)
  and its scripted form
  [`tutorial09.sh`](tutorials/apim-get-started/tutorial09.sh)
- Feature-by-feature status: [CAPABILITY-MATRIX.md](CAPABILITY-MATRIX.md)
  ("Products and Subscriptions" and "Developer Portal" sections)
- Scope boundaries: [SCOPE.md](SCOPE.md)
- Enforced behaviour contracts:
  [`contracts/contract_matrix.yml`](../contracts/contract_matrix.yml)
  (`AUTH-PRODUCT-PUBLISH-STATE`, `AUTH-SUBSCRIPTION-LIFECYCLE`,
  `PORTAL-CATALOG-VISIBILITY`, `PORTAL-SUBSCRIPTION-SIGNUP`)
