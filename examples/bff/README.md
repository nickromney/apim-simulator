# Backends for Frontends demo

A small Python example of [Microsoft's BFF pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/backends-for-frontends): APIM handles client authentication and routing; separate web and mobile BFF containers shape a shared catalog for their clients. There is no browser app, frontend build, database, or external identity service. One Python module and image serve all three application containers.

## Run

These commands run from the repository root. From this directory, use `make up`,
`make up-series`, `make smoke`, or `make down` directly.

```bash
make -C examples/bff up
make -C examples/bff smoke
make -C examples/bff down
```

The direct path is:

```text
web client    -> APIM /web/catalog    -> web BFF    -> shared backend
mobile client -> APIM /mobile/catalog -> mobile BFF -> shared backend
```

Web responses include descriptions and prices. Mobile responses contain only IDs and names. Each public API requires a signed JWT with its own audience (`bff-web` or `bff-mobile`); the smoke script creates short-lived demo tokens and verifies that they cannot be swapped between APIs. This models gateway authorization without adding an identity-provider container.

The public gateway binds to `127.0.0.1:8000`. BFFs and the backend have no published ports. To isolate ports and Compose project names:

```bash
STACK_SLOT=1 make -C examples/bff up
STACK_SLOT=1 make -C examples/bff smoke
STACK_SLOT=1 make -C examples/bff down
```

## Put APIM gateways in series

After stopping the direct example, start the series variant:

```bash
make -C examples/bff up-series
make -C examples/bff smoke
make -C examples/bff logs
make -C examples/bff down
```

```text
client -> public APIM -> web/mobile BFF -> internal APIM /domain/catalog -> shared backend
```

The overlay changes only the BFFs' domain URL and adds a second simulator. The internal gateway requires `X-Service-Key: bff-internal-demo-key`, which the BFF supplies on its backend request. It has no published host port. Its log entries show the additional gateway hop. Networks demonstrate reachability, not a production security perimeter: the BFFs can also reach the backend directly on the shared domain network.

The two modes use the same default Compose project and replace its BFF configuration; stop one before starting the other. Different stack slots let both run simultaneously. `make -C examples/bff down` removes either mode in the matching slot.

## Scope and verification

This demonstrates routing, separate client audiences, frontend-specific response shaping, and an optional second gateway. API revisions select alternative definitions; they do not add request hops. BFF transport failures, upstream errors, and malformed catalog responses return 502.

```bash
uv run --extra dev pytest tests/test_bff_example.py
make -C examples/bff compose-config
make -C examples/bff compose-config-series
```

The focused tests exercise both paths through actual gateway applications using in-process HTTP transports, including rejected credentials and backend failures. `make -C examples/bff smoke` exercises the running container path and checks public gateway tracing. It does not prove Azure identity or network parity.

All signing and service keys are checked-in local demo credentials. Replace the JWT signing setup with your existing identity provider when adapting the pattern. Container builds use the repository's hardened Python defaults and existing locked dependencies; the upstream image overrides described in the root README also apply.
