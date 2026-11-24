.DEFAULT_GOAL := help
SHELL := /bin/bash

REPO_ROOT := $(abspath ../..)
COMPOSE ?= docker compose
STACK_SLOT_WIDTH ?= 100
PORT_OFFSET ?= 0
ifneq ($(strip $(STACK_SLOT)),)
PORT_OFFSET := $(shell expr $(STACK_SLOT) \* $(STACK_SLOT_WIDTH))
endif
APIM_MANAGED_PORT ?= $(shell expr 8000 + $(PORT_OFFSET))
APIM_SELF_HOSTED_PORT ?= $(shell expr 8001 + $(PORT_OFFSET))
STACK_SUFFIX := $(if $(STACK_SLOT),-s$(STACK_SLOT),$(if $(filter-out 0,$(PORT_OFFSET)),-o$(PORT_OFFSET)))
COMPOSE_PROJECT := apim-simulator-self-hosted-gateway$(STACK_SUFFIX)
COMPOSE_BASE := $(COMPOSE) --project-directory "$(REPO_ROOT)" --project-name "$(COMPOSE_PROJECT)" -f "$(CURDIR)/compose.yml"
UV_RUN := uv run --project "$(REPO_ROOT)" --extra dev
SMOKE := $(UV_RUN) python "$(CURDIR)/smoke.py" --managed-url "http://127.0.0.1:$(APIM_MANAGED_PORT)" --self-hosted-url "http://127.0.0.1:$(APIM_SELF_HOSTED_PORT)" --compose-project "$(COMPOSE_PROJECT)" --compose-file "$(CURDIR)/compose.yml"
export APIM_MANAGED_PORT APIM_SELF_HOSTED_PORT

.PHONY: help up down logs smoke verify test compose-config

help:
	@echo "Self-hosted gateway: make -C examples/self-hosted-gateway <target> [STACK_SLOT=1]"
	@echo "  up / down / logs    Start or inspect the managed and remote-network services"
	@echo "  smoke               Check configuration pull, placement, local data path and policy sync"
	@echo "  verify              Bring up the stack and run the complete smoke"
	@echo "  test                Run the focused in-process tests"
	@echo "  compose-config      Render the hardened Compose configuration"

up:
	$(COMPOSE_BASE) up --build --wait --remove-orphans

down:
	$(COMPOSE_BASE) down --remove-orphans

logs:
	$(COMPOSE_BASE) logs -f

smoke:
	$(SMOKE)

verify: up smoke

test:
	cd "$(REPO_ROOT)" && $(UV_RUN) pytest tests/test_self_hosted_gateway.py

compose-config:
	$(COMPOSE_BASE) config
