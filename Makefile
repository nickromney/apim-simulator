.DEFAULT_GOAL := help

# Recipes are POSIX today, so this changes nothing now -- it is here to stop the
# defect the sibling platform repo hit in its PR #197. `SHELL ?= /bin/bash` there
# was a silent no-op, because GNU make always has SHELL defined and `?=` can
# never fire, so every recipe ran under /bin/sh: bash on Arch, dash on
# ubuntu-latest. The first recipe to use `set -o pipefail` or `[[ ]]` passed
# locally and died on CI with "Illegal option -o pipefail". Pin it with `:=`
# before that can happen, not after.
SHELL := /bin/bash

COMPOSE ?= docker compose
STACK_SLOT_WIDTH ?= 100
PORT_OFFSET ?= 0
ifneq ($(strip $(STACK_SLOT)),)
PORT_OFFSET := $(shell expr $(STACK_SLOT) \* $(STACK_SLOT_WIDTH))
endif

calc_port = $(shell expr $(1) + $(PORT_OFFSET))
url_port_suffix = $(if $(filter 80 443,$(1)),,:$(1))
STACK_INSTANCE_SUFFIX := $(strip $(if $(STACK_SLOT),s$(STACK_SLOT),$(if $(filter-out 0,$(PORT_OFFSET)),o$(PORT_OFFSET),)))
compose_project_args = $(if $(STACK_INSTANCE_SUFFIX),--project-name apim-simulator-$(1)-$(STACK_INSTANCE_SUFFIX))
compose_stack = $(COMPOSE) $(call compose_project_args,$(1))

APIM_GATEWAY_PORT ?= $(call calc_port,8000)
APIM_SERVING_GATEWAY_PORT ?= $(call calc_port,8001)
GRAFANA_HOST ?= lgtm.apim.127.0.0.1.sslip.io
GRAFANA_PORT ?= $(call calc_port,8443)
OTEL_GRPC_PORT ?= $(call calc_port,4317)
OTEL_HTTP_PORT ?= $(call calc_port,4318)
KEYCLOAK_PORT ?= $(call calc_port,8180)
OPERATOR_CONSOLE_PORT ?= $(call calc_port,3007)
EDGE_HTTP_PORT ?= $(call calc_port,8088)
EDGE_TLS_HTTP_PORT ?= $(call calc_port,8080)
EDGE_TLS_PORT ?= $(call calc_port,9443)
# Give each port offset a distinct subnet for the trusted edge proxy.
APIM_EDGE_PROXY_NETWORK_PREFIX ?= $(shell offset=$(PORT_OFFSET); printf '10.%s.%s' $$((247 - offset / 256)) $$((offset % 256)))
AWS_GATEWAY_PORT ?= $(call calc_port,4566)
TODO_FRONTEND_PORT ?= $(call calc_port,3000)
VITE_DEV_PORT ?= 5173
APIM_EDGE_ROOT_HOST ?= apim.127.0.0.1.sslip.io
APIM_EDGE_HOST ?= edge.apim.127.0.0.1.sslip.io
APIM_EDGE_WILDCARD_HOST ?= *.apim.127.0.0.1.sslip.io

APIM_BASE_URL ?= http://localhost:$(APIM_GATEWAY_PORT)
APIM_LOOPBACK_BASE_URL ?= http://127.0.0.1:$(APIM_GATEWAY_PORT)
GRAFANA_BASE_URL ?= https://$(GRAFANA_HOST)$(call url_port_suffix,$(GRAFANA_PORT))
KEYCLOAK_BASE_URL ?= http://localhost:$(KEYCLOAK_PORT)
OIDC_ISSUER_EXTERNAL ?= $(KEYCLOAK_BASE_URL)/realms/subnet-calculator
OPERATOR_CONSOLE_URL ?= http://localhost:$(OPERATOR_CONSOLE_PORT)
TODO_FRONTEND_BASE_URL ?= http://127.0.0.1:$(TODO_FRONTEND_PORT)
TODO_FRONTEND_BROWSER_URL ?= http://localhost:$(TODO_FRONTEND_PORT)
TODO_FRONTEND_ORIGIN_LOCALHOST ?= http://localhost:$(TODO_FRONTEND_PORT)
TODO_FRONTEND_ORIGIN_LOOPBACK ?= http://127.0.0.1:$(TODO_FRONTEND_PORT)
TODO_APIM_BASE_URL ?= $(APIM_LOOPBACK_BASE_URL)
TODO_APIM_PUBLIC_BASE_URL ?= $(APIM_BASE_URL)
TODO_GRAFANA_BASE_URL ?= $(GRAFANA_BASE_URL)
TODO_OBSERVABILITY_DASHBOARD_URL ?= $(GRAFANA_BASE_URL)/d/apim-simulator-overview/apim-simulator-overview
APIM_ALLOWED_ORIGIN_BROWSER_LOCALHOST ?= $(TODO_FRONTEND_ORIGIN_LOCALHOST)
APIM_ALLOWED_ORIGIN_OPERATOR_CONSOLE ?= $(OPERATOR_CONSOLE_URL)
APIM_ALLOWED_ORIGIN_VITE ?= http://localhost:$(VITE_DEV_PORT)
APIM_ALLOWED_ORIGIN_GATEWAY ?= $(APIM_BASE_URL)
EDGE_HTTP_BASE_URL ?= http://$(APIM_EDGE_HOST):$(EDGE_HTTP_PORT)
EDGE_TLS_BASE_URL ?= https://$(APIM_EDGE_HOST):$(EDGE_TLS_PORT)
SMOKE_HELLO_BASE_URL ?= $(APIM_LOOPBACK_BASE_URL)
SMOKE_HELLO_KEYCLOAK_BASE_URL ?= $(KEYCLOAK_BASE_URL)
SMOKE_OIDC_BASE_URL ?= $(APIM_LOOPBACK_BASE_URL)
SMOKE_OIDC_KEYCLOAK_BASE_URL ?= $(KEYCLOAK_BASE_URL)
SMOKE_MCP_URL ?= $(APIM_BASE_URL)/mcp
SMOKE_EDGE_BASE_URL ?= $(EDGE_HTTP_BASE_URL)
SMOKE_AI_BASE_URL ?= $(APIM_LOOPBACK_BASE_URL)
SMOKE_AI_FOUNDRY_BASE_URL ?= $(APIM_LOOPBACK_BASE_URL)
SMOKE_SHARED_BASE_URL ?= $(APIM_LOOPBACK_BASE_URL)
SMOKE_SHARED_KEYCLOAK_BASE_URL ?= $(KEYCLOAK_BASE_URL)
SMOKE_AWS_BASE_URL ?= http://127.0.0.1:$(AWS_GATEWAY_PORT)
SMOKE_MANAGEMENT_GATEWAY_BASE_URL ?= $(APIM_LOOPBACK_BASE_URL)
SMOKE_SERVING_GATEWAY_BASE_URL ?= http://127.0.0.1:$(APIM_SERVING_GATEWAY_PORT)
PORTS ?= $(TODO_FRONTEND_PORT) $(GRAFANA_PORT) $(OTEL_GRPC_PORT) $(OTEL_HTTP_PORT) $(APIM_GATEWAY_PORT) $(APIM_SERVING_GATEWAY_PORT) $(EDGE_HTTP_PORT) $(EDGE_TLS_HTTP_PORT) $(EDGE_TLS_PORT) $(OPERATOR_CONSOLE_PORT) $(KEYCLOAK_PORT)
UP_ALL_SLOT_BASE ?= 0
UP_ALL_STACKS := up up-otel up-oidc up-mcp up-edge up-tls up-private up-hello up-hello-subscription up-hello-otel up-hello-oidc up-hello-oidc-subscription up-ai up-shared up-todo up-todo-otel

