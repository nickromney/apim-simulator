# Migrating From AWS API Gateway

This simulator is a good fit when you want a cheaper, local-first place to
learn APIM concepts without needing the full Azure control plane.

The first target is AWS API Gateway HTTP API and REST API users. WebSocket and
developer portal migration paths are still out of scope.

## Mental Model Mapping

| AWS API Gateway concept | Local APIM simulator concept |
| --- | --- |
| API | `apis.<id>` |
| Route or method | `apis.<id>.operations.<id>` |
| Stage | API `path` or local env-specific config |
| Integration | `backends.<id>` or API `upstream_base_url` |
| Usage plan + API key | `product` + `subscription` |
| Authorizer | `oidc`, `oidc_providers`, `authz`, or `validate-jwt` policy |
| Mapping template or parameter mapping | APIM policy XML |
| CloudWatch logs / X-Ray | OTEL + [Grafana LGTM](https://github.com/grafana/docker-otel-lgtm) + `/apim/trace/{id}` |

## Practical Translation

### Stage

If you are used to `/dev` or `/prod`, model that locally with the API path.

Example:

- AWS style: `/prod/orders`
- local simulator style: API path `prod` plus operation `/orders`

### Usage Plan And API Key

Model usage plans as products and client API keys as subscriptions.

- product decides whether a subscription is required
- subscription carries the key pair
- APIs and operations attach to products

### Authorizer

Model JWT authorizers with:

- `oidc` or `oidc_providers` for issuer and audience
- `authz.required_roles`
- `authz.required_scopes`
- `authz.required_claims`

If you are already thinking in policy terms, use `validate-jwt`.

### Integration

Model upstream integrations with:

- API `upstream_base_url` for the simple case
- `backends` when you want reusable backend config or credentials

### Mapping And Transformation

Model request and response shaping with policies. Common starting points:

- `set-header`
- `set-query-parameter`
- `rewrite-uri`
- `set-body`
- `include-fragment`

### Diagnostics

Use the local tools together:

- `/apim/trace/{id}` for APIM-style per-request detail
- `/apim/management/traces` for recent trace browsing
- Grafana on [https://lgtm.apim.127.0.0.1.sslip.io:8443](https://lgtm.apim.127.0.0.1.sslip.io:8443) when [LGTM](https://github.com/grafana/docker-otel-lgtm) is enabled

## Starter Example

Use the starter under
[`examples/migrating-from-aws-api-gateway/`](../examples/migrating-from-aws-api-gateway/README.md)
when you want a familiar stage-like path and usage-plan style access pattern.

Bring it up on the existing hello backend with:

```bash
HELLO_APIM_CONFIG_PATH=/app/examples/migrating-from-aws-api-gateway/apim.http-api.json make up-hello
curl -H "Ocp-Apim-Subscription-Key: aws-migration-demo-key" http://localhost:8000/prod/hello
```

## Run a Real AWS-Shaped Gateway Beside the Simulator

If you want to compare against the real thing rather than just a mapping
table, `compose.aws.yml` is an opt-in overlay that runs
[LocalStack](https://github.com/localstack/localstack) (community edition)
with a REST API proxying to the same mock backend the simulator fronts. That
gives you two gateways pointed at one upstream: APIM-shaped on `:8000`,
AWS-shaped on `:4566`.

Note on the pin: the LocalStack community line was archived on 2026-03-23 and
its successor image requires an account-linked auth token, so this overlay
deliberately pins the frozen final community image
(`localstack/localstack:4.9`). It still serves REST API v1 proxy invocation
correctly and stays out of `up-all` and CI. The alternatives considered
(including an empirical test showing moto server has no API Gateway data
plane) and the revisit triggers are recorded in
[ADR 0002](adr/0002-localstack-archival-pin.md). If you hold a LocalStack
auth token, the unified image is a drop-in `image:` override.

```bash
make up-aws
make smoke-aws
```

The init script (`examples/aws-api-gateway/init-aws.sh`) creates a REST API
with a fixed id (`apimsim`), wires an `ANY {proxy+}` method with an
`HTTP_PROXY` integration to the mock backend, and deploys it to a `local`
stage. Invoke it with either URL form LocalStack supports:

```bash
# path-style
curl http://localhost:4566/restapis/apimsim/local/_user_request_/echo

# host-style
curl http://apimsim.execute-api.localhost.localstack.cloud:4566/local/echo
```

This overlay is deliberately not part of `up-all` or CI — it pulls a large
third-party image and exists as a comparison aid, not a core stack. LocalStack
community covers REST API v1 `HTTP_PROXY` integrations well, which is what
this overlay exercises. HTTP API v2 and Lambda/JWT authorizers vary by
LocalStack version and edition and are out of scope here. See
[`docs/adr/0001-goldilocks-ai-gateway-scope.md`](adr/0001-goldilocks-ai-gateway-scope.md)
(decision D4) for why LocalStack rather than a homegrown simulator.

## Good First Moves

1. Start with subscription-only access and prove the path works.
2. Add JWT validation once the basic flow is stable.
3. Move request shaping into policies only after the routing and auth path is green.
4. Use the management summary and trace endpoints before debugging raw policy XML.
