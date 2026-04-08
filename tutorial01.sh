#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DOCKER_BIN="${DOCKER_BIN:-docker}"
APIM_BASE="${APIM_BASE:-http://localhost:8000}"
APIM_TENANT_KEY="${APIM_TENANT_KEY:-local-dev-tenant-key}"
OPENAPI_SOURCE="${OPENAPI_SOURCE:-$ROOT_DIR/examples/mock-backend/openapi.json}"
APIM_API_ID="${APIM_API_ID:-tutorial-api}"
APIM_API_NAME="${APIM_API_NAME:-Tutorial API}"
APIM_API_PATH="${APIM_API_PATH:-tutorial-api}"
APIM_HEALTH_ATTEMPTS="${APIM_HEALTH_ATTEMPTS:-30}"
APIM_HEALTH_DELAY_SECONDS="${APIM_HEALTH_DELAY_SECONDS:-1}"
VERIFY=0

usage() {
  cat <<EOF
Usage: ./tutorial01.sh [--verify]

Runs tutorial step 1 for the APIM simulator end-to-end, including starting the
local stack with docker compose.

Environment overrides:
  DOCKER_BIN        Docker CLI binary. Default: $DOCKER_BIN
  APIM_BASE         Gateway base URL. Default: $APIM_BASE
  APIM_TENANT_KEY   Management tenant key. Default: $APIM_TENANT_KEY
  OPENAPI_SOURCE    OpenAPI file path or URL. Default: $OPENAPI_SOURCE
  APIM_API_ID       API identifier to create. Default: $APIM_API_ID
  APIM_API_NAME     API display name. Default: $APIM_API_NAME
  APIM_API_PATH     Public API path. Default: $APIM_API_PATH
  APIM_HEALTH_ATTEMPTS      Health-check retry attempts. Default: $APIM_HEALTH_ATTEMPTS
  APIM_HEALTH_DELAY_SECONDS Health-check retry delay. Default: $APIM_HEALTH_DELAY_SECONDS

Examples:
  ./tutorial01.sh
  ./tutorial01.sh --verify
EOF
}

wait_for_gateway() {
  local attempt

  for ((attempt = 1; attempt <= APIM_HEALTH_ATTEMPTS; attempt += 1)); do
    if curl -fsS "$APIM_BASE/apim/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$APIM_HEALTH_DELAY_SECONDS"
  done

  echo "Gateway did not become healthy at $APIM_BASE/apim/health" >&2
  return 1
}

start_stack() {
  local compose_log
  compose_log="$(mktemp)"
  trap 'rm -f "$compose_log"' RETURN

  if ! "$DOCKER_BIN" compose \
    -f "$ROOT_DIR/compose.yml" \
    -f "$ROOT_DIR/compose.public.yml" \
    up --build -d >"$compose_log" 2>&1; then
    echo "docker compose failed while starting the tutorial 01 stack:" >&2
    cat "$compose_log" >&2
    exit 1
  fi
}

verify_api_metadata() {
  local response

  echo '$ curl -sS -H "X-Apim-Tenant-Key: '"$APIM_TENANT_KEY"'" "'"$APIM_BASE"'/apim/management/apis/'"$APIM_API_ID"'"'
  response="$(curl -fsS -H "X-Apim-Tenant-Key: $APIM_TENANT_KEY" "$APIM_BASE/apim/management/apis/$APIM_API_ID")"

  ACTUAL_JSON="$response" EXPECTED_API_ID="$APIM_API_ID" EXPECTED_API_PATH="$APIM_API_PATH" python3 - <<'PY'
import json
import os
import sys

data = json.loads(os.environ["ACTUAL_JSON"])
summary = {
    "id": data.get("id"),
    "operations": sorted(item.get("id") for item in data.get("operations", [])),
    "path": data.get("path"),
    "upstream_base_url": data.get("upstream_base_url"),
}
expected = {
    "id": os.environ["EXPECTED_API_ID"],
    "operations": ["echo", "health"],
    "path": os.environ["EXPECTED_API_PATH"],
    "upstream_base_url": "http://mock-backend:8080/api",
}
if summary != expected:
    print("Metadata verification failed.", file=sys.stderr)
    print(json.dumps({"expected": expected, "actual": summary}, indent=2, sort_keys=True), file=sys.stderr)
    sys.exit(1)
print(json.dumps(summary, indent=2, sort_keys=True))
PY
}

verify_health_route() {
  local response

  echo '$ curl -sS "'"$APIM_BASE"'/'"$APIM_API_PATH"'/health"'
  response="$(curl -fsS "$APIM_BASE/$APIM_API_PATH/health")"

  ACTUAL_JSON="$response" python3 - <<'PY'
import json
import os
import sys

summary = json.loads(os.environ["ACTUAL_JSON"])
expected = {"path": "/api/health", "status": "ok"}
if summary != expected:
    print("Health route verification failed.", file=sys.stderr)
    print(json.dumps({"expected": expected, "actual": summary}, indent=2, sort_keys=True), file=sys.stderr)
    sys.exit(1)
print(json.dumps(summary, indent=2, sort_keys=True))
PY
}

verify_echo_route() {
  local response

  echo '$ curl -sS "'"$APIM_BASE"'/'"$APIM_API_PATH"'/echo"'
  response="$(curl -fsS "$APIM_BASE/$APIM_API_PATH/echo")"

  ACTUAL_JSON="$response" python3 - <<'PY'
import json
import os
import sys

data = json.loads(os.environ["ACTUAL_JSON"])
summary = {
    "body": data.get("body"),
    "method": data.get("method"),
    "ok": data.get("ok"),
    "path": data.get("path"),
}
expected = {
    "body": "",
    "method": "GET",
    "ok": True,
    "path": "/api/echo",
}
if summary != expected:
    print("Echo route verification failed.", file=sys.stderr)
    print(json.dumps({"expected": expected, "actual": summary}, indent=2, sort_keys=True), file=sys.stderr)
    sys.exit(1)
print(json.dumps(summary, indent=2, sort_keys=True))
PY
}

while (($# > 0)); do
  case "$1" in
    --verify)
      VERIFY=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

echo "Starting tutorial 01 stack with docker compose"
start_stack

echo "Waiting for gateway health at $APIM_BASE/apim/health"
wait_for_gateway

echo "Importing OpenAPI source into API '$APIM_API_ID'"
APIM_BASE_URL="$APIM_BASE" \
APIM_TENANT_KEY="$APIM_TENANT_KEY" \
OPENAPI_SOURCE="$OPENAPI_SOURCE" \
APIM_API_ID="$APIM_API_ID" \
APIM_API_NAME="$APIM_API_NAME" \
APIM_API_PATH="$APIM_API_PATH" \
uv run python "$ROOT_DIR/scripts/import_openapi.py"

if [[ "$VERIFY" -eq 1 ]]; then
  echo
  echo "Verifying imported API metadata"
  verify_api_metadata

  echo
  echo "Verifying imported API routes"
  verify_health_route
  echo
  verify_echo_route
  echo
fi