export STACK_SLOT STACK_SLOT_WIDTH PORT_OFFSET
export APIM_GATEWAY_PORT APIM_SERVING_GATEWAY_PORT GRAFANA_PORT OTEL_GRPC_PORT OTEL_HTTP_PORT KEYCLOAK_PORT OPERATOR_CONSOLE_PORT EDGE_HTTP_PORT EDGE_TLS_HTTP_PORT EDGE_TLS_PORT AWS_GATEWAY_PORT TODO_FRONTEND_PORT VITE_DEV_PORT
export APIM_EDGE_ROOT_HOST APIM_EDGE_HOST APIM_EDGE_WILDCARD_HOST GRAFANA_HOST
export APIM_EDGE_PROXY_NETWORK_PREFIX
export APIM_BASE_URL APIM_LOOPBACK_BASE_URL GRAFANA_BASE_URL KEYCLOAK_BASE_URL OIDC_ISSUER_EXTERNAL OPERATOR_CONSOLE_URL
export TODO_FRONTEND_BASE_URL TODO_FRONTEND_BROWSER_URL TODO_FRONTEND_ORIGIN_LOCALHOST TODO_FRONTEND_ORIGIN_LOOPBACK TODO_APIM_BASE_URL TODO_APIM_PUBLIC_BASE_URL TODO_GRAFANA_BASE_URL TODO_OBSERVABILITY_DASHBOARD_URL
export APIM_ALLOWED_ORIGIN_BROWSER_LOCALHOST APIM_ALLOWED_ORIGIN_OPERATOR_CONSOLE APIM_ALLOWED_ORIGIN_VITE APIM_ALLOWED_ORIGIN_GATEWAY
export EDGE_HTTP_BASE_URL EDGE_TLS_BASE_URL
export SMOKE_HELLO_BASE_URL SMOKE_HELLO_KEYCLOAK_BASE_URL SMOKE_OIDC_BASE_URL SMOKE_OIDC_KEYCLOAK_BASE_URL SMOKE_MCP_URL SMOKE_EDGE_BASE_URL SMOKE_AI_BASE_URL SMOKE_AI_FOUNDRY_BASE_URL SMOKE_SHARED_BASE_URL SMOKE_SHARED_KEYCLOAK_BASE_URL SMOKE_AWS_BASE_URL SMOKE_MANAGEMENT_GATEWAY_BASE_URL SMOKE_SERVING_GATEWAY_BASE_URL

# Root compose stacks, one row each. Every stack gets up-<name>,
# logs-<name> and compose-config-<name>; the core stack's are plain up, logs
# and compose-config. A row sets:
#   <name>.files    compose files, in order
#   <name>.project  compose project name, if not <name>
#   <name>.needs    prerequisites of up-<name>
#   <name>.env      environment for up-<name>
#   <name>.logs     services logs-<name> follows (all when unset)
#   <name>.about    help text for up-<name>
# STACKS is also the order `make down` stops them in.
STACKS := core otel oidc mcp edge tls private hello hello-otel hello-oidc ai ai-foundry shared aws management-gateway todo todo-otel

