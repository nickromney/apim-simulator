# Local APIM Simulator

This repository is the standalone extraction of the APIM simulator work that started in `platform/apps/subnet-calculator`. The goal here is narrower and cleaner: a Docker-first Azure API Management lab that can evolve independently of the broader platform stack.

## Current Shape

- Python/FastAPI gateway engine for fast iteration and readable policy/auth logic
- Config-driven routing, products, subscriptions, OIDC, traces, and a subset of APIM XML policies
- Standalone Docker Compose entrypoints for a core anonymous stack and an OIDC-enabled Keycloak stack
- An MCP-focused example that places the simulator in front of a streamable HTTP MCP server
- Example assets from the original subnet-calculator stack kept under `examples/subnet-calculator/`

## Quick Start

### Core stack

```bash
docker compose up --build -d
curl http://localhost:8000/apim/health
curl http://localhost:8000/api/echo
```

Ports:

- `8000` - APIM simulator
- `8081` - mock upstream backend

### OIDC stack

```bash
make up-oidc
uv run python scripts/smoke_oidc.py
```

Fetch a token manually if you want to inspect the flow:

```bash
TOKEN=$(uv run python scripts/get_keycloak_token.py)

curl \
  -H "Authorization: Bearer $TOKEN" \
  -H "Ocp-Apim-Subscription-Key: oidc-demo-key" \
  http://localhost:8000/api/echo
```

### MCP stack

```bash
make up-mcp
uv run --with mcp python scripts/smoke_mcp.py
```

This starts a minimal streamable HTTP MCP server behind the simulator and exercises it through `http://localhost:8000/mcp` with an APIM subscription key.

## Development

```bash
uv run --extra dev pytest -q
docker compose config
```

Useful targets:

```bash
make help
make up
make up-oidc
make up-mcp
make test
```

## Repo Layout

```text
apim-simulator/
├── app/                    # Gateway engine
├── docs/                   # Capability and scope documents
├── examples/
│   ├── basic.json          # Default anonymous local config
│   ├── mcp/                # MCP-focused APIM example config
│   ├── oidc/               # Standalone OIDC config
│   ├── mock-backend/       # Self-contained upstream echo service
│   ├── mcp-server/         # Streamable HTTP MCP example server
│   └── subnet-calculator/  # Extracted example assets from platform
├── scripts/                # Token and smoke-test helpers
├── tests/                  # Unit and integration tests
├── compose.yml             # Core local runtime
├── compose.oidc.yml        # Keycloak overlay
├── compose.mcp.yml         # MCP server overlay
└── Makefile
```

## Notes

- Python remains the gateway engine on purpose. The main work here is policy parsing, auth flow simulation, request shaping, and rapid experimentation. That is a good fit for Python and FastAPI.
- Lower-level rewrites are not required for this phase. If the simulator later needs a higher-performance proxy core, Go or Rust would be the next serious options.
