.DEFAULT_GOAL := help
SHELL := /bin/bash
REPO_ROOT := $(abspath ../..)
STACK_SLOT_WIDTH ?= 100
PORT_OFFSET ?= 0
ifneq ($(strip $(STACK_SLOT)),)
PORT_OFFSET := $(shell expr $(STACK_SLOT) \* $(STACK_SLOT_WIDTH))
endif
APIM_GATEWAY_PORT ?= $(shell expr 8000 + $(PORT_OFFSET))
APIM_BASE_URL ?= http://127.0.0.1:$(APIM_GATEWAY_PORT)
STACK_SUFFIX := $(if $(STACK_SLOT),-s$(STACK_SLOT),$(if $(filter-out 0,$(PORT_OFFSET)),-o$(PORT_OFFSET)))
export APIM_GATEWAY_PORT
COMPOSE := docker compose --project-directory "$(REPO_ROOT)" --project-name apim-simulator-scalar$(STACK_SUFFIX) -f "$(REPO_ROOT)/compose.yml" -f "$(REPO_ROOT)/compose.public.yml" -f "$(CURDIR)/compose.scalar.yml"

.PHONY: help up down smoke compose-config
help:
	@echo "Scalar portal lab: make -C examples/portal up / smoke / down [STACK_SLOT=1]"
up:
	$(COMPOSE) up --build --wait
down:
	$(COMPOSE) down --remove-orphans
compose-config:
	$(COMPOSE) config
smoke:
	npm ci
	APIM_BASE_URL="$(APIM_BASE_URL)" npm run smoke