core.files := compose.yml compose.public.yml compose.ui.yml
core.logs := apim-simulator mock-backend
core.about = Start the direct public simulator stack
otel.files := compose.yml compose.public.yml compose.otel.yml
otel.project := core-otel
otel.needs := ensure-certs
otel.logs := apim-simulator mock-backend lgtm lgtm-proxy
otel.about = Start the direct public simulator stack with LGTM at $(GRAFANA_BASE_URL)
oidc.files := compose.yml compose.public.yml compose.oidc.yml
oidc.logs := apim-simulator mock-backend keycloak
oidc.about = Start the simulator with the Keycloak overlay
mcp.files := compose.yml compose.public.yml compose.mcp.yml
mcp.logs := apim-simulator mcp-server
mcp.about = Start the simulator with the MCP example overlay
edge.files := compose.yml compose.edge.yml compose.mcp.yml
edge.needs := ensure-certs
edge.about = Start the edge HTTP MCP stack on $(APIM_EDGE_HOST):8088
tls.files := compose.yml compose.edge.yml compose.tls.yml compose.mcp.yml
tls.needs := ensure-certs
tls.about = Start the edge TLS MCP stack on $(APIM_EDGE_HOST):9443
private.files := compose.yml compose.private.yml compose.mcp.yml
private.logs := apim-simulator mcp-server smoke-runner
private.about = Start the private MCP stack without publishing the gateway host port
hello.files := compose.yml compose.public.yml compose.hello.yml
hello.logs := apim-simulator hello-api
hello.about = Start the anonymous hello API example behind APIM
hello-otel.files := compose.yml compose.public.yml compose.hello.yml compose.otel.yml compose.hello.otel.yml
hello-otel.needs := ensure-certs
hello-otel.logs := apim-simulator hello-api lgtm lgtm-proxy
hello-otel.about = Start the hello API example with LGTM
hello-oidc.files := compose.yml compose.public.yml compose.oidc.yml compose.hello.yml
hello-oidc.env := HELLO_APIM_CONFIG_PATH=/app/examples/hello-api/apim.oidc.jwt-only.json
hello-oidc.logs := apim-simulator hello-api keycloak
hello-oidc.about = Start the JWT-only hello API example with Keycloak
ai.files := compose.yml compose.public.yml compose.ai.yml
ai.logs := apim-simulator llm-backend
ai.about = Start the AI gateway example with the mock LLM backend
ai-foundry.files := compose.yml compose.public.yml compose.ai-foundry.yml
ai-foundry.needs := check-aifoundry-network
ai-foundry.logs := apim-simulator
ai-foundry.about = Start the gateway fronting the sibling AI Foundry simulator (start that first)
shared.files := compose.yml compose.public.yml compose.oidc.yml compose.shared.yml
shared.logs := apim-simulator keycloak
shared.about = Start the shared gateway RBAC example with Keycloak workload identities
aws.files := compose.yml compose.public.yml compose.aws.yml
aws.logs := localstack mock-backend
aws.about = Start LocalStack AWS API Gateway comparison beside the simulator stack
management-gateway.files := compose.yml compose.public.yml compose.management-gateway.yml
management-gateway.logs := apim-simulator apim-serving mock-backend
management-gateway.about = Start a management gateway routing to a self-hosted serving gateway
todo.files := compose.todo.yml
todo.logs := todo-frontend apim-simulator todo-api
todo.about = Start the Astro + APIM + FastAPI todo demo stack
todo-otel.files := compose.todo.yml compose.todo.otel.yml
todo-otel.needs := ensure-certs
todo-otel.logs := todo-frontend apim-simulator todo-api lgtm lgtm-proxy
todo-otel.about = Start the todo demo stack with LGTM at $(GRAFANA_BASE_URL)

# $(call compose_for,<stack>): the docker compose command for a stack.
compose_for = $(call compose_stack,$(or $($(1).project),$(1))) $(addprefix -f ,$($(1).files))
# $(call stack_target,<verb>,<stack>): up-mcp, logs-mcp; plain up for core.
stack_target = $(if $(filter core,$(2)),$(1),$(1)-$(2))
STACK_TARGETS := $(foreach verb,up logs compose-config,$(foreach s,$(STACKS),$(call stack_target,$(verb),$(s))))
define newline


endef

DEV_CERTS := examples/edge/certs/$(APIM_EDGE_HOST).crt examples/edge/certs/$(APIM_EDGE_HOST).key
HELP_FMT := "  %-34s %s\n"
UV_RUN := uv run --project $(CURDIR)
LINT_YAML_SCRIPT ?= scripts/lint-yaml.sh
LINT_MARKDOWN_SCRIPT ?= scripts/lint-markdown.sh
LINT_BASH32_SCRIPT ?= scripts/check-bash32-compat.sh
AUDIT_SHELL_SCRIPTS_SCRIPT ?= scripts/audit-shell-scripts.sh
CHECK_VERSION_SCRIPT ?= scripts/check-version.sh
RELEASE_SCRIPT ?= scripts/release.sh
RELEASE_TAG_SCRIPT ?= scripts/release_tag.sh

.PHONY: examples

.PHONY: help ensure-certs hooks install-hooks local-ci fmt lint lint-check lint-yaml lint-markdown lint-bash32 lint-shell frontend-check check-version runtime-artifact release release-dry-run release-preview release-tag release-tag-dry-run test test-python test-shell test-terraform-provider compat compat-report import-tofu verify verify-dry-run verify-test verify-azure verify-otel verify-hello-otel verify-todo-otel check-host-ports check-private-port-clear smoke-mcp smoke-edge smoke-tls smoke-private smoke-ai-foundry-keyless smoke-management-gateway smoke-tutorials-live test-todo-e2e test-todo-bruno test-todo-postman export-todo-har complexity complexity-report lint-shellcheck mutation mutation-baseline mutation-clean mutation-gate mutation-results mutation-show

help:
	@echo "Local APIM Simulator"
	@echo ""
	@echo "Start here: make doctor, then make up    Stop stacks: make down"
	@echo ""
	@printf $(HELP_FMT) "make examples" "Choose a demo and see how to run it"
	@printf $(HELP_FMT) "make help-all" "Show the complete command reference"
	@printf $(HELP_FMT) "make help-config" "Inspect Compose configurations"
	@printf $(HELP_FMT) "make help-dev" "Setup, tests, lint, and compatibility tools"
	@printf $(HELP_FMT) "make help-release" "Packaging and releases"
	@printf $(HELP_FMT) "make help-stacks" "Start, stop, isolate stacks, and view logs"
	@printf $(HELP_FMT) "make help-verify" "Smoke tests and live verification"
	@echo ""
	@echo "BFF demo: make -C examples/bff help"
	@echo "APIM tutorials: make -C examples/apim-tutorials help"
	@echo "APIM policies: make -C examples/apim-policies help"
	@echo "APIM security: make -C examples/apim-security up / verify / down"

