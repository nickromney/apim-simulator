# 1 - Import Your First API

Source: [Tutorial: Import and publish your first API](https://learn.microsoft.com/en-us/azure/api-management/import-and-publish)

Simulator status: Supported

## Run It Locally

From the repo root:

```bash
make up
export APIM_BASE=http://localhost:8000
export APIM_TENANT_KEY=local-dev-tenant-key

OPENAPI_SOURCE=examples/mock-backend/openapi.json \
APIM_API_ID=tutorial-api \
APIM_API_NAME="Tutorial API" \
APIM_API_PATH=tutorial-api \
uv run python scripts/import_openapi.py
```

## What Mapped Cleanly

- OpenAPI import
- API creation and operation discovery
- Gateway routing through the imported API path

## Shortcut

If you want the scripted shortcut instead of running the commands manually:

```bash
./tutorial01.sh
./tutorial01.sh --verify
```

Unlike the manual path above, `tutorial01.sh` brings the local stack up itself with `docker compose`.

Expected `./tutorial01.sh --verify` output:

```text
Starting tutorial 01 stack with docker compose
Waiting for gateway health at http://localhost:18000/apim/health
Importing OpenAPI source into API 'tutorial-api'
{
  "api_id": "tutorial-api",
  "path": "tutorial-api",
  "operations": [
    "echo",
    "health"
  ],
  "import": {
    "diagnostics": [],
    "format": "openapi+json",
    "operation_count": 2,
    "upstream_base_url": "http://mock-backend:8080/api"
  }
}

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

## Differences From Azure APIM

- This uses the simulator management API plus `scripts/import_openapi.py`, not the Azure portal.
- The sample spec is local and points at the checked-in mock backend instead of the public Petstore service.
