# 1 - Import your first API

Microsoft Learn equivalent tutorial: [Tutorial: Import and publish your first API](https://learn.microsoft.com/en-us/azure/api-management/import-and-publish)

Import a Petstore API and call it through the gateway. Then import the API used by the following tutorials.

## Check prerequisites

You need Docker with Compose, `make`, `curl`, and `uv`. The project requires
Python 3.13 or later. On the first run, `uv` can download a compatible Python
and install the project dependencies. [Install uv](https://docs.astral.sh/uv/getting-started/installation/)

Run these checks from the repository root. Continue when the Docker check
passes and both version commands succeed.

```bash
make check-docker-prerequisites
curl --version
uv run --project . python --version
```

## Start the gateway

Start the default stack, then check gateway health and management access.

```bash
make up
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key

curl -fsS "$APIM_BASE/apim/health"
curl -fsS -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" "$APIM_BASE/apim/management/status"
```

The health endpoint returns `{"status":"healthy"}`. The management endpoint
returns the service name and resource counts. If the gateway is still
starting, wait a few seconds and retry both checks. If Docker asks for access
to `dhi.io`, see [Docker images and public alternatives](../../../README.md#container-hardening).

## Import and call the APIs

```bash
APIM_BASE_URL="$APIM_BASE" \
OPENAPI_SOURCE=tests/fixtures/openapi/petstore3-2026-10-02.json \
APIM_API_ID=petstore \
APIM_API_NAME="Swagger Petstore - OpenAPI 3.0" \
APIM_API_PATH=petstore \
APIM_UPSTREAM_BASE_URL=http://mock-backend:8080/api/v3 \
uv run --project . python scripts/import_openapi.py

curl -i "$APIM_BASE/petstore/pet/findByStatus?status=pending"
curl -i "$APIM_BASE/petstore/pet/findByTags?tags=cat&tags=dog"

# Keep the small health/echo API for the subsequent tutorial commands.
APIM_BASE_URL="$APIM_BASE" \
OPENAPI_SOURCE=examples/mock-backend/openapi.json \
APIM_API_ID=tutorial-api \
APIM_API_NAME="Tutorial API" \
APIM_API_PATH=tutorial-api \
APIM_UPSTREAM_BASE_URL=http://mock-backend:8080/api \
uv run --project . python scripts/import_openapi.py
```

The pending lookup returns pet 1 with `status: pending`. The cat-and-dog tag
lookup returns pets 1, 2, and 3. The import also creates `tutorial-api` for
the following tutorials.

## Run the setup script

After checking the prerequisites, you can use the script to start the gateway,
import the APIs, and verify the results.

```bash
./docs/tutorials/apim-get-started/tutorial01.sh --setup
./docs/tutorials/apim-get-started/tutorial01.sh --verify
```

Use `--setup` to import the APIs. Use `--verify` to check the existing state without restarting the stack.

The imported Petstore API has 19 operations. On `tutorial-api`, the health
operation returns `"status": "ok"` and the echo operation returns
`"method": "GET"` and `"ok": true`.

The Petstore fixture uses OpenAPI 3.0.4. The import command overrides its backend
URL to use the local mock service. To use the public HTTPS server declared in a
specification, omit the backend override.
