# Adapter, tenant stamps, and failure isolation

This local lab combines three patterns from the [Microsoft Azure Architecture Center catalogue](https://learn.microsoft.com/en-us/azure/architecture/patterns/): [Anti-Corruption Layer](https://learn.microsoft.com/en-us/azure/architecture/patterns/anti-corruption-layer), [Bulkhead](https://learn.microsoft.com/en-us/azure/architecture/patterns/bulkhead), and [Deployment Stamps](https://learn.microsoft.com/en-us/azure/architecture/patterns/deployment-stamp).

## Run

From the repository root:

```bash
make -C examples/architecture-patterns-extra up
make -C examples/architecture-patterns-extra smoke
make -C examples/architecture-patterns-extra down
```

`STACK_SLOT=1` gives the gateway a shifted host port and a separate Compose project:

```bash
STACK_SLOT=1 make -C examples/architecture-patterns-extra up
STACK_SLOT=1 make -C examples/architecture-patterns-extra smoke
STACK_SLOT=1 make -C examples/architecture-patterns-extra down
```

## Scenarios

The adapter path is `client -> APIM /orders/{id} -> order-adapter -> legacy-orders`. APIM owns the public route and forwarding. The adapter owns domain translation: it maps legacy names and cents into a stable order DTO, and omits a legacy-only field. A failed legacy call becomes a 502 at the adapter boundary.

The deployment-stamp path maps `/tenants/a/*` and `/tenants/b/*` to separate backend pools and separate service containers. Each pool has its own breaker state. `?fail=true` injects a 503 in the selected local stamp. After the failure opens tenant A's pool, tenant A receives a gateway 503 while tenant B continues to serve its own stamp. This illustrates tenant routing and failure-domain separation, rather than relying on caller-specific rate limits as a proxy for bulkhead isolation.

Use the local failure control only for the demonstration:

```bash
curl http://localhost:8000/orders/42
curl http://localhost:8000/tenants/a/catalog
curl 'http://localhost:8000/tenants/a/catalog?fail=true'
curl http://localhost:8000/tenants/a/catalog
curl http://localhost:8000/tenants/b/catalog
```

The next tenant A call shows the isolated result: its pool is open, while tenant B remains healthy. The local breaker opens for one second and then permits recovery, so the smoke target can be run repeatedly without restarting the Compose project.

## What this demonstrates and what Azure supplies

The simulator demonstrates selected API routing, backend-pool selection, a local circuit-breaker state transition, and a real adapter service making an HTTP request to a legacy service. The adapter—not APIM—owns domain mapping. Stamp services represent separate tenants and canaries locally; APIM routes to them using configured API paths and named pools.

This is not an Azure deployment. The local Compose networks and containers do not prove Azure VNet/private endpoint boundaries, Azure Front Door global routing, Cosmos DB tenant-to-stamp lookup or replication, APIM multi-region behavior, Azure workload identity, managed-service SLAs, or production stamp provisioning and rollout. The sample routes tenants through explicit path prefixes and does not perform a live tenant-map lookup. The pool circuit breaker is simulator behavior, not an Azure resource guarantee. Each local backend container has its own CPU and memory limit to demonstrate bounded resource use, but Compose limits do not prove production capacity isolation or Azure scaling behavior.

Focused behavior tests:

```bash
make -C examples/architecture-patterns-extra test
```
