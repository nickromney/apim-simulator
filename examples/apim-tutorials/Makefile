ROOT_DIR := $(abspath ../..)
APIM_BASE ?= http://localhost:8000
APIM_TENANT_KEY ?= local-dev-tenant-key
APIM_UPSTREAM_BASE_URL ?= http://mock-backend:8080/api
BRUNO_VERSION ?= 4.2.0

.PHONY: help smoke bruno
help:
	@echo "make smoke: run all eleven local tutorial workflows"
	@echo "make bruno: run the tutorial 10 API authoring collection"

smoke:
	cd "$(ROOT_DIR)" && ./scripts/run_tutorial_smoke.sh --execute

bruno:
	@env_file="$$(mktemp)"; \
	trap 'rm -f "$$env_file"' EXIT; \
	printf 'vars {\n  apimBaseUrl: %s\n  tenantKey: %s\n  upstreamBaseUrl: %s\n  subscriptionKey: tutorial10-bruno-key\n}\n' "$(APIM_BASE)" "$(APIM_TENANT_KEY)" "$(APIM_UPSTREAM_BASE_URL)" >"$$env_file"; \
	cd bruno && npm exec --yes --package=@usebruno/cli@$(BRUNO_VERSION) -- bru run --env-file "$$env_file" --noproxy --bail .
