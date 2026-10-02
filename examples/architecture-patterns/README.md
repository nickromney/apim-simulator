# API architecture patterns

Runnable local examples of patterns commonly placed at an API gateway. This stack uses one public gateway on `127.0.0.1:8000` and private backends on an internal Compose network. It starts no cloud services and publishes no backend ports.

```bash
make -C examples/architecture-patterns up
make -C examples/architecture-patterns smoke
make -C examples/architecture-patterns down
```

Set `STACK_SLOT=1` on each command to move the gateway port to 8100 and use a separate Compose project.

| Pattern | Local route | What to inspect |
| --- | --- | --- |
| Routing | `GET /route/catalog`, `GET /route/orders` | The operations go to distinct private services and return different backend markers. |
| Offloading | `GET /offload/catalog` | APIM applies a per-client rate limit, internal cache lookup/store, an inbound header transform, and an outbound marker. Repeated requests avoid a second backend call. |
| Gatekeeper | `GET /guard/catalog` | A signed JWT with issuer `http://patterns.local` and audience `patterns-client` is required; the subject is limited to two calls per minute. |
| Aggregation | `GET /aggregate/overview` | Two `send-request` callouts fetch orders and profile, and inbound `return-response` composes them into one JSON response. `?fail=true` makes one source return 503; `?fail=transport` simulates a connection failure. Both return 502. |
| Backend counts | `GET /diagnostics/stats` | Read private backend request counts to confirm cache hits and rejected requests do not fan through. |

All policy XML is APIM-shaped and lives in [apim.json](apim.json), so it can be compared with Azure APIM policy behavior. The smoke script checks successful responses, JWT rejection, rate limiting, cache hit backend counts, the combined aggregate document, a source HTTP failure, and a callout connection failure. The checked-in signing key and returned data are local teaching fixtures.

For a Backends for Frontends example with separate web/mobile response shaping and an optional second gateway hop, use [examples/bff](../bff/README.md). This directory links to that demo rather than duplicating its application topology.

## Azure comparison

`make -C examples/architecture-patterns azure-plan` checks the source config
and XML locally. `azure-validate` creates isolated temporary APIs on an existing
APIM service to check native XML acceptance and removes them afterward.
`azure-compare` also runs request cases against Azure and the local lab. Set
`AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and `AZURE_APIM_SERVICE`;
optionally set `AZURE_APIM_BASE_URL` and `AZURE_REPORT`. These targets provision
no compute or network resources and do not restart cloud backends.

The cloud runtime uses mock operations on APIM with a request-id marker to
distinguish a cache hit from two identical uncached responses. It checks the
expected statuses, stable bodies, and selected headers; it cannot establish
production capacity or networking guarantees. See the
[dated comparison report](../../docs/ARCHITECTURE-PATTERNS-COMPARISON.md) for
recorded results and the private-network requirement.

## Limits

The gateway cache and rate-limit counters are in-memory and reset when the simulator restarts. Docker network isolation demonstrates reachability boundaries for local learning; it does not establish a production security perimeter. Local JWT validation and demo credentials do not substitute for an identity provider or Azure managed identity. Azure and simulator cache scope, quota distribution, networking, and managed identity lifecycle need a separate environment-specific comparison.
