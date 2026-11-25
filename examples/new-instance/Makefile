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
APIM_CONFIG_SOURCE_PATH := /app/examples/new-instance/apim.json
APIM_ALLOWED_ORIGIN_GATEWAY ?= http://localhost:$(APIM_GATEWAY_PORT)
export APIM_GATEWAY_PORT APIM_CONFIG_SOURCE_PATH APIM_ALLOWED_ORIGIN_GATEWAY

COMPOSE_BASE := $(COMPOSE) --project-directory "$(REPO_ROOT)" --project-name apim-simulator-new-instance$(STACK_SUFFIX) \
	-f "$(REPO_ROOT)/compose.yml" -f "$(REPO_ROOT)/compose.public.yml"
UV_RUN := uv run --project "$(REPO_ROOT)" --extra dev
SMOKE := $(UV_RUN) python "$(CURDIR)/smoke.py" --gateway-url "http://127.0.0.1:$(APIM_GATEWAY_PORT)"

.PHONY: help up down logs smoke verify test compose-config

help:
	@echo "New instance: make -C examples/new-instance <target> [STACK_SLOT=1]"
	@echo "  up / down / logs   Start or inspect a gateway loaded with a new Azure APIM instance's defaults"
	@echo "  smoke              Check keys, Starter limits, portal sign-up and approval through published ports"
	@echo "  verify             up, then smoke"
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

verify: up smoke

test:
	cd "$(REPO_ROOT)" && $(UV_RUN) pytest tests/test_new_instance_example.py

compose-config:
	$(COMPOSE_BASE) config
