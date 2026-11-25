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
APIM_WESTEUROPE_PORT ?= $(shell expr 8001 + $(PORT_OFFSET))
APIM_EASTUS_PORT ?= $(shell expr 8002 + $(PORT_OFFSET))
STACK_SUFFIX := $(if $(STACK_SLOT),-s$(STACK_SLOT),$(if $(filter-out 0,$(PORT_OFFSET)),-o$(PORT_OFFSET)))
export APIM_GATEWAY_PORT APIM_WESTEUROPE_PORT APIM_EASTUS_PORT

COMPOSE_BASE := $(COMPOSE) --project-directory "$(REPO_ROOT)" --project-name apim-simulator-regional-gateways$(STACK_SUFFIX) -f "$(CURDIR)/compose.yml"
UV_RUN := uv run --project "$(REPO_ROOT)" --extra dev
SMOKE := $(UV_RUN) python "$(CURDIR)/smoke.py" --global-url "http://127.0.0.1:$(APIM_GATEWAY_PORT)" \
	--westeurope-url "http://127.0.0.1:$(APIM_WESTEUROPE_PORT)" --eastus-url "http://127.0.0.1:$(APIM_EASTUS_PORT)"

.PHONY: help up down logs smoke failover verify test compose-config

help:
	@echo "Regional gateways: make -C examples/regional-gateways <target> [STACK_SLOT=1]"
	@echo "  up / down / logs   Start or inspect two regional gateways, their backends and the global URL"
	@echo "  smoke              Check regional routing and backend failover through published ports"
	@echo "  failover           Stop West Europe, check the global URL moves to East US, restart it and check it returns"
	@echo "  verify             up, smoke and failover"
	@echo "  test               Run the focused in-process tests"
	@echo "  compose-config     Render the Compose configuration"

up:
	$(COMPOSE_BASE) up --build --wait --remove-orphans

down:
	$(COMPOSE_BASE) down --remove-orphans

logs:
	$(COMPOSE_BASE) logs -f

smoke:
	$(SMOKE)

failover:
	$(COMPOSE_BASE) stop gateway-westeurope
	$(SMOKE) --expect-global-region "East US" --wait-seconds 15; status=$$?; \
	$(COMPOSE_BASE) up --wait gateway-westeurope || exit 1; \
	[ $$status -eq 0 ] || exit $$status; \
	$(SMOKE) --expect-global-region "West Europe" --wait-seconds 30

verify: up smoke failover

test:
	cd "$(REPO_ROOT)" && $(UV_RUN) pytest tests/test_regional_gateways_example.py tests/test_policy_retry.py

compose-config:
	$(COMPOSE_BASE) config
