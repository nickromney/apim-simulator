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

# Retain repo-root build contexts and .env while keeping example ownership here.
COMPOSE_BASE := $(COMPOSE) --project-directory "$(REPO_ROOT)" --project-name apim-simulator-bff$(STACK_SUFFIX) -f "$(CURDIR)/compose.yml"
COMPOSE_SERIES := $(COMPOSE_BASE) -f "$(CURDIR)/compose.series.yml"
UV_RUN := uv run --project "$(REPO_ROOT)" --extra dev

.PHONY: help up up-series down logs smoke test compose-config compose-config-series

help:
	@echo "BFF demo: make -C examples/bff <target> [STACK_SLOT=1]"
	@echo "  compose-config / compose-config-series  Render Compose configuration"
	@echo "  down / logs                    Stop either topology or show logs"
	@echo "  smoke / test                   Check running containers or focused tests"
	@echo "  up / up-series                 Start direct or two-gateway topology"

up:
	$(COMPOSE_BASE) up --build --wait --remove-orphans

up-series:
	$(COMPOSE_SERIES) up --build --wait --remove-orphans

down:
	$(COMPOSE_SERIES) down --remove-orphans

logs:
	$(COMPOSE_SERIES) logs -f

smoke:
	$(UV_RUN) python "$(CURDIR)/smoke.py" --base-url "http://127.0.0.1:$(APIM_GATEWAY_PORT)"

test:
	cd "$(REPO_ROOT)" && $(UV_RUN) pytest tests/test_bff_example.py

compose-config:
	$(COMPOSE_BASE) config

compose-config-series:
	$(COMPOSE_SERIES) config