.PHONY: help-stacks help-dev help-verify help-release help-config help-all

help-all:
	@$(MAKE) --no-print-directory help-config help-dev help-release help-stacks help-verify

help-stacks:
	@printf "\nStack Lifecycle:\n"
	@printf $(HELP_FMT) "down" "Stop all compose services defined by this repo"
	@printf $(HELP_FMT) "down-all" "Stop every stack launched by up-all"
	@printf $(HELP_FMT) "examples" "Choose a demo and see how to run it"
	@$(foreach s,$(STACKS),printf $(HELP_FMT) "$(call stack_target,up,$(s))" "$($(s).about)";)
	@printf $(HELP_FMT) "up-ai-foundry-keyless" "Same, calling Foundry with managed identity (Foundry: make up-keyless)"
	@printf $(HELP_FMT) "up-all" "Start root-managed stacks using isolated slots"
	@printf $(HELP_FMT) "up-gateway" "Start only the gateway and mock backend"
	@printf $(HELP_FMT) "up-hello-oidc-subscription" "Start the subscription-plus-JWT hello API example with Keycloak"
	@printf $(HELP_FMT) "up-hello-subscription" "Start the subscription-protected hello API example behind APIM"
	@printf $(HELP_FMT) "up-ui" "Start the operator console on localhost:3007"
	@printf "\nStack Isolation:\n"
	@printf $(HELP_FMT) "PORT_OFFSET=100 make up-ui" "Shift published ports by a fixed offset without changing defaults"
	@printf $(HELP_FMT) "STACK_SLOT=1 make up-otel" "Shift published ports by a slot and isolate compose project names"
	@printf "\nLogs:\n"
	@$(foreach s,$(STACKS),printf $(HELP_FMT) "$(call stack_target,logs,$(s))" "Tail logs for make $(call stack_target,up,$(s))";)

help-dev:
	@printf "\nCode Quality and Tooling:\n"
	@printf $(HELP_FMT) "check-host-ports" "Check common local host ports before starting stacks"
	@printf $(HELP_FMT) "check-version" "Check synchronized release versions and pinned upstream refs"
	@printf $(HELP_FMT) "compat" "Run the curated APIM sample compatibility harness"
	@printf $(HELP_FMT) "compat-report" "Run static Terraform/APIM compatibility analysis (requires TOFU_SHOW=...)"
	@printf $(HELP_FMT) "fmt" "Format Python code with Ruff"
	@printf $(HELP_FMT) "frontend-check" "Run Biome, TypeScript, and Astro checks for repo frontends"
	@printf $(HELP_FMT) "hooks" "Install lefthook-managed local validation hooks"
	@printf $(HELP_FMT) "import-tofu" "Import a tofu show JSON file into a running simulator (requires TOFU_SHOW=...)"
	@printf $(HELP_FMT) "install-hooks" "Install lefthook-managed local validation hooks"
	@printf $(HELP_FMT) "lint" "Run repo-level lint checks without modifying files"
	@printf $(HELP_FMT) "lint-bash32" "Check tracked shell scripts for Bash 3.2 compatibility"
	@printf $(HELP_FMT) "lint-check" "Check Python formatting and lint with Ruff without modifying files"
	@printf $(HELP_FMT) "lint-markdown" "Lint tracked Markdown files"
	@printf $(HELP_FMT) "lint-shell" "Audit executable shell script interfaces and hygiene"
	@printf $(HELP_FMT) "lint-yaml" "Lint tracked YAML files"
	@printf $(HELP_FMT) "doctor" "Check this machine can run the simulator, with a fix for each problem"
	@printf $(HELP_FMT) "prereqs" "Check Docker, mkcert, and local host ports before starting stacks"
	@printf $(HELP_FMT) "test" "Run Python and shell tests"
	@printf $(HELP_FMT) "test-python" "Run the Python test suite"
	@printf $(HELP_FMT) "test-shell" "Run the shell script test suite with BATS"
	@printf $(HELP_FMT) "test-terraform-provider" "Run the Terraform provider unit and acceptance tests"

