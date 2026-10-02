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
STACK_SUFFIX := $(if $(STACK_SLOT),-s$(STACK_SLOT),$(if $(filter-out 0,$(PORT_OFFSET)),-o$(PORT_OFFSET)))
export APIM_GATEWAY_PORT

COMPOSE_BASE := $(COMPOSE) --project-directory "$(REPO_ROOT)" --project-name apim-simulator-patterns-extra$(STACK_SUFFIX) -f "$(CURDIR)/compose.yml"
UV_RUN := uv run --project "$(REPO_ROOT)" --extra dev

.PHONY: help up down logs smoke test compose-config

help:
	@echo "Extra architecture patterns: make -C examples/architecture-patterns-extra <target> [STACK_SLOT=1]"
	@echo "  up / down / logs   Start or inspect the local APIM and backend stack"
	@echo "  smoke              Exercise adapter mapping and isolated stamp failure"
	@echo "  test               Run the focused in-process behavior tests"
	@echo "  compose-config     Render the Compose configuration"

up:
	$(COMPOSE_BASE) up --build --wait --remove-orphans

down:
	$(COMPOSE_BASE) down --remove-orphans

logs:
	$(COMPOSE_BASE) logs -f

smoke:
	$(UV_RUN) python "$(CURDIR)/smoke.py" --base-url "http://127.0.0.1:$(APIM_GATEWAY_PORT)"

test:
	cd "$(REPO_ROOT)" && $(UV_RUN) pytest tests/test_architecture_patterns_extra.py

compose-config:
	$(COMPOSE_BASE) config
