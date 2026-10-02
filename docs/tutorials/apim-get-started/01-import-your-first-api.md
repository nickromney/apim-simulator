# 1 - Import Your First API

Source: [Tutorial: Import and publish your first API](https://learn.microsoft.com/en-us/azure/api-management/import-and-publish)

Simulator status: Supported

## Run It Locally

From the repo root:

```bash
make up
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key

OPENAPI_SOURCE=tests/fixtures/openapi/petstore3-2026-10-02.json \
APIM_API_ID=petstore \
APIM_API_NAME="Swagger Petstore - OpenAPI 3.0" \
APIM_API_PATH=petstore \
APIM_UPSTREAM_BASE_URL=http://mock-backend:8080/api/v3 \
uv run --project . python scripts/import_openapi.py

curl -i "$APIM_BASE/petstore/pet/findByStatus?status=pending"
curl -i "$APIM_BASE/petstore/pet/findByTags?tags=cat&tags=dog"

# Keep the small health/echo API for the subsequent tutorial commands.
OPENAPI_SOURCE=examples/mock-backend/openapi.json \
APIM_API_ID=tutorial-api \
APIM_API_NAME="Tutorial API" \
APIM_API_PATH=tutorial-api \
APIM_UPSTREAM_BASE_URL=http://mock-backend:8080/api \
uv run --project . python scripts/import_openapi.py
```

## What Mapped Cleanly

- Unchanged import of Microsoft's complete Petstore OpenAPI document
- API creation and operation discovery
- Gateway routing of the tutorial's `findByStatus?status=pending` request
- Repeated array query parameters forwarded to the local backend

The fixture is an unmodified snapshot of the tutorial's public specification,
fetched on 2026-10-02. It declares OpenAPI 3.0.4 and all 19 operations. Only the
backend URL is overridden at import time; the document is not rewritten.
The local mock Petstore returns representative pets for status, tags, and ID
lookups. A pending lookup returns pet 1 with `status: pending`; the cat-and-dog
tag lookup returns pets 1, 2, and 3.

## Shortcut

If you want the scripted shortcut instead of running the commands manually:

```bash
./docs/tutorials/apim-get-started/tutorial01.sh --setup
./docs/tutorials/apim-get-started/tutorial01.sh --verify
```

Use `--setup` to have [`tutorial01.sh`](tutorial01.sh) perform the local setup for this step. Use `--verify` to validate the existing tutorial state without restarting the stack.

Setup imports both Petstore and the small health/echo API named `tutorial-api`,
which subsequent tutorials use. Verification checks Petstore's 19 imported
operations, required status query template, backend override, pending response,
and repeated tag query behavior, alongside the health/echo routes below.

Expected `./docs/tutorials/apim-get-started/tutorial01.sh --verify` output:

```text
Verifying imported API metadata
$ curl -sS -H "X-Apim-Tenant-Key: test-tenant-key" "http://localhost:18000/apim/management/apis/tutorial-api"
{
  "id": "tutorial-api",
  "operations": [
    "echo",
    "health"
  ],
  "path": "tutorial-api",
  "upstream_base_url": "http://mock-backend:8080/api"
}

Verifying imported API routes
$ curl -sS "http://localhost:18000/tutorial-api/health"
{
  "path": "/api/health",
  "status": "ok"
}

$ curl -sS "http://localhost:18000/tutorial-api/echo"
{
  "body": "",
  "method": "GET",
  "ok": true,
  "path": "/api/echo"
}
```

## Local Runtime

- This uses the simulator management API plus [`scripts/import_openapi.py`](../../../scripts/import_openapi.py), not the Azure portal.
- The public specification is captured locally for repeatable imports; its provenance is in [the fixture README](../../../tests/fixtures/openapi/README.md).
- The HTTP mock backend is an explicit import override. The importer also accepts the tutorial's original linked document and derives its public HTTPS server URL when no override is provided.
- Local 3.0.4 support, scoped schema projection, and default-response semantics are documented in the [import contract](../../FIDELITY-CONTRACTS.md#openapi-import--verified-subset).