help-verify:
	@printf "\nVerification and Smoke:\n"
	@printf $(HELP_FMT) "export-todo-har" "Capture the todo APIM flow as a HAR file for Proxyman"
	@printf $(HELP_FMT) "smoke-browser" "Run Scalar and console browser smokes in Docker slot 7"
	@printf $(HELP_FMT) "smoke-ai" "Run the AI gateway token-limit smoke test"
	@printf $(HELP_FMT) "smoke-ai-foundry" "Run the AI Foundry integration smoke test (cache, content safety, 429)"
	@printf $(HELP_FMT) "smoke-ai-foundry-keyless" "Check the keyless pairing: chat, JWKS, Foundry rejects bad tokens"
	@printf $(HELP_FMT) "smoke-aws" "Run the LocalStack AWS API Gateway comparison smoke test"
	@printf $(HELP_FMT) "smoke-edge" "Run the edge MCP and forwarded-header smoke test"
	@printf $(HELP_FMT) "smoke-hello" "Run the hello API smoke test (mode via SMOKE_HELLO_MODE)"
	@printf $(HELP_FMT) "smoke-management-gateway" "Prove the management gateway routes to the serving gateway"
	@printf $(HELP_FMT) "smoke-mcp" "Run the end-to-end MCP smoke test against a running stack"
	@printf $(HELP_FMT) "smoke-oidc" "Run the end-to-end OIDC smoke test against a running stack"
	@printf $(HELP_FMT) "smoke-private" "Run the private-mode smoke test and internal probe"
	@printf $(HELP_FMT) "smoke-shared" "Run the shared gateway workload RBAC smoke test"
	@printf $(HELP_FMT) "smoke-tls" "Run the TLS edge smoke test using the generated local CA"
	@printf $(HELP_FMT) "smoke-todo" "Run the APIM-backed todo demo smoke test"
	@printf $(HELP_FMT) "smoke-tutorials-live" "Run all numbered tutorial scripts against live local stacks"
	@printf $(HELP_FMT) "test-todo-bruno" "Run the Bruno collection against the running todo demo stack"
	@printf $(HELP_FMT) "test-todo-e2e" "Run Playwright against the running todo demo stack"
	@printf $(HELP_FMT) "test-todo-postman" "Run the Postman collection against the running todo demo stack"
	@printf $(HELP_FMT) "verify" "Prove the hello-subscription stack end to end via the verify skill (launch, drive, cleanup)"
	@printf $(HELP_FMT) "verify-dry-run" "Show what make verify would do, without starting anything"
	@printf $(HELP_FMT) "verify-test" "Run the verify skill's BATS contract tests under Bash 3.2"
	@printf $(HELP_FMT) "verify-azure" "Diff curated requests against simulator and live Azure APIM"
	@printf $(HELP_FMT) "verify-hello-otel" "Verify OTEL signals for the LGTM-backed hello API starter"
	@printf $(HELP_FMT) "verify-otel" "Verify Grafana, Loki, Tempo, and Prometheus for the OTEL stack"
	@printf $(HELP_FMT) "verify-todo-otel" "Verify OTEL signals for the LGTM-backed todo demo stack"

help-release:
	@printf "\nRelease:\n"
	@printf $(HELP_FMT) "release" "Bump to VERSION, run checks, and create a release commit"
	@printf $(HELP_FMT) "release-dry-run" "Preview the release-commit flow for VERSION without changing files"
	@printf $(HELP_FMT) "release-preview" "Alias for release-dry-run"
	@printf $(HELP_FMT) "release-tag" "Create an annotated vVERSION tag from the current main commit"
	@printf $(HELP_FMT) "release-tag-dry-run" "Preview tag creation for VERSION without changing git state"
	@printf $(HELP_FMT) "runtime-artifact" "Build the narrow runtime source zip under dist/"

help-config:
	@printf "\nCompose Config:\n"
	@$(foreach s,$(STACKS),printf $(HELP_FMT) "$(call stack_target,compose-config,$(s))" "Render docker compose config for make $(call stack_target,up,$(s))";)
	@printf $(HELP_FMT) "compose-config-ui" "Render docker compose config for the console stack"

.PHONY: doctor

doctor:
	@./scripts/doctor.sh --execute

prereqs: check-docker-prerequisites check-mkcert-prerequisites check-host-ports

.PHONY: prereqs check-docker-prerequisites check-mkcert-prerequisites

check-docker-prerequisites:
	@if ! command -v docker >/dev/null 2>&1; then \
		echo "docker is required but was not found in PATH." >&2; \
		exit 1; \
	fi
	@if ! docker compose version >/dev/null 2>&1; then \
		echo "'docker compose' is required but not available." >&2; \
		exit 1; \
	fi
	@if ! docker info >/dev/null 2>&1; then \
		echo "Docker is not running. Start Docker Desktop and try again." >&2; \
		exit 1; \
	fi
	@engine="$$(docker version --format '{{.Server.Version}}')"; \
	if [ "$${engine%%.*}" -lt 25 ] 2>/dev/null; then \
		echo "Docker Engine 25 or later is required (found $$engine): the healthchecks use start_interval." >&2; \
		exit 1; \
	fi

check-mkcert-prerequisites:
	@if ! command -v mkcert >/dev/null 2>&1; then \
		echo "mkcert is required but was not found in PATH." >&2; \
		echo "Install it, then run 'mkcert -install'." >&2; \
		exit 1; \
	fi
	@CAROOT="$$(mkcert -CAROOT 2>/dev/null || true)"; \
	if [ -z "$$CAROOT" ] || [ ! -f "$$CAROOT/rootCA.pem" ] || [ ! -f "$$CAROOT/rootCA-key.pem" ]; then \
		echo "mkcert is installed but its local CA is not ready." >&2; \
		echo "Run 'mkcert -install' and try again." >&2; \
		exit 1; \
	fi

ensure-certs: check-mkcert-prerequisites
	./scripts/gen_dev_certs.sh --execute

$(DEV_CERTS): check-mkcert-prerequisites
	./scripts/gen_dev_certs.sh --execute

.PHONY: $(STACK_TARGETS)

define stack_rules
$(call stack_target,up,$(1)): $($(1).needs)
	$(if $($(1).env),$($(1).env) )$$(call compose_for,$(1)) up --build -d

$(call stack_target,logs,$(1)):
	$$(call compose_for,$(1)) logs -f $($(1).logs)

$(call stack_target,compose-config,$(1)):
	$$(call compose_for,$(1)) config
endef
$(foreach s,$(STACKS),$(eval $(call stack_rules,$(s))))

# Variants of a stack: the same compose files with another gateway config, or a subset.
.PHONY: up-gateway up-ui compose-config-ui up-hello-subscription up-hello-oidc-subscription up-ai-foundry-keyless check-aifoundry-network up-all down down-all

up-gateway:
	$(call compose_stack,core) -f compose.yml -f compose.public.yml up --build -d

up-ui: up

compose-config-ui: compose-config

up-hello-subscription:
	HELLO_APIM_CONFIG_PATH=/app/examples/hello-api/apim.subscription.json $(call compose_for,hello) up --build -d

up-hello-oidc-subscription:
	HELLO_APIM_CONFIG_PATH=/app/examples/hello-api/apim.oidc.subscription.json $(call compose_for,hello-oidc) up --build -d

