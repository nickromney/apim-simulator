#!/usr/bin/env bash
set -euo pipefail

PORTAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$PORTAL_DIR/../.." && pwd)"
# shellcheck source=scripts/lib/shell-cli.sh
source "$ROOT_DIR/scripts/lib/shell-cli.sh"

usage() {
  echo 'Usage: smoke-browser.sh [--dry-run] [--execute]'
  echo 'Run the Scalar and operator console browser smokes against Docker in slot 7.'
}

shell_cli_handle_standard_no_args usage \
  'would build the slot-7 stack, log both browser smokes, and remove the stack' "$@"

export APIM_BIND_ADDRESS=127.0.0.1 APIM_GATEWAY_PORT=8700 OPERATOR_CONSOLE_PORT=3707
export APIM_BASE_URL=http://127.0.0.1:8700 OPERATOR_CONSOLE_BASE_URL=http://localhost:3707
export APIM_ALLOWED_ORIGIN_GATEWAY="$APIM_BASE_URL"
export APIM_ALLOWED_ORIGIN_OPERATOR_CONSOLE="$OPERATOR_CONSOLE_BASE_URL" OPERATOR_CONSOLE_URL="$OPERATOR_CONSOLE_BASE_URL"
export APIM_CONFIG_SOURCE_PATH=/app/examples/basic.json APIM_CONFIG_PATH=/tmp/apim-config.json
unset APIM_TENANT_KEY

mkdir -p "$ROOT_DIR/.run/verify"
evidence=$(mktemp -d "$ROOT_DIR/.run/verify/browser-s7.XXXXXX")
printf 'Browser smoke logs: %s\n' "$evidence"
git -C "$ROOT_DIR" rev-parse HEAD > "$evidence/revision.txt"

project=$(basename "$evidence" | tr '[:upper:].' '[:lower:]-')
compose=(docker compose --project-directory "$ROOT_DIR" --project-name "apim-$project"
  -f "$ROOT_DIR/compose.yml" -f "$ROOT_DIR/compose.public.yml" -f "$ROOT_DIR/compose.ui.yml")
phase=authoring

cleanup() {
  local status=$?
  "${compose[@]}" logs --no-color > "$evidence/$phase-compose.log" 2>&1 || true
  if ! "${compose[@]}" down --remove-orphans > "$evidence/down.log" 2>&1; then
    cat "$evidence/down.log" >&2
    status=1
  fi
  exit "$status"
}

probe_gateway() {
  curl -fsS --max-time 5 "$APIM_BASE_URL/apim/health" && printf '\n'
}

cd "$PORTAL_DIR"
npm ci 2>&1 | tee "$evidence/install.log"
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${compose[@]}" up --build --wait --wait-timeout 90 2>&1 | tee "$evidence/authoring-launch.log"
probe_gateway 2>&1 | tee "$evidence/authoring-health.log"
curl -fsS --max-time 5 "$OPERATOR_CONSOLE_BASE_URL/" -o /dev/null 2> "$evidence/console-health.log"
authoring=0
npm run smoke-authoring 2>&1 | tee "$evidence/authoring.log" || authoring=$?

"${compose[@]}" logs --no-color > "$evidence/authoring-compose.log" 2>&1 || true
phase=scalar
"${compose[@]}" -f "$PORTAL_DIR/compose.scalar.yml" up --no-build --force-recreate \
  --no-deps --wait --wait-timeout 90 apim-simulator 2>&1 | tee "$evidence/scalar-launch.log"
probe_gateway 2>&1 | tee "$evidence/scalar-health.log"
scalar=0
npm run smoke 2>&1 | tee "$evidence/scalar.log" || scalar=$?

printf 'Authoring exit status: %s\nScalar exit status: %s\n' "$authoring" "$scalar" | tee "$evidence/results.txt"
(( authoring == 0 && scalar == 0 ))
