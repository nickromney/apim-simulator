# Tutorial 01 Live Docker Capture

*2026-04-08T21:05:14Z*

Live Docker-backed capture of `./tutorial01.sh --verify` from the repo root.

```bash
make down >/dev/null 2>&1 || true
```

```output
```

```bash
./tutorial01.sh --verify
```

```output
Starting tutorial 01 stack with docker compose
Waiting for gateway health at http://localhost:8000/apim/health
Importing OpenAPI source into API 'tutorial-api'
{
  "api_id": "tutorial-api",
  "path": "tutorial-api",
  "operations": [
    "echo",
    "health"
  ],
  "import": {
    "format": "openapi+json",
    "operation_count": 2,
    "upstream_base_url": "http://mock-backend:8080/api",
    "diagnostics": []
  }
}

Verifying imported API metadata
$ curl -sS -H "X-Apim-Tenant-Key: local-dev-tenant-key" "http://localhost:8000/apim/management/apis/tutorial-api"
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
$ curl -sS "http://localhost:8000/tutorial-api/health"
{
  "path": "/api/health",
  "status": "ok"
}

$ curl -sS "http://localhost:8000/tutorial-api/echo"
{
  "body": "",
  "method": "GET",
  "ok": true,
  "path": "/api/echo"
}

```
