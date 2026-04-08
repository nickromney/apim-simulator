# Tutorial 01 Expected Output

*2026-04-08T20:01:56Z*

This document captures the deterministic expected output contract for `./tutorial01.sh --verify`. It uses stubbed `docker`, `curl`, and `uv` commands so the transcript stays stable even when Docker is unavailable in the local environment.

```bash
set -euo pipefail

repo_root="$PWD"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$tmp/bin/docker"

cat > "$tmp/bin/curl" <<'SH'
#!/usr/bin/env bash
case "$*" in
  *"/apim/health"*)
    printf '{"status":"healthy"}\n'
    ;;
  *"/apim/management/apis/"*)
    printf '{"id":"tutorial-api","path":"tutorial-api","upstream_base_url":"http://mock-backend:8080/api","operations":[{"id":"health"},{"id":"echo"}]}'"\n"
    ;;
  *"/tutorial-api/health"*)
    printf '{"status":"ok","path":"/api/health"}\n'
    ;;
  *"/tutorial-api/echo"*)
    printf '{"ok":true,"method":"GET","path":"/api/echo","body":"","headers":{"host":"mock-backend:8080"}}\n'
    ;;
esac
SH
chmod +x "$tmp/bin/curl"

cat > "$tmp/bin/uv" <<'SH'
#!/usr/bin/env bash
printf '{\n  "api_id": "tutorial-api",\n  "path": "tutorial-api",\n  "operations": [\n    "echo",\n    "health"\n  ],\n  "import": {\n    "diagnostics": [],\n    "format": "openapi+json",\n    "operation_count": 2,\n    "upstream_base_url": "http://mock-backend:8080/api"\n  }\n}\n'
SH
chmod +x "$tmp/bin/uv"

PATH="$tmp/bin:$PATH" \
APIM_HEALTH_ATTEMPTS=1 \
APIM_HEALTH_DELAY_SECONDS=0 \
APIM_BASE=http://localhost:18000 \
APIM_TENANT_KEY=test-tenant-key \
OPENAPI_SOURCE="$repo_root/examples/mock-backend/openapi.json" \
./tutorial01.sh --verify

```

```output
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