up-ai-foundry-keyless:
	$(MAKE) --no-print-directory -C examples/ai-gateway up-foundry-keyless

check-aifoundry-network:
	@docker network inspect aifoundry >/dev/null 2>&1 || { \
	  echo "error: Docker network 'aifoundry' not found."; \
	  echo "Start the sibling AI Foundry simulator first: run 'make up' in your aifoundry-simulator checkout"; \
	  echo "(https://github.com/nickromney/aifoundry-simulator)."; \
	  exit 1; \
	}

up-all:
	@set -e; \
	slot="$(UP_ALL_SLOT_BASE)"; \
	for target in $(UP_ALL_STACKS); do \
	  echo "==> $$target (STACK_SLOT=$$slot)"; \
	  ./scripts/run-stacked-make.sh --execute "$$slot" "$$target"; \
	  slot=$$((slot + 1)); \
	done

down:
	$(foreach s,$(STACKS),$(call compose_for,$(s)) down --remove-orphans$(newline))
	$(MAKE) --no-print-directory -C examples/bff down

down-all:
	@set -e; \
	slot="$(UP_ALL_SLOT_BASE)"; \
	for _target in $(UP_ALL_STACKS); do \
	  echo "==> down (STACK_SLOT=$$slot)"; \
	  ./scripts/run-stacked-make.sh --execute "$$slot" down; \
	  slot=$$((slot + 1)); \
	done

hooks: install-hooks

install-hooks:
	lefthook install

local-ci:
	@command -v gitleaks >/dev/null 2>&1 || { echo "gitleaks is required for local CI"; exit 1; }
	gitleaks detect --source . --config .gitleaks.toml --redact
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory test-python
	@$(MAKE) --no-print-directory test-shell
	@$(MAKE) --no-print-directory compat
	@TOFU_SHOW=tests/fixtures/tofu_show/sample.json $(MAKE) --no-print-directory compat-report
	@$(MAKE) --no-print-directory frontend-check
	@$(MAKE) --no-print-directory test-terraform-provider
	@$(MAKE) --no-print-directory compose-config-ci

compose-config-ci:
	./scripts/gen_dev_certs.sh --execute
	docker compose version
	@$(MAKE) --no-print-directory compose-config
	@$(MAKE) --no-print-directory compose-config-mcp
	@$(MAKE) --no-print-directory compose-config-oidc
	@$(MAKE) --no-print-directory compose-config-edge
	@$(MAKE) --no-print-directory compose-config-tls
	@$(MAKE) --no-print-directory compose-config-private
	@$(MAKE) --no-print-directory compose-config-ui
	@$(MAKE) --no-print-directory compose-config-management-gateway
	@$(MAKE) --no-print-directory compose-config-self-hosted-gateway
	@$(MAKE) --no-print-directory -C examples/bff compose-config compose-config-series
	@$(MAKE) --no-print-directory -C examples/architecture-patterns compose-config
	@$(MAKE) --no-print-directory -C examples/architecture-patterns-extra compose-config
	@$(MAKE) --no-print-directory -C examples/regional-gateways compose-config
	@$(MAKE) --no-print-directory -C examples/new-instance compose-config
	@$(MAKE) --no-print-directory -C examples/ai-gateway compose-config-foundry-keyless

fmt:
	uv run --extra dev ruff format .

lint:
	@$(MAKE) --no-print-directory lint-check
	@$(MAKE) --no-print-directory lint-yaml
	@$(MAKE) --no-print-directory lint-markdown
	@$(MAKE) --no-print-directory lint-bash32
	@$(MAKE) --no-print-directory lint-shell
	@$(MAKE) --no-print-directory lint-shellcheck

lint-check:
	uv run --extra dev ruff format --check .
	uv run --extra dev ruff check .

lint-yaml:
	@"$(LINT_YAML_SCRIPT)" --execute

lint-markdown:
	@"$(LINT_MARKDOWN_SCRIPT)" --execute

lint-bash32:
	@/bin/bash "$(LINT_BASH32_SCRIPT)" --execute

lint-shell:
	@"$(AUDIT_SHELL_SCRIPTS_SCRIPT)" --execute

# lint-shell audits conventions; this is the one that runs shellcheck.
lint-shellcheck:
	@./scripts/lint-shellcheck.sh --execute

frontend-check:
	npm --prefix ui ci
	npm --prefix ui run check
	npm --prefix examples/todo-app/frontend-astro ci
	npm --prefix examples/todo-app/frontend-astro run check
	npm --prefix examples/portal ci
	npm --prefix examples/portal run check

check-version:
	@"$(CHECK_VERSION_SCRIPT)" --execute

check-host-ports:
	./scripts/check-host-ports.sh --execute $(PORTS)

runtime-artifact:
	$(UV_RUN) python scripts/build_runtime_artifact.py

release:
	@[ -n "$(VERSION)" ] || { echo "VERSION is required, e.g. make release VERSION=X.Y.Z"; exit 1; }
	@"$(RELEASE_SCRIPT)" --execute "$(VERSION)"

release-dry-run:
	@[ -n "$(VERSION)" ] || { echo "VERSION is required, e.g. make release-dry-run VERSION=X.Y.Z"; exit 1; }
	@"$(RELEASE_SCRIPT)" --dry-run "$(VERSION)"

release-preview: release-dry-run

release-tag:
	@[ -n "$(VERSION)" ] || { echo "VERSION is required, e.g. make release-tag VERSION=X.Y.Z"; exit 1; }
	@"$(RELEASE_TAG_SCRIPT)" --execute "$(VERSION)"

release-tag-dry-run:
	@[ -n "$(VERSION)" ] || { echo "VERSION is required, e.g. make release-tag-dry-run VERSION=X.Y.Z"; exit 1; }
	@"$(RELEASE_TAG_SCRIPT)" --dry-run "$(VERSION)"

