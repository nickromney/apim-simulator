.DEFAULT_GOAL := help
SHELL := /bin/bash

REPO_ROOT := $(abspath ../..)
export CURDIR
COMPOSE ?= docker compose
STACK_SLOT_WIDTH ?= 100
PORT_OFFSET ?= 0
ifneq ($(strip $(STACK_SLOT)),)
PORT_OFFSET := $(shell expr $(STACK_SLOT) \* $(STACK_SLOT_WIDTH))
endif
APIM_GATEWAY_PORT ?= $(shell expr 8000 + $(PORT_OFFSET))
STACK_SUFFIX := $(if $(STACK_SLOT),-s$(STACK_SLOT),$(if $(filter-out 0,$(PORT_OFFSET)),-o$(PORT_OFFSET)))
export APIM_GATEWAY_PORT
COMPOSE_BASE := $(COMPOSE) --project-directory "$(REPO_ROOT)" --project-name apim-simulator-patterns$(STACK_SUFFIX) -f "$(CURDIR)/compose.yml"
UV_RUN := uv run --project "$(REPO_ROOT)" --extra dev
AZURE_TARGET_ARGS := --subscription-id "$(AZURE_SUBSCRIPTION_ID)" --resource-group "$(AZURE_RESOURCE_GROUP)" --service-name "$(AZURE_APIM_SERVICE)"
AZURE_GATEWAY_ARG := $(if $(AZURE_APIM_BASE_URL),--gateway-url "$(AZURE_APIM_BASE_URL)")
AZURE_REPORT_ARG := $(if $(AZURE_REPORT),--report "$(AZURE_REPORT)")

.PHONY: help up down logs smoke compose-config azure-plan azure-validate azure-compare

help:
	@echo "Architecture patterns demo: make -C examples/architecture-patterns <target> [STACK_SLOT=1]"
	@echo "  up / down / logs      Start, stop, or follow the local stack"
	@echo "  smoke                 Verify routes, policy offloading, fan-out, and gatekeeper"
	@echo "  compose-config        Render Compose configuration"
	@echo "  azure-plan            Check comparison inputs without calling Azure"
	@echo "  azure-validate        Validate temporary policies on existing APIM, then clean up"
	@echo "  azure-compare         Compare local and reachable Azure gateways, then clean up"
	@echo "Azure targets require AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZURE_APIM_SERVICE."

up:
	$(COMPOSE_BASE) up --build --wait --remove-orphans

down:
	$(COMPOSE_BASE) down --remove-orphans

logs:
	$(COMPOSE_BASE) logs -f

smoke:
	$(UV_RUN) python "$(CURDIR)/smoke.py" --base-url "http://127.0.0.1:$(APIM_GATEWAY_PORT)"

compose-config:
	$(COMPOSE_BASE) config

azure-plan:
	$(UV_RUN) python "$(CURDIR)/azure_compare.py"

azure-validate:
	$(UV_RUN) python "$(CURDIR)/azure_compare.py" --execute --validate-only $(AZURE_TARGET_ARGS) $(AZURE_GATEWAY_ARG) $(AZURE_REPORT_ARG)

azure-compare:
	$(UV_RUN) python "$(CURDIR)/azure_compare.py" --execute $(AZURE_TARGET_ARGS) $(AZURE_GATEWAY_ARG) $(AZURE_REPORT_ARG) --simulator-base-url "http://127.0.0.1:$(APIM_GATEWAY_PORT)"
