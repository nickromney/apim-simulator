# Local APIM Simulator

This repository is the standalone extraction of the APIM simulator work that started in `platform/apps/subnet-calculator`. The goal here is narrower and cleaner: a Docker-first Azure API Management lab that can evolve independently of the broader platform stack.

## New To APIs Or APIM?

Start with [docs/APIM-TRAINING-GUIDE.md](docs/APIM-TRAINING-GUIDE.md).

That guide is written for people who are new to APIs, Azure API Management, or
OpenTelemetry and need a directive path through:

- writing a simple backend API
- putting APIM in front of it
- securing it with a subscription key and/or JWT
- proving it works with browser, curl, Bruno, Proxyman, APIM trace, and Grafana

Companion docs:

- [docs/FIRST-DAY-APIM-CHECKLIST.md](docs/FIRST-DAY-APIM-CHECKLIST.md)
- [docs/AZURE-APIM-TERM-MAP.md](docs/AZURE-APIM-TERM-MAP.md)
- [docs/APIM-SDK-SURFACE-GUIDE.md](docs/APIM-SDK-SURFACE-GUIDE.md)
- [docs/APIM-TEAM-PLAYBOOK.md](docs/APIM-TEAM-PLAYBOOK.md)
- [docs/APIM-STARTER-RECIPE.md](docs/APIM-STARTER-RECIPE.md)
- [docs/MIGRATING-FROM-AWS-API-GATEWAY.md](docs/MIGRATING-FROM-AWS-API-GATEWAY.md)

If you want the fastest possible success path, run:

```bash
make up-todo-otel
make smoke-todo
make verify-todo-otel
```

Then open `http://localhost:3000` and create a todo through APIM.

## Current Shape

- Python/FastAPI gateway engine for fast iteration and readable policy/auth logic
- Config-driven APIs, operations, products, subscriptions, tenant access keys, OIDC, traces, and a practical APIM XML policy subset
- Policy-driven response caching, value caching, and by-key throttling for local APIM-shaped gateway flows
- Terraform/OpenTofu import of APIM APIs, version sets, backends, named values, policies, and OpenAPI-derived operations
- APIM-style management resources for service, APIs, operations, products, subscriptions, backends, named values, version sets, policy fragments, users, and groups
- Static compatibility reporting before runtime plus optional live Azure diff tooling for high-confidence verification
- Compose-first runtime overlays for direct public, edge HTTP, edge TLS, private/internal, OIDC, and MCP scenarios
- A curated APIM sample compatibility harness plus an operator console over the local management APIs
- An MCP-focused example that places the simulator in front of a streamable HTTP MCP server
- Example assets from the original subnet-calculator stack kept under `examples/subnet-calculator/`

## Quick Start

### Direct public stack

```bash
make up
until curl -fsS http://localhost:8000/apim/startup >/dev/null; do sleep 1; done
curl http://localhost:8000/apim/health
curl --retry 10 --retry-delay 1 --retry-connrefused http://localhost:8000/api/echo
```

### Direct public stack with LGTM

```bash
make up-otel
curl --retry 10 --retry-delay 1 --retry-connrefused http://localhost:8000/api/echo
make verify-otel
```

Grafana is exposed on `http://localhost:3001` with `admin` / `admin`. The
gateway exports OpenTelemetry logs, traces, and metrics to the bundled LGTM
collector over OTLP HTTP (`http://lgtm:4318`) and keeps the existing APIM trace
endpoint for policy-level debugging.

The stack provisions an `APIM Simulator Overview` dashboard automatically. It
combines custom gateway metrics, todo API metrics, Loki logs, and Tempo-backed
span throughput so the local OTEL setup is useful immediately after `up`.

### MCP stack

```bash
make up-mcp
make smoke-mcp
```

This starts a minimal streamable HTTP MCP server behind the simulator and exercises it through `http://localhost:8000/mcp` with an APIM subscription key.

### Edge HTTP stack

```bash
make up-edge
make smoke-edge
```

`apim.localtest.me` resolves to `127.0.0.1` through `localtest.me`, so no `/etc/hosts` changes are required.

