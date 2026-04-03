# Azure APIM Term Map

This document translates common Azure API Management language into the terms,
files, and runtime surfaces used in this repository.

Use it when somebody says "update the APIM product" or "add JWT validation to
the API" and you are not yet fluent in APIM vocabulary.

## The Short Version

| If someone says... | They usually mean... | In this repo, start here |
| --- | --- | --- |
| APIM service | The gateway runtime | `app/main.py`, `compose*.yml` |
| API | A public surface exposed by the gateway | `routes` in config JSON |
| Operation | A specific path and method | backend path, method, and route match |
| Product | A named access bundle | `products` in config JSON |
| Subscription | A client key pair tied to products | `subscription.subscriptions` in config JSON |
| Subscription key | The header clients send | `Ocp-Apim-Subscription-Key` |
| Policy | Gateway behavior before/after backend call | `policies_xml` |
| Backend | The upstream service APIM calls | `upstream_base_url`, `backends` |
| JWT validation | Bearer token verification | `oidc`, `authz`, `validate-jwt` |
| Trace | Per-request APIM detail | `/apim/trace/{id}` |
| Diagnostics / observability | Logs, traces, metrics | OTEL + Grafana LGTM |

## Azure APIM Concepts Translated

### APIM Service

In Azure, this is the API Management instance itself.

In this repo, that is primarily:

- the `apim-simulator` container
- the FastAPI gateway in `app/main.py`

If you are changing gateway behavior, auth flow, routing, or policy execution,
you are changing the local APIM service equivalent.

### API

In Azure APIM, an API is a published surface with operations under it.

In this repo, the practical equivalent is usually one or more routes in config
JSON, for example:

- `examples/basic.json`
- `examples/todo-app/apim.json`
- `examples/oidc/keycloak.json`

For beginners, think:

- public path prefix
- target backend
- auth requirements

### Operation

In Azure APIM, an operation is usually a method plus path.

In this repo, operation-level thinking is split across:

- backend path handling
- HTTP method
- route matching in the gateway

If you are troubleshooting one endpoint, think in terms of:

- request path
- request method
- matched route
- backend path after any rewrite

### Product

In Azure APIM, a product groups APIs for access control and subscription.

In this repo, products live in config JSON:

```json
"products": {
  "todo-demo": {
    "name": "Todo Demo",
    "require_subscription": true
  }
}
```

Beginners should remember:

- products are about access packaging
- a product can require a subscription
- routes can be attached to products

### Subscription

In Azure APIM, a subscription grants access to products.

In this repo, subscriptions also live in config JSON:

```json
"subscription": {
  "required": true,
  "subscriptions": {
    "todo-demo": {
      "keys": {
        "primary": "todo-demo-key"
      },
      "products": ["todo-demo"]
    }
  }
}
```

Beginners should remember:

- a subscription key is not a user login
- it proves the caller is allowed to use a product
- routes tied to that product can accept or reject the request based on the key

### Policy

In Azure APIM, policies are XML rules that run in the gateway.

In this repo, policies are typically attached as `policies_xml`.

Example:

```xml
<policies>
  <inbound />
  <backend />
  <outbound>
    <set-header name="x-todo-demo-policy" exists-action="override">
      <value>applied</value>
    </set-header>
  </outbound>
  <on-error />
</policies>
```

For beginners, a policy is just:

- "before the backend call, do this"
- "after the backend call, do this"
- "if something fails, do this"

### Backend

In Azure APIM, a backend is the service APIM forwards requests to.

In this repo, the simple form is:

- `upstream_base_url`
- `upstream_path_prefix`

The more advanced form uses named `backends`.

For the todo example, the backend is the internal FastAPI service:

- container: `todo-api`
- app file: `examples/todo-app/api-fastapi-container-app/main.py`

### JWT Validation

In Azure APIM, JWT validation is often configured with policies or OpenID
providers.

In this repo, the relevant pieces are:

- `oidc` for issuer, audience, and JWKS
- `authz.required_scopes`
- `authz.required_roles`
- `authz.required_claims`
- `validate-jwt` support in the policy engine

For beginners:

- subscription keys answer "is this caller allowed to consume the product?"
- JWTs answer "who is the caller and what are they allowed to do?"

### Trace

In Azure APIM, trace and diagnostic tooling help you explain gateway behavior.

In this repo, the APIM-shaped trace surface is:

- request header: `x-apim-trace: true`
- response header: `x-apim-trace-id`
- lookup endpoint: `/apim/trace/{id}`

Use this when:

- a request failed in a surprising way
- you need to understand policy execution
- you need to explain a `401`, `403`, `404`, or `429`

### Diagnostics And Observability

In Azure, you may hear people talk about diagnostics, Application Insights,
logging, or distributed tracing.

In this repo, the practical observability stack is:

- OTEL instrumentation in the gateway and backend
- Grafana LGTM for logs, metrics, and traces
- the dashboard at `http://localhost:3001/d/apim-simulator-overview/apim-simulator-overview`

For beginners, use these words precisely:

- logs: individual event records
- traces: per-request flow across services
- metrics: numeric time-series signals

## If Someone Gives You An Azure APIM Task

Use this quick translation table.

| Task someone says aloud | What you should inspect first |
| --- | --- |
| Add a new API behind APIM | backend app + route config JSON |
| Require subscriptions for this API | `products`, `subscription`, route `product` |
| Require JWT for this route | `allow_anonymous`, `oidc`, route `authz` |
| Add a policy header | `policies_xml` |
| Rate-limit this endpoint | policy XML |
| Show me why this request failed | response headers, `/apim/trace/{id}`, Grafana |
| Prove the browser is going through APIM | todo UI transcript, Proxyman, HAR |
| Show me telemetry for this route | Grafana dashboard and Explore |

## Good Beginner Habits

- Do not start by changing three auth layers at once.
- Get one route working anonymously first if you can.
- Then add subscription protection.
- Then add JWT or route-level authz.
- Always keep one positive case and one negative case.
- Always verify with both a request tool and an observability tool.

## Read Next

- Guided onboarding: [`APIM-TRAINING-GUIDE.md`](./APIM-TRAINING-GUIDE.md)
- First-day checklist: [`FIRST-DAY-APIM-CHECKLIST.md`](./FIRST-DAY-APIM-CHECKLIST.md)
- Team delivery tasks: [`APIM-TEAM-PLAYBOOK.md`](./APIM-TEAM-PLAYBOOK.md)
