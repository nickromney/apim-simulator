ROOT_DIR := $(abspath ../..)
COMPOSE_PROJECT_NAME ?= apim-simulator-policy-lab
APIM_GATEWAY_PORT ?= 8900
POLICY_BROKER_PORT ?= 8901
APIM_BASE ?= http://localhost:$(APIM_GATEWAY_PORT)
BROKER_BASE ?= http://localhost:$(POLICY_BROKER_PORT)
APIM_TENANT_KEY ?= local-dev-tenant-key
POLICY_VAULT_READER_KEY ?= local-vault-reader
POLICY_BROKER_ADMIN_KEY ?= local-broker-admin
POLICY_BROKER_SENDER_KEY ?= local-broker-sender
COMPOSE := docker compose --project-name $(COMPOSE_PROJECT_NAME) -f $(ROOT_DIR)/compose.yml -f $(ROOT_DIR)/compose.public.yml -f $(ROOT_DIR)/examples/apim-policies/compose.yml
export POLICY_VAULT_READER_KEY APIM_GATEWAY_PORT POLICY_BROKER_PORT POLICY_BROKER_ADMIN_KEY POLICY_BROKER_SENDER_KEY
.PHONY: help up down verify smoke config
help:
	@echo "make up / verify / smoke / down: local policy guide lab with pub/sub broker"
up:
	$(COMPOSE) up --build -d
verify:
	cd $(ROOT_DIR) && APIM_BASE="$(APIM_BASE)" BROKER_BASE="$(BROKER_BASE)" APIM_TENANT_KEY="$(APIM_TENANT_KEY)" uv run --extra dev python examples/apim-policies/verify.py
smoke: up verify
config:
	$(COMPOSE) config --quiet
down:
	$(COMPOSE) down --remove-orphans