### Edge TLS stack

```bash
make up-tls
make smoke-tls
```

`make up-tls` generates repo-local development certificates under `examples/edge/certs/` with `openssl`. The HTTPS smoke defaults to strict verification against that generated local CA.

Override verification only when you mean to:

```bash
SMOKE_EDGE_CA_CERT=/path/to/ca.crt make smoke-tls
SMOKE_EDGE_INSECURE_SKIP_VERIFY=true make smoke-tls
SMOKE_MCP_CA_CERT=/path/to/ca.crt make smoke-mcp
SMOKE_MCP_INSECURE_SKIP_VERIFY=true make smoke-mcp
```

### Private/internal stack

```bash
docker compose -f compose.yml -f compose.private.yml -f compose.mcp.yml up --build -d
make smoke-private
```

This keeps the gateway off host ports and validates private reachability from the internal `smoke-runner` container.

### OIDC stack

```bash
make up-oidc
make smoke-oidc
```

Fetch a token manually if you want to inspect the flow:

```bash
TOKEN=$(uv run python scripts/get_keycloak_token.py)

curl \
  -H "Authorization: Bearer $TOKEN" \
  -H "Ocp-Apim-Subscription-Key: oidc-demo-key" \
  http://localhost:8000/api/echo
```

### Hello starter example

```bash
make up-hello
make smoke-hello
```

Switch auth modes with the checked-in starter configs under
`examples/hello-api/`:

```bash
make up-hello-subscription
SMOKE_HELLO_MODE=subscription make smoke-hello

make up-hello-oidc
SMOKE_HELLO_MODE=oidc-jwt make smoke-hello

make up-hello-oidc-subscription
SMOKE_HELLO_MODE=oidc-subscription make smoke-hello
```

Add LGTM with:

```bash
make up-hello-otel
make smoke-hello
make verify-hello-otel
```

The starter ships with four APIM variants:

- `examples/hello-api/apim.anonymous.json`
- `examples/hello-api/apim.subscription.json`
- `examples/hello-api/apim.oidc.jwt-only.json`
- `examples/hello-api/apim.oidc.subscription.json`

Use it when you want the smallest possible service that still demonstrates:

- a simple backend API
- APIM in front of it
- subscription auth and/or JWT auth
- shared OTEL wiring that can move between this repo and `platform`

### Migrating From AWS API Gateway

Use the guide at [docs/MIGRATING-FROM-AWS-API-GATEWAY.md](docs/MIGRATING-FROM-AWS-API-GATEWAY.md)
with the starter under
[`examples/migrating-from-aws-api-gateway/`](examples/migrating-from-aws-api-gateway/README.md)
when you want a familiar local shape for:

- stage-style paths
- usage plan plus API key equivalents
- JWT authorizers
- backend integrations
- policy-based header and payload shaping

Fastest path:

```bash
HELLO_APIM_CONFIG_PATH=/app/examples/migrating-from-aws-api-gateway/apim.http-api.json make up-hello
curl -H "Ocp-Apim-Subscription-Key: aws-migration-demo-key" http://localhost:8000/prod/hello
```

### Todo demo stack

```bash
make up-todo
make up-todo-otel
make smoke-todo
make test-todo-e2e
make test-todo-bruno
make export-todo-har
```

This starts a small Astro frontend on `http://localhost:3000`, brokers every
browser API call through the simulator on `http://localhost:8000`, and keeps the
FastAPI todo backend internal-only. The example also ships with a Bruno
collection and a Proxyman-ready HAR capture under
`examples/todo-app/api-clients/`.

`make up-todo-otel` adds LGTM on `http://localhost:3001` and instruments both
the simulator and the toy FastAPI backend with the same OTEL env contract used
for the main gateway.

Verify the OTEL path against the running todo stack with:

```bash
make verify-todo-otel
```

### Operator console

```bash
make up-ui
```

Open `http://localhost:3007` and connect to `http://localhost:8000` with tenant key `local-dev-tenant-key`.

### Terraform/OpenTofu import

Start the direct stack, then import a `tofu show -json` payload into the running simulator:

