# 9 - Customise Developer Portal

Source: [Tutorial: Access and customise the developer portal](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-developer-portal-customize)

Simulator status: Adapted

## What Maps And What Does Not

The Microsoft developer portal is two things wearing one name: a managed CMS
(page editing, theming, sign-up emails) and a set of consumer workflows
(browse published products, request a subscription, try an API call).

The simulator ships the consumer workflows at `/apim/portal` and leaves the
CMS out of scope. Identity is simulator-grade: the acting user is a
config-defined user passed in the `X-Apim-Portal-User` header, not a
signed-in account.

## Local Equivalent

From the repo root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
```

Start the operator console stack:

```bash
make up-ui
```

Open the two portals side by side:

- consumer developer portal: `http://localhost:8000/apim/portal`
- operator console: `http://localhost:3007`

Then walk the publish-and-approve loop:

1. Create an approval-gated product as the operator:

   ```bash
   curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
     -H "Content-Type: application/json" \
     "$APIM_BASE/apim/management/products/portal-premium" \
     --data '{"name":"Portal Premium","require_subscription":true,"approval_required":true}'
   ```

2. Attach an API to the product:

   ```bash
   curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
     -H "Content-Type: application/json" \
     "$APIM_BASE/apim/management/apis/portal-hello" \
     --data '{"name":"Portal Hello","path":"portal-hello","upstream_base_url":"http://mock-backend:8080","upstream_path_prefix":"/api","products":["portal-premium"]}'
   ```

3. Request a subscription as the portal user (or click "Request subscription"
   on the portal page):

   ```bash
   curl -sS -X POST -H "X-Apim-Portal-User: demo-dev" \
     -H "Content-Type: application/json" \
     "$APIM_BASE/apim/portal/subscriptions" \
     --data '{"product_id":"portal-premium"}'
   ```

   The subscription lands in `submitted` state, and its key returns `403`
   until it is approved.

4. Approve it as the operator (or click "Approve" in the console's
   Subscriptions panel):

   ```bash
   curl -sS -X PATCH -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
     -H "Content-Type: application/json" \
     "$APIM_BASE/apim/management/subscriptions/demo-dev-portal-premium" \
     --data '{"state":"active"}'
   ```

5. Call the API with the approved key:

   ```bash
   curl -sS -H "Ocp-Apim-Subscription-Key: sub-demo-dev-portal-premium-primary" \
     "$APIM_BASE/portal-hello/health"
   ```

## Shortcut

If you want the scripted shortcut instead of running the commands manually:

```bash
./docs/tutorials/apim-get-started/tutorial09.sh --setup
./docs/tutorials/apim-get-started/tutorial09.sh --verify
```

Use `--setup` to have [`tutorial09.sh`](tutorial09.sh) perform the local setup for this step. Use `--verify` to validate the existing tutorial state without restarting the stack.

Expected key `./docs/tutorials/apim-get-started/tutorial09.sh --verify` output:

```text
Verifying the consumer developer portal
$ curl -i "http://localhost:8000/apim/portal"
{
  "status_code": 200
}

$ curl -sS -H "X-Apim-Portal-User: demo-dev" "http://localhost:8000/apim/portal/catalog"
{
  "api_ids": [
    "portal-hello"
  ],
  "approval_required": true,
  "product_id": "portal-premium"
}

$ curl -sS -H "X-Apim-Portal-User: demo-dev" "http://localhost:8000/apim/portal/subscriptions"
{
  "id": "demo-dev-portal-premium",
  "state": "active"
}

$ curl -sS -H "Ocp-Apim-Subscription-Key: sub-demo-dev-portal-premium-primary" "http://localhost:8000/portal-hello/health"
{
  "path": "/api/health",
  "status": "ok"
}
```

## Guidance

If you need to rehearse portal CMS customisation, theming, or sign-up emails, use real Azure APIM.
If you need to rehearse the consumer loop — publish, discover, request, approve, call — the simulator covers it locally.
