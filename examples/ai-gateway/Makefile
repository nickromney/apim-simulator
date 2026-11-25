.DEFAULT_GOAL := help
SHELL := /bin/bash

REPO_ROOT := $(abspath ../..)
COMPOSE ?= docker compose
STACK_SLOT_WIDTH ?= 100
PORT_OFFSET ?= 0
ifneq ($(strip $(STACK_SLOT)),)
PORT_OFFSET := $(shell expr $(STACK_SLOT) \* $(STACK_SLOT_WIDTH))
endif
APIM_GATEWAY_PORT ?= $(shell expr 8000 + $(PORT_OFFSET))
FOUNDRY_PORT ?= 8020
STACK_INSTANCE_SUFFIX := $(strip $(if $(STACK_SLOT),s$(STACK_SLOT),$(if $(filter-out 0,$(PORT_OFFSET)),o$(PORT_OFFSET),)))
export APIM_GATEWAY_PORT

# Same Compose project as the root `make up-ai-foundry`, so the keyless and
# key-based gateways replace each other instead of both answering to the
# `apim-simulator` name on the shared `aifoundry` network.
COMPOSE_FOUNDRY_KEYLESS := $(COMPOSE) --project-directory "$(REPO_ROOT)" \
	$(if $(STACK_INSTANCE_SUFFIX),--project-name apim-simulator-ai-foundry-$(STACK_INSTANCE_SUFFIX)) \
	-f "$(REPO_ROOT)/compose.yml" -f "$(REPO_ROOT)/compose.public.yml" -f "$(REPO_ROOT)/compose.ai-foundry.yml" \
	-f "$(CURDIR)/compose.foundry-keyless.yml"
UV_RUN := uv run --project "$(REPO_ROOT)" --extra dev
KEY_DIR := $(CURDIR)/.runtime/workload-identity

.PHONY: help keypair up-foundry-keyless down-foundry-keyless logs-foundry-keyless smoke-foundry-keyless compose-config-foundry-keyless

help:
	@echo "AI gateway, keyless Foundry variant: make -C examples/ai-gateway <target> [STACK_SLOT=1]"
	@echo "  keypair                         Generate the gateway's local signing key (kept if present)"
	@echo "  up-foundry-keyless              Start the gateway calling AI Foundry with its managed identity"
	@echo "  smoke-foundry-keyless           Check chat, JWKS and token auth through published ports"
	@echo "  down-foundry-keyless / logs-foundry-keyless   Stop or follow the gateway"
	@echo "  compose-config-foundry-keyless  Render the Compose configuration"
	@echo "Start the sibling aifoundry-simulator first with 'make up-keyless' in its checkout."

keypair:
	cd "$(REPO_ROOT)" && $(UV_RUN) python "$(CURDIR)/generate_workload_key.py" "$(KEY_DIR)"

up-foundry-keyless: keypair
	@$(MAKE) --no-print-directory -C "$(REPO_ROOT)" check-aifoundry-network
	$(COMPOSE_FOUNDRY_KEYLESS) up --build -d

down-foundry-keyless:
	$(COMPOSE_FOUNDRY_KEYLESS) down --remove-orphans

logs-foundry-keyless:
	$(COMPOSE_FOUNDRY_KEYLESS) logs -f apim-simulator

smoke-foundry-keyless:
	cd "$(REPO_ROOT)" && $(UV_RUN) python "$(CURDIR)/smoke_foundry_keyless.py" \
		--gateway-url "http://127.0.0.1:$(APIM_GATEWAY_PORT)" --foundry-url "http://127.0.0.1:$(FOUNDRY_PORT)"

compose-config-foundry-keyless:
	$(COMPOSE_FOUNDRY_KEYLESS) config
