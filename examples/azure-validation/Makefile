.DEFAULT_GOAL := help
REPO_ROOT := $(abspath ../..)
UV_RUN := cd "$(REPO_ROOT)" && uv run --extra dev python examples/azure-validation/lab.py
.PHONY: help prepare deploy serve verify cleanup
help:
	@echo "Azure parity: prepare / deploy / serve / verify / cleanup"
prepare deploy serve verify cleanup:
	$(UV_RUN) $@
