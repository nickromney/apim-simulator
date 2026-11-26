SHELL := /bin/bash
REPO_ROOT := $(abspath ..)
BIN := bin/terraform-provider-apimsim
TENANT_KEY ?= tfacc-tenant

.PHONY: build test vet fmt-check check testacc example-plan clean

build:
	go build -o $(BIN) .

test:
	go test ./...

vet:
	go vet ./...

fmt-check:
	@test -z "$$(gofmt -l .)" || { gofmt -l .; echo "run gofmt -w ."; exit 1; }

check: fmt-check vet test

# Starts a throwaway simulator on a free port, runs the acceptance tests
# against it, and stops it again. The simulator loads a minimal config file:
# started from environment variables alone it builds a legacy route config,
# and API CRUD refuses to mutate that.
testacc:
	@set -euo pipefail; \
	port="$${SIM_PORT:-$$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')}"; \
	log="$$(mktemp)"; config="$$(mktemp)"; \
	printf '{"tenant_access": {"enabled": true, "primary_key": "%s"}}\n' "$(TENANT_KEY)" >"$$config"; \
	( cd "$(REPO_ROOT)" && PORT="$$port" APIM_CONFIG_PATH="$$config" exec uv run python -m app.run_server ) >"$$log" 2>&1 & \
	pid=$$!; \
	trap 'kill "$$pid" 2>/dev/null || true; wait "$$pid" 2>/dev/null || true' EXIT; \
	for _ in $$(seq 1 100); do \
		curl -fsS "http://127.0.0.1:$$port/apim/health" >/dev/null 2>&1 && break; \
		kill -0 "$$pid" 2>/dev/null || { cat "$$log"; exit 1; }; \
		sleep 0.2; \
	done; \
	echo "simulator on :$$port (log $$log)"; \
	TF_ACC=1 APIMSIM_ENDPOINT="http://127.0.0.1:$$port" APIMSIM_TENANT_KEY="$(TENANT_KEY)" \
		go test ./internal/provider -run '^TestAcc' -count=1 -v $(TESTARGS)

# Plans examples/main.tf with this checkout's provider build, without touching
# your global Terraform CLI configuration.
example-plan: build
	@printf 'provider_installation {\n  dev_overrides {\n    "nickromney/apimsim" = "%s"\n  }\n  direct {}\n}\n' "$(CURDIR)/bin" > bin/dev.tfrc
	TF_CLI_CONFIG_FILE="$(CURDIR)/bin/dev.tfrc" terraform -chdir=examples plan

clean:
	rm -rf bin
