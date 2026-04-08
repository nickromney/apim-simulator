#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT_DIR/scripts/tutorial_lib.sh"

init_tutorial_env
VERIFY=0

usage() {
  cat <<EOF
Usage: ./tutorial09.sh [--verify]

Runs tutorial step 9 for the APIM simulator end-to-end, including starting the
operator console stack with docker compose.
EOF
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

echo "Starting tutorial 09 stack with docker compose"
start_ui_stack

echo "Waiting for gateway health at $APIM_BASE/apim/health"
wait_for_gateway

echo "Waiting for operator console at $OPERATOR_CONSOLE_BASE"
wait_for_operator_console

echo "Operator console is available at $OPERATOR_CONSOLE_BASE"
echo "Gateway is available at $APIM_BASE"

if [[ "$VERIFY" -eq 1 ]]; then
  echo
  echo "Verifying the closest local equivalent"

  echo '$ curl -sS -H "X-Apim-Tenant-Key: '"$APIM_TENANT_KEY"'" "'"$APIM_BASE"'/apim/management/status"'
  status_response="$(management_get "/apim/management/status")"
  json_expect_summary \
    "$status_response" \
    '{"gateway_scope":"gateway","service_name":"apim-simulator"}' \
    'summary = {"gateway_scope": ((data.get("gateway_policy_scope") or {}).get("scope_name")), "service_name": ((data.get("service") or {}).get("name"))}'

  echo
  echo '$ curl -sS "'"$OPERATOR_CONSOLE_BASE"'"'
  capture_http_request "$OPERATOR_CONSOLE_BASE"
  captured_expect_summary \
    '{"status_code":200}' \
    'summary = {"status_code": status}'
  echo
fi
