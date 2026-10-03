ROOT := ../..
export SECURITY_LAB_UID := $(shell id -u)
export SECURITY_LAB_GID := $(shell id -g)
COMPOSE := docker compose -p apim-simulator-security -f compose.yml
.PHONY: prepare up verify down logs
prepare:
	cd $(ROOT) && uv run --extra dev python examples/apim-security/generate_certs.py examples/apim-security/.runtime/certs
	cd $(ROOT) && uv run --extra dev python -m examples.apim-security.bootstrap
	mkdir -p .runtime/state-a .runtime/state-b
up: prepare
	$(COMPOSE) up -d --build --force-recreate
verify:
	cd $(ROOT) && uv run --extra dev python examples/apim-security/verify.py
logs:
	$(COMPOSE) logs --tail=100
down:
	$(COMPOSE) down
