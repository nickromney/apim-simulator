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
	@printf "  %-22s %s\n" "up" "Start the direct public simulator stack"
	@printf "  %-22s %s\n" "up-oidc" "Start the simulator with the Keycloak overlay"
	@printf "  %-22s %s\n" "up-mcp" "Start the simulator with the MCP example overlay"
	@printf "  %-22s %s\n" "up-edge" "Start the edge HTTP MCP stack on apim.localtest.me:8088"
	@printf "  %-22s %s\n" "up-tls" "Start the edge TLS MCP stack on apim.localtest.me:8443"
	@printf "  %-22s %s\n" "up-ui" "Start the operator console on localhost:3007"
	@printf "  %-22s %s\n" "down" "Stop all compose services defined by this repo"
	@printf "  %-22s %s\n" "logs" "Tail core stack logs"
	@printf "  %-22s %s\n" "logs-oidc" "Tail OIDC stack logs"
	@printf "  %-22s %s\n" "logs-mcp" "Tail MCP stack logs"
	@printf "  %-22s %s\n" "test" "Run the Python test suite"
	@printf "  %-22s %s\n" "compat" "Run the curated APIM sample compatibility harness"
	@printf "  %-22s %s\n" "smoke-oidc" "Run the end-to-end OIDC smoke test against a running stack"
	@printf "  %-22s %s\n" "smoke-mcp" "Run the end-to-end MCP smoke test against a running stack"
	@printf "  %-22s %s\n" "smoke-edge" "Run the edge MCP and forwarded-header smoke test"
	@printf "  %-22s %s\n" "smoke-tls" "Run the TLS edge smoke test using the generated local CA"
	@printf "  %-22s %s\n" "smoke-private" "Run the private-mode smoke test and internal probe"
	@printf "  %-22s %s\n" "compose-config" "Render docker compose config for the direct public stack"
	@printf "  %-22s %s\n" "compose-config-oidc" "Render docker compose config for the OIDC stack"
	@printf "  %-22s %s\n" "compose-config-mcp" "Render docker compose config for the MCP stack"
	@printf "  %-22s %s\n" "compose-config-edge" "Render docker compose config for the edge HTTP stack"
	@printf "  %-22s %s\n" "compose-config-tls" "Render docker compose config for the edge TLS stack"
	@printf "  %-22s %s\n" "compose-config-private" "Render docker compose config for the private MCP stack"
	@printf "  %-22s %s\n" "compose-config-ui" "Render docker compose config for the console stack"

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
