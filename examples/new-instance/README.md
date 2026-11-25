# New APIM instance

Start a gateway that holds what a new classic-tier Azure API Management
instance contains: the Echo API, the Starter and Unlimited products, the
Administrators, Developers and Guests groups, and the built-in all-access
subscription. Use it to follow Microsoft's APIM hands-on lab step by step
locally.

The configuration is [`apim.json`](apim.json). It runs on the root gateway
stack, so the developer portal and management API work as usual.

## Before you start

You need Docker with Compose, `uv`, and port 8000 free. To run beside other
stacks, add `STACK_SLOT=7` to every command; the gateway moves to port 8700.

## Start the stack

```bash
make -C examples/new-instance up
```

## Call the Echo API without a key

```bash
curl -i 'http://localhost:8000/echo/resource?param1=sample'
```

Expected result: `401`, because Starter and Unlimited both require a
subscription key, as on a new instance.

## Hit Starter's rate limit

```bash
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w '%{http_code}\n' \
    -H 'Ocp-Apim-Subscription-Key: new-instance-starter-primary' \
    'http://localhost:8000/echo/resource?param1=sample'
done
```

Expected result: five `200` responses, then `429`. Starter allows 5 calls per
60 seconds and 100 calls per week.

The Unlimited key `new-instance-unlimited-primary` has no limits. The built-in
all-access key `new-instance-all-access-primary` is not tied to a product, so
Starter's policy never applies to it.

## Try each Echo operation

```bash
curl -I -H 'Ocp-Apim-Subscription-Key: new-instance-all-access-primary' \
  'http://localhost:8000/echo/resource?param1=sample'
```

Expected result: `200` with headers and no body. `POST`, `PUT` and `DELETE` on
`/echo/resource` echo the request back. `GET /echo/resource-cached` caches
the response for an hour: call it twice with a different header and the
second response repeats the first.

## Subscribe through the developer portal

Open [http://localhost:8000/apim/portal](http://localhost:8000/apim/portal),
or call the portal API as `demo-dev`, a member of Developers:

```bash
curl -s -X POST http://localhost:8000/apim/portal/subscriptions \
  -H 'X-Apim-Portal-User: demo-dev' -H 'Content-Type: application/json' \
  -d '{"product_id": "unlimited"}'
```

Expected result: the subscription comes back `submitted`, and its key is
refused until an administrator approves it. A Starter request comes back
`active` straight away; a second Starter request gets `409` because each
product allows one subscription per developer.

Approve the Unlimited subscription as the administrator:

```bash
curl -s -X PATCH http://localhost:8000/apim/management/subscriptions/demo-dev-unlimited \
  -H 'X-Apim-Tenant-Key: local-dev-tenant-key' -H 'Content-Type: application/json' \
  -d '{"state": "active"}'
```

Expected result: the key works, and
`GET /apim/management/outbox` holds the "Your subscription is activated" email
for `demo-dev@example.test`.

## Check the whole flow

```bash
make -C examples/new-instance verify
```

Expected result: `new instance smoke passed`. Run it on a fresh stack: the rate
limit and portal subscriptions carry over until you stop the stack.

```bash
make -C examples/new-instance down
```

## Where each default comes from

Microsoft Learn documents only part of a new instance's contents.

| Default | Source |
| --- | --- |
| Administrators (the creating admin; system-managed), Developers (signed-in portal users) and Guests (anonymous portal users) | Documented: [Create and use groups](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-create-groups) |
| A built-in, service-scoped all-access subscription that product policies do not apply to | Documented: [Subscriptions](https://learn.microsoft.com/en-us/azure/api-management/api-management-subscriptions) |
| The creating administrator is subscribed to every product | Documented: [Create and publish a product](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-add-products) |
| Starter and Unlimited exist "in certain tiers" | Documented, without a tier list: [Import and publish your first API](https://learn.microsoft.com/en-us/azure/api-management/import-and-publish) |
| Starter: 5 calls per 60 seconds and 100 calls per week as product policy; subscribes without approval | Shown in the masterclass recording, chapters 4, 7 and 11 |
| Unlimited: needs administrator approval; no limits | Shown in the masterclass recording, chapter 4 |
| Guests and Developers can see Starter and Unlimited | Shown in the masterclass recording, chapter 7 |
| Echo API at suffix `echo`, in both products; a call without a key fails | Shown in the masterclass recording, chapter 3 |
| Echo's six operations and their descriptions, `param1`/`param2`, the one-hour cache on `resource-cached`, product descriptions, one subscription per product, a global policy that only forwards | Observed in an exported new instance; not on Microsoft Learn |

## How this differs from Azure

- The Echo API calls the repo's mock backend instead of Azure's hosted echo
  service. Responses echo the method, path, headers and body as JSON.
- Keys are fixed placeholders, such as `new-instance-starter-primary`, so the
  commands above work as written.
- The administrator is user `1` with `admin@example.test`. `demo-dev` is an
  extra Developers member for trying portal sign-up.
- Developer portal users are chosen with the `X-Apim-Portal-User` header
  instead of signing in.
- The management API uses the stack's tenant key instead of Azure RBAC.
