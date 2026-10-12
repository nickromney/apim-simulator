# Management API and CLI

Operators inspect and change the simulated service through `/apim/management/...` with `X-Apim-Tenant-Key`, or through `python -m app.cli` (apimsim). The CLI covers status, APIs, products, subscriptions, policies, traces and replay.

## Sub-features

- `mgmt-status` returns the service identity and resource counts.
- `mgmt-apis` lists APIs with paths, upstreams and products.
- `mgmt-write` covers `put-api`, `put-product`, `set-policy` and `delete-*`.
- `mgmt-traces` covers `traces` and `trace`, which require tracing on a request.

## How to get to it (user POV)

- `uv run --extra dev python -m app.cli --base-url http://127.0.0.1:8700 --tenant-key local-dev-tenant-key <command>`

## Driving it with control.sh

Preconditions:

- `control.sh --execute launch gateway` and `doctor` print `OK`.

- **Status.** `control.sh --execute cli status` prints `"name": "apim-simulator"` with `"apis": 2` and `"products": 2`, and exits 0.
- **APIs.** `control.sh --execute cli apis` lists `portal-demo` with path `demo`.
- **Writes (not yet driven).** `control.sh --execute cli put-api <id> <file.json>`, then `control.sh --execute cli api <id>`, then `control.sh --execute request` against the new path.

## Gotchas

- Hello stacks have no `tenant_access`, so management calls fail there by design.
- `cli commands` lists each command's effect without network access. Use it to tell read commands from write commands.
