# Local APIM Simulator

This repository is the standalone extraction of the APIM simulator work that started in `platform/apps/subnet-calculator`. The goal here is narrower and cleaner: a Docker-first Azure API Management lab that can evolve independently of the broader platform stack.

## Current Shape

- Python/FastAPI gateway engine for fast iteration and readable policy/auth logic
- Config-driven routing, products, subscriptions, tenant access keys, OIDC, traces, and a practical APIM XML policy subset
- Policy-driven response caching, value caching, and by-key throttling for local APIM-shaped gateway flows
- Terraform/OpenTofu import of APIM APIs, version sets, backends, named values, policies, and OpenAPI-derived operations
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
make fmt
make lint
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
make up-mcp
make up-edge
make up-tls
make up-ui
make smoke-mcp
make smoke-edge
make smoke-tls
make smoke-private
make smoke-oidc
make install-hooks
make fmt
make lint
make test
make compat
make compat-report
make import-tofu
make verify-azure
make down
```

`make fmt` runs `ruff format .`. `make lint` is the local alias that formats first and then runs `ruff check .`.

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
│   ├── mcp/                # MCP-focused APIM example config
│   ├── oidc/               # Standalone OIDC config
│   ├── mock-backend/       # Self-contained upstream echo service
│   ├── mcp-server/         # Streamable HTTP MCP example server
│   └── subnet-calculator/  # Extracted example assets from platform
├── scripts/                # Token and smoke-test helpers
├── tests/                  # Unit and integration tests
├── ui/                     # Operator console (Vite + React + TypeScript)
├── compose.yml             # Internal-only base runtime
├── compose.public.yml      # Direct public localhost overlay
├── compose.edge.yml        # Nginx edge HTTP overlay
├── compose.tls.yml         # TLS + redirect overlay
├── compose.private.yml     # Internal-only probe overlay
├── compose.oidc.yml        # Keycloak overlay
├── compose.mcp.yml         # MCP server overlay
├── compose.ui.yml          # Operator console overlay
└── Makefile
```

## Notes

- Python remains the gateway engine on purpose. The main work here is policy parsing, auth flow simulation, request shaping, and rapid experimentation. That is a good fit for Python and FastAPI.
- Lower-level rewrites are not required for this phase. If the simulator later needs a higher-performance proxy core, Go or Rust would be the next serious options.