complexity:
	@echo "Cyclomatic complexity above the ratchet in pyproject.toml:"
	$(UV_RUN) --extra dev ruff check --select C901 app scripts tests examples

# Report every function above THRESHOLD, highest first, without failing.
# The ratchet only moves down: use this to find the next one to split.
complexity-report:
	@$(UV_RUN) --extra dev ruff check --select C901 \
		--config 'lint.mccabe.max-complexity=$(or $(THRESHOLD),8)' \
		--output-format=concise app scripts tests examples 2>&1 \
		| sed -E 's/^([^:]+):[0-9]+:[0-9]+: C901 `([^`]+)` is too complex \(([0-9]+) > [0-9]+\)/\3\t\2\t\1/' \
		| sort -rn

# Mutation testing. Name the module: a run that drags in suites which cannot
# kill a single mutant costs minutes and buys nothing.
#   make mutation MODULE=app.backend_pool
# mutmut matches mutant names with fnmatch, and a mutant is named
# <module>.x_<function>__mutmut_<n>. A bare module name therefore matches
# nothing, so the glob is appended here rather than left as a trap.
mutation:
	@[ -n "$(MODULE)" ] || { echo "MODULE is required, e.g. make mutation MODULE=app.backend_pool"; exit 1; }
	$(UV_RUN) --extra dev mutmut run "$(MODULE)$(if $(findstring *,$(MODULE)),,.*)"

# Score the curated module list in one bounded pass. Each module gets its own
# budget through scripts/lib/timeout.sh, which works without GNU coreutils.
mutation-baseline:
	@./scripts/mutation-test.sh --execute --no-fail

mutation-gate:
	@./scripts/mutation-test.sh --execute

mutation-results:
	$(UV_RUN) --extra dev mutmut results

mutation-show:
	@[ -n "$(MUTANT)" ] || { echo "MUTANT is required, e.g. make mutation-show MUTANT=app.backend_pool.x_f__mutmut_1"; exit 1; }
	$(UV_RUN) --extra dev mutmut show $(MUTANT)

mutation-clean:
	rm -rf mutants .mutmut-cache

test: test-python test-shell

test-python:
	$(UV_RUN) --extra dev pytest -q --cov=app --cov-branch --cov-report=term-missing --cov-report=xml
	mkdir -p coverage-reports
	cp coverage.xml coverage-reports/python-coverage-report.xml

test-shell:
	@command -v bats >/dev/null 2>&1 || { echo "bats is required for shell tests"; exit 1; }
	bats tests/shell

# Unit tests, then acceptance tests against a throwaway simulator with a real
# terraform binary. See terraform-provider-apimsim/README.md.
test-terraform-provider:
	@command -v go >/dev/null 2>&1 || { echo "go is required for the Terraform provider tests"; exit 1; }
	@command -v terraform >/dev/null 2>&1 || { echo "terraform is required for the Terraform provider acceptance tests"; exit 1; }
	$(MAKE) --no-print-directory -C terraform-provider-apimsim check testacc

compat:
	$(UV_RUN) python scripts/check_sample_compat.py

compat-report:
	$(UV_RUN) python scripts/compat_report.py

import-tofu:
	$(UV_RUN) python scripts/import_tofu.py

verify-azure:
	$(UV_RUN) python scripts/verify_azure.py

verify-otel:
	$(UV_RUN) python scripts/verify_otel.py

verify-hello-otel:
	$(UV_RUN) python scripts/verify_hello_otel.py

verify-todo-otel:
	VERIFY_OTEL_TODO=true $(UV_RUN) python scripts/verify_otel.py

# Verification skill (skills/verify-apim-simulator): drives the hello-subscription stack through its published gateway port.
# Targets come from verify-kit: verify, verify-dry-run, verify-test.
VERIFY_SKILL := skills/verify-apim-simulator
include $(VERIFY_SKILL)/verify.mk

# Smoke tests that are one script each: smoke-<name> runs scripts/smoke_<name>.py.
SMOKE_SCRIPTS := oidc hello ai ai-foundry shared aws todo
.PHONY: $(addprefix smoke-,$(SMOKE_SCRIPTS))
$(addprefix smoke-,$(SMOKE_SCRIPTS)): smoke-%:
	$(UV_RUN) python scripts/smoke_$(subst -,_,$*).py

smoke-mcp:
	SMOKE_MCP_URL="$(SMOKE_MCP_URL)" $(UV_RUN) --extra mcp python scripts/smoke_mcp.py

smoke-edge:
	SMOKE_EDGE_BASE_URL="$(SMOKE_EDGE_BASE_URL)" $(UV_RUN) --extra mcp python scripts/smoke_edge.py

smoke-tls:
	SMOKE_EDGE_BASE_URL="$(EDGE_TLS_BASE_URL)" $(UV_RUN) --extra mcp python scripts/smoke_edge.py

check-private-port-clear:
	$(UV_RUN) python -c "import socket; sock = socket.socket(); sock.settimeout(1); code = sock.connect_ex(('127.0.0.1', $(APIM_GATEWAY_PORT))); sock.close(); print('Host port $(APIM_GATEWAY_PORT) is unavailable, as required for private mode.') if code else (_ for _ in ()).throw(SystemExit('localhost:$(APIM_GATEWAY_PORT) is already reachable before private-mode launch; stop the conflicting listener before continuing'))"

smoke-private:
	$(MAKE) check-private-port-clear
	$(call compose_for,private) run --rm --entrypoint python3 smoke-runner scripts/run_smoke_private.py

smoke-ai-foundry-keyless:
	$(MAKE) --no-print-directory -C examples/ai-gateway smoke-foundry-keyless

