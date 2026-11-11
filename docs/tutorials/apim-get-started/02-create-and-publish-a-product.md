# 2 - Create and publish a product

Microsoft Learn equivalent tutorial: [Tutorial: Create and publish a product](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-add-products)

Create a product, add an API, and request a subscription to call it.

## Use the operator console

Start the stack with `make up` and [import `tutorial-api`](01-import-your-first-api.md).
Open [the operator console](http://localhost:3007), choose **Load Local Demo**,
and connect.

1. Open **Products**, select **New product**, and enter `tutorial-product` as
   the product ID and `Tutorial Product` as its name.
2. Leave **Require subscription** selected. Optionally enter a description,
   terms, a subscription limit, or require approval. Select **Published** and
   save the product.
3. Under **API association**, select `tutorial-api` and choose **Add API**.
4. Open [the developer portal](http://localhost:8000/apim/portal) to discover
   the published product and request a subscription. If approval is required,
   approve the request in the operator console before using its key.

A published subscription product with no group restrictions is visible to
portal users. Group-restricted products retain their existing groups when edited;
manage group links through the management API. Open products are visible only
to administrators in this simulator. Publication controls portal discovery,
while a valid subscription can access an unpublished product through the gateway.

## Use the management API

From the repo root:

```bash
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key
```

Import `tutorial-api` before running these commands.

Create and publish the product.

```bash
curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/products/tutorial-product" \
  --data '{"name":"Tutorial Product","state":"published","require_subscription":true}'
```

Attach the imported API to the product:

```bash
curl -sS -X PUT -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/apis/tutorial-api" \
  --data '{"name":"Tutorial API","path":"tutorial-api","upstream_base_url":"http://mock-backend:8080/api","products":["tutorial-product"]}'
```

Create a subscription:

```bash
curl -sS -X POST -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" \
  -H "Content-Type: application/json" \
  "$APIM_BASE/apim/management/subscriptions" \
  --data '{"id":"tutorial-sub","name":"tutorial-sub","products":["tutorial-product"],"primary_key":"tutorial-key"}'
```

Verify access:

```bash
curl -i "$APIM_BASE/tutorial-api/health"
curl -i -H "Ocp-Apim-Subscription-Key: tutorial-key" "$APIM_BASE/tutorial-api/health"
```

The first request returns HTTP 401. The second returns HTTP 200 and
`"status": "ok"`.

## Run the setup script

To run setup and verify the results with the tutorial script:

```bash
./docs/tutorials/apim-get-started/tutorial02.sh --setup
./docs/tutorials/apim-get-started/tutorial02.sh --verify
```

Use `--setup` to create the product and subscription. Use `--verify` to check the existing state without restarting the stack.

Verification confirms the product appears in the catalog and has one active
subscription.

## Publication, terms and limits

Products you create through the management API default to `not_published`. To
publish an existing product, set `state` to `published` with a product PUT.

When you set product terms, the developer must accept them before requesting a
subscription. A request without acceptance returns HTTP 400. A request that
exceeds the product's per-developer subscription limit returns HTTP 409. An API
can belong to one open product.
