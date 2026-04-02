.DEFAULT_GOAL := help

COMPOSE ?= docker compose
COMPOSE_CORE := $(COMPOSE) -f compose.yml -f compose.public.yml
COMPOSE_OIDC := $(COMPOSE) -f compose.yml -f compose.public.yml -f compose.oidc.yml
COMPOSE_MCP := $(COMPOSE) -f compose.yml -f compose.public.yml -f compose.mcp.yml
COMPOSE_EDGE := $(COMPOSE) -f compose.yml -f compose.edge.yml -f compose.mcp.yml
COMPOSE_TLS := $(COMPOSE) -f compose.yml -f compose.edge.yml -f compose.tls.yml -f compose.mcp.yml
COMPOSE_PRIVATE := $(COMPOSE) -f compose.yml -f compose.private.yml -f compose.mcp.yml
COMPOSE_UI := $(COMPOSE) -f compose.yml -f compose.public.yml -f compose.ui.yml
COMPOSE_ALL := $(COMPOSE) -f compose.yml -f compose.public.yml -f compose.edge.yml -f compose.tls.yml -f compose.private.yml -f compose.ui.yml -f compose.oidc.yml -f compose.mcp.yml
DEV_CERTS := examples/edge/certs/apim.localtest.me.crt examples/edge/certs/apim.localtest.me.key

.PHONY: help ensure-certs up up-oidc up-mcp up-edge up-tls up-ui down logs logs-oidc logs-mcp test compat smoke-oidc smoke-mcp smoke-edge smoke-tls smoke-private compose-config compose-config-oidc compose-config-mcp compose-config-edge compose-config-tls compose-config-private compose-config-ui

help:
	@printf "Run:\n"
	@printf "  up               Start the direct public simulator stack\n"
	@printf "  up-oidc          Start the simulator with the Keycloak overlay\n"
	@printf "  up-mcp           Start the simulator with the MCP example overlay\n"
	@printf "  up-edge          Start the edge HTTP MCP stack on apim.localtest.me:8088\n"
	@printf "  up-tls           Start the edge TLS MCP stack on apim.localtest.me:8443\n"
	@printf "  up-ui            Start the operator console on localhost:3007\n"
	@printf "  down             Stop all compose services defined by this repo\n"
	@printf "  logs             Tail core stack logs\n"
	@printf "  logs-oidc        Tail OIDC stack logs\n"
	@printf "  logs-mcp         Tail MCP stack logs\n"
	@printf "  test             Run the Python test suite\n"
	@printf "  compat           Run the curated APIM sample compatibility harness\n"
	@printf "  smoke-oidc       Run the end-to-end OIDC smoke test against a running stack\n"
	@printf "  smoke-mcp        Run the end-to-end MCP smoke test against a running stack\n"
	@printf "  smoke-edge       Run the edge MCP and forwarded-header smoke test\n"
	@printf "  smoke-tls        Run the TLS edge smoke test using the generated local CA\n"
	@printf "  smoke-private    Run the private-mode smoke test and internal probe\n"
	@printf "  compose-config   Render docker compose config for the direct public stack\n"
	@printf "  compose-config-oidc Render docker compose config for the OIDC stack\n"
	@printf "  compose-config-mcp Render docker compose config for the MCP stack\n"
	@printf "  compose-config-edge Render docker compose config for the edge HTTP stack\n"
	@printf "  compose-config-tls Render docker compose config for the edge TLS stack\n"
	@printf "  compose-config-private Render docker compose config for the private MCP stack\n"
	@printf "  compose-config-ui Render docker compose config for the console stack\n"

ensure-certs: $(DEV_CERTS)

$(DEV_CERTS):
	./scripts/gen_dev_certs.sh

up:
	$(COMPOSE_CORE) up --build -d

up-oidc:
	$(COMPOSE_OIDC) up --build -d

up-mcp:
	$(COMPOSE_MCP) up --build -d

up-edge: ensure-certs
	$(COMPOSE_EDGE) up --build -d

up-tls: ensure-certs
	$(COMPOSE_TLS) up --build -d

up-ui:
	$(COMPOSE_UI) up -d

down:
	$(COMPOSE_ALL) down --remove-orphans

logs:
	$(COMPOSE_CORE) logs -f apim-simulator mock-backend

logs-oidc:
	$(COMPOSE_OIDC) logs -f apim-simulator mock-backend keycloak

logs-mcp:
	$(COMPOSE_MCP) logs -f apim-simulator mcp-server

test:
	uv run --extra dev pytest -q

compat:
	uv run python scripts/check_sample_compat.py

smoke-oidc:
	uv run python scripts/smoke_oidc.py

smoke-mcp:
	uv run --with mcp python scripts/smoke_mcp.py

smoke-edge:
	uv run --with mcp python scripts/smoke_edge.py

smoke-tls:
	SMOKE_EDGE_BASE_URL=https://apim.localtest.me:8443 uv run --with mcp python scripts/smoke_edge.py

smoke-private:
	uv run python -c "import socket; sock = socket.socket(); sock.settimeout(1); code = sock.connect_ex(('127.0.0.1', 8000)); sock.close(); print('Host port 8000 is unavailable, as expected.') if code else (_ for _ in ()).throw(SystemExit('localhost:8000 is reachable; private mode should not publish the gateway port'))"
	$(COMPOSE_PRIVATE) run --rm smoke-runner sh -lc "python -m pip install -q httpx mcp && python scripts/smoke_private.py"

compose-config:
	$(COMPOSE_CORE) config

compose-config-oidc:
	$(COMPOSE_OIDC) config

compose-config-mcp:
	$(COMPOSE_MCP) config

compose-config-edge:
	$(COMPOSE_EDGE) config

compose-config-tls:
	$(COMPOSE_TLS) config

compose-config-private:
	$(COMPOSE_PRIVATE) config

compose-config-ui:
	$(COMPOSE_UI) config