smoke-management-gateway:
	SMOKE_MANAGEMENT_GATEWAY_BASE_URL="$(SMOKE_MANAGEMENT_GATEWAY_BASE_URL)" SMOKE_SERVING_GATEWAY_BASE_URL="$(SMOKE_SERVING_GATEWAY_BASE_URL)" $(UV_RUN) python scripts/smoke_management_gateway.py

smoke-tutorials-live:
	APIM_BASE="$(APIM_BASE_URL)" GRAFANA_BASE="$(GRAFANA_BASE_URL)" OPERATOR_CONSOLE_BASE="$(OPERATOR_CONSOLE_URL)" ./scripts/run_tutorial_smoke.sh --execute

test-todo-e2e:
	npm --prefix examples/todo-app/frontend-astro ci
	npm --prefix examples/todo-app/frontend-astro exec playwright install chromium
	BASE_URL="$(TODO_FRONTEND_BASE_URL)" API_BASE_URL="$(TODO_APIM_PUBLIC_BASE_URL)" GRAFANA_BASE_URL="$(TODO_GRAFANA_BASE_URL)" npm --prefix examples/todo-app/frontend-astro run test:e2e

test-todo-bruno:
	@tmp_env="$$(mktemp)"; \
	trap 'rm -f "$$tmp_env"' EXIT; \
	printf 'vars {\n  apimBaseUrl: %s\n  frontendOrigin: %s\n  subscriptionKey: todo-demo-key\n  invalidSubscriptionKey: bad-subscription-key\n}\n' "$(TODO_APIM_BASE_URL)" "$(TODO_FRONTEND_BASE_URL)" >"$$tmp_env" \
	&& cd examples/todo-app/api-clients/bruno \
	&& npm exec --yes --package=@usebruno/cli -- bru run --env-file "$$tmp_env" .

test-todo-postman:
	@tmp_env="$$(mktemp)"; \
	trap 'rm -f "$$tmp_env"' EXIT; \
	jq -n \
	  --arg apimBaseUrl "$(TODO_APIM_BASE_URL)" \
	  --arg frontendOrigin "$(TODO_FRONTEND_BASE_URL)" \
	  '{"id":"apim-simulator-local","name":"Local","values":[{"key":"apimBaseUrl","value":$$apimBaseUrl,"enabled":true},{"key":"frontendOrigin","value":$$frontendOrigin,"enabled":true},{"key":"subscriptionKey","value":"todo-demo-key","enabled":true},{"key":"invalidSubscriptionKey","value":"bad-subscription-key","enabled":true}]}' >"$$tmp_env" \
	&& npm exec --yes --package=newman -- newman run examples/todo-app/api-clients/postman/todo-through-apim.postman_collection.json --environment "$$tmp_env"

export-todo-har:
	TODO_HAR_APIM_BASE_URL="$(TODO_APIM_BASE_URL)" TODO_HAR_FRONTEND_BASE_URL="$(TODO_FRONTEND_BASE_URL)" $(UV_RUN) python scripts/export_todo_har.py

.PHONY: compose-config-self-hosted-gateway
compose-config-self-hosted-gateway:
	@$(MAKE) --no-print-directory -C examples/self-hosted-gateway compose-config

examples:
	@echo "Choose a demo (commands run from the repository root):"
	@echo ""
	@printf "  %-18s %-34s %s\n" "Scalar portal" "make -C examples/portal help" "Offline API reference and request client"
	@printf "  %-18s %-34s %s\n" "AI gateway" "make up-ai" "Token limits and metrics with a mock LLM"
	@printf "  %-18s %-34s %s\n" "AWS comparison" "make up-aws" "LocalStack API Gateway beside APIM"
	@printf "  %-18s %-34s %s\n" "APIM tutorials" "make -C examples/apim-tutorials help" "Eleven workflows and Bruno CLI"
	@printf "  %-18s %-34s %s\n" "BFF" "make -C examples/bff help" "Web/mobile backends and gateways in series"
	@printf "  %-18s %-34s %s\n" "Gateway patterns" "make -C examples/architecture-patterns help" "Routing, offloading, aggregation, gatekeeper"
	@printf "  %-18s %-34s %s\n" "Workload patterns" "make -C examples/architecture-patterns-extra help" "Domain adapter, deployment stamps, bulkheads"
	@printf "  %-18s %-34s %s\n" "New instance" "make -C examples/new-instance help" "Echo API, Starter and Unlimited, as in a new Azure instance"
	@printf "  %-18s %-34s %s\n" "Regional gateways" "make -C examples/regional-gateways help" "Deployment.Region routing, retry and regional failover"
	@printf "  %-18s %-34s %s\n" "Self-hosted gateway" "make -C examples/self-hosted-gateway help" "Configuration pull, API assignment and last-known-good state"
	@printf "  %-18s %-34s %s\n" "Hello API" "make up-hello" "Smallest backend behind APIM"
	@printf "  %-18s %-34s %s\n" "MCP" "make up-mcp" "MCP server behind APIM"
	@printf "  %-18s %-34s %s\n" "OIDC" "make up-oidc" "JWT and subscription flows with Keycloak"
	@printf "  %-18s %-34s %s\n" "Shared gateway" "make up-shared" "Workload identities separated by role"
	@printf "  %-18s %-34s %s\n" "Todo app" "make up-todo" "Browser frontend calling an API through APIM"
	@echo ""
	@echo "BFF and architecture patterns have their own Makefiles; other demos use root targets."
	@echo "For stack variants, logs, and smoke checks: make help-stacks or make help-verify"

.PHONY: security-lab-up security-lab-verify security-lab-down
security-lab-up:
	$(MAKE) -C examples/apim-security up
security-lab-verify:
	$(MAKE) -C examples/apim-security verify
security-lab-down:
	$(MAKE) -C examples/apim-security down


.PHONY: smoke-authoring smoke-browser
smoke-authoring:
	$(MAKE) -C examples/portal smoke-authoring

smoke-browser:
	$(MAKE) -C examples/portal smoke-browser