```bash
make up
TOFU_SHOW=/path/to/tofu-show.json make import-tofu
```

Preflight a Terraform/APIM payload without starting the gateway:

```bash
TOFU_SHOW=/path/to/tofu-show.json make compat-report
```

Key Vault-backed named values are intentionally local-first. Provide local overrides with env vars in the form `APIM_NAMED_VALUE_<NAME>`:

```bash
export APIM_NAMED_VALUE_BACKEND_SECRET=super-secret-token
```

### Tear down

```bash
make down
```

## Development

```bash
make install-hooks
make lint-check
make test
make compat
TOFU_SHOW=tests/fixtures/tofu_show/sample.json make compat-report
make compose-config
make compose-config-edge
make compose-config-tls
make compose-config-private
```

Useful targets:

```bash
make help
make up
make up-otel
make up-mcp
make up-edge
make up-tls
make up-ui
make up-hello
make up-hello-subscription
make up-hello-otel
make up-hello-oidc
make up-hello-oidc-subscription
make up-todo
make up-todo-otel
make smoke-mcp
make smoke-edge
make smoke-tls
make smoke-private
make smoke-hello
make smoke-oidc
make smoke-todo
make test-todo-e2e
make test-todo-bruno
make export-todo-har
make install-hooks
make fmt
make lint
make lint-check
make test
make test-python
make test-shell
make compat
make compat-report
make import-tofu
make verify-azure
make verify-otel
make verify-hello-otel
make verify-todo-otel
make compose-config-hello
make compose-config-hello-otel
make compose-config-hello-oidc
make down
```

Before opening a PR, run:

```bash
make lint-check
make test
```

`make fmt` runs `ruff format .`. `make lint` is the write-fixing alias that formats first and then runs `ruff check .`. `make lint-check` is the non-mutating gate for CI-style local verification. `make test` runs both the Python suite and the shell-script suite under BATS.

The repo-managed pre-commit hook runs `ruff check --fix` and `ruff format` on staged Python files. Enable it once per clone with:

```bash
make install-hooks
```

## Repo Layout

```text
apim-simulator/
├── app/                    # Gateway engine
├── docs/                   # Capability and scope documents
├── examples/
│   ├── basic.json          # Default anonymous local config
│   ├── edge/               # Shared nginx config and generated dev certs
│   ├── hello-api/          # Minimal FastAPI + APIM starter scaffold
│   ├── mcp/                # MCP-focused APIM example config
│   ├── oidc/               # Standalone OIDC config
│   ├── mock-backend/       # Self-contained upstream echo service
│   ├── mcp-server/         # Streamable HTTP MCP example server
│   ├── todo-app/           # Astro + APIM + FastAPI example app
│   └── subnet-calculator/  # Extracted example assets from platform
├── scripts/                # Token and smoke-test helpers
├── tests/                  # Unit and integration tests
├── ui/                     # Operator console (Vite + React + TypeScript)
├── compose.yml             # Internal-only base runtime
├── compose.otel.yml        # LGTM + OTEL overlay for the direct public stack
├── compose.public.yml      # Direct public localhost overlay
├── compose.edge.yml        # Nginx edge HTTP overlay
├── compose.tls.yml         # TLS + redirect overlay
├── compose.private.yml     # Internal-only probe overlay
├── compose.oidc.yml        # Keycloak overlay
├── compose.mcp.yml         # MCP server overlay
├── compose.ui.yml          # Operator console overlay
├── compose.hello.yml       # Hello API starter overlay
├── compose.hello.otel.yml  # Hello API OTEL overlay
├── compose.todo.yml        # Todo demo stack
├── compose.todo.otel.yml   # LGTM + OTEL overlay for the todo demo stack
├── observability/          # Grafana provisioning and local dashboards
└── Makefile
```

## Notes

- Python remains the gateway engine on purpose. The main work here is policy parsing, auth flow simulation, request shaping, and rapid experimentation. That is a good fit for Python and FastAPI.
- Lower-level rewrites are not required for this phase. If the simulator later needs a higher-performance proxy core, Go or Rust would be the next serious options.
