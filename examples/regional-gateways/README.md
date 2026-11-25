# Regional gateways

Run two regional APIM gateways behind one global URL, and route each request
to the backend in the same region. This is the multi-region Premium topology:
a Traffic Manager name in front of regional gateways that share one
configuration.

| Address | Role |
| --- | --- |
| `http://localhost:8000` | Global URL. Stands in for `<name>.azure-api.net` |
| `http://localhost:8001` | West Europe regional gateway |
| `http://localhost:8002` | East US regional gateway |

Both gateways load [`apim.json`](apim.json). They differ only in
`APIM_REGION`, which policies read as `context.Deployment.Region`. Each region
has its own orders backend, reachable only on the internal Docker network.

## Before you start

You need Docker with Compose, `uv`, and ports 8000-8002 free. To run beside
other stacks, add `STACK_SLOT=7` to every command; ports move to 8700-8702.

## Start the stack

```bash
make -C examples/regional-gateways up
```

## Call each region

```bash
curl -i http://localhost:8001/orders/42
curl -i http://localhost:8002/orders/42
```

Expected result: the West Europe gateway answers with
`"backend_region": "West Europe"` and `x-apim-region: West Europe`, and the East
US gateway uses the East US backend. The API policy follows Microsoft's
["route API calls to regional backend services"](https://learn.microsoft.com/en-us/azure/api-management/api-management-howto-deploy-multi-region#route-api-calls-to-regional-backend-services)
pattern:

```xml
<when condition="@(&quot;West Europe&quot;.Equals(context.Deployment.Region, StringComparison.OrdinalIgnoreCase))">
```

## Fail over to the other region's backend

Simulate an outage of the West Europe backend:

```bash
curl -i -H 'x-simulate-outage: West Europe' http://localhost:8001/orders/42
```

Expected result: `200` with `"backend_region": "East US"`, still served by the
West Europe gateway, and `x-apim-attempts: 2`. The backend section wraps
`forward-request` in a [`retry`](../../docs/RETRY-POLICY.md) that switches to
the other region's backend on a 503.

## Use the global URL

```bash
curl -i http://localhost:8000/orders/42
```

Expected result: served by West Europe. Run the regional outage drill:

```bash
make -C examples/regional-gateways failover
```

It stops the West Europe gateway, checks that the global URL is now served by
East US, restarts West Europe, and checks that traffic returns to it.

`make -C examples/regional-gateways verify` runs `up`, `smoke` and `failover`.
Stop the stack with `make -C examples/regional-gateways down`.

## How this differs from Azure

| Azure | Here |
| --- | --- |
| Traffic Manager answers DNS with the lowest-latency healthy regional gateway | nginx proxies to West Europe and uses East US only while West Europe is unreachable. Every caller is treated as nearest West Europe |
| The control plane replicates configuration to every regional gateway | Each gateway loads the same file at start. Management edits on one gateway do not reach the other |
| Regional gateways scale in units across availability zones | One container per region |
