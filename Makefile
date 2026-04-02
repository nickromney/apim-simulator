.DEFAULT_GOAL := help
.RECIPEPREFIX := >

COMPOSE ?= docker compose
COMPOSE_CORE := $(COMPOSE) -f compose.yml
COMPOSE_OIDC := $(COMPOSE) -f compose.yml -f compose.oidc.yml
COMPOSE_MCP := $(COMPOSE) -f compose.yml -f compose.mcp.yml

.PHONY: help up up-oidc up-mcp down logs logs-oidc logs-mcp test smoke-oidc smoke-mcp compose-config compose-config-oidc compose-config-mcp

help:
> @printf "Run:\n"
> @printf "  up               Start the core simulator stack\n"
> @printf "  up-oidc          Start the simulator with the Keycloak overlay\n"
> @printf "  up-mcp           Start the simulator with the MCP example overlay\n"
> @printf "  down             Stop all compose services defined by this repo\n"
> @printf "  logs             Tail core stack logs\n"
> @printf "  logs-oidc        Tail OIDC stack logs\n"
> @printf "  logs-mcp         Tail MCP stack logs\n"
> @printf "  test             Run the Python test suite\n"
> @printf "  smoke-oidc       Run the end-to-end OIDC smoke test against a running stack\n"
> @printf "  smoke-mcp        Run the end-to-end MCP smoke test against a running stack\n"
> @printf "  compose-config   Render docker compose config for the core stack\n"
> @printf "  compose-config-oidc Render docker compose config for the OIDC stack\n"
> @printf "  compose-config-mcp Render docker compose config for the MCP stack\n"

up:
> $(COMPOSE_CORE) up --build -d

up-oidc:
> $(COMPOSE_OIDC) up --build -d

up-mcp:
> $(COMPOSE_MCP) up --build -d

down:
> $(COMPOSE) -f compose.yml -f compose.oidc.yml -f compose.mcp.yml down --remove-orphans

logs:
> $(COMPOSE_CORE) logs -f apim-simulator mock-backend

logs-oidc:
> $(COMPOSE_OIDC) logs -f apim-simulator mock-backend keycloak

logs-mcp:
> $(COMPOSE_MCP) logs -f apim-simulator mcp-server

test:
> uv run --extra dev pytest -q

smoke-oidc:
> uv run python scripts/smoke_oidc.py

smoke-mcp:
> uv run --with mcp python scripts/smoke_mcp.py

compose-config:
> $(COMPOSE_CORE) config

compose-config-oidc:
> $(COMPOSE_OIDC) config

compose-config-mcp:
> $(COMPOSE_MCP) config
