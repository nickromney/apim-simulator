#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/scripts/tutorial_lib.sh"

init_tutorial_env
EXECUTE=0
VERIFY=0
DRY_RUN=0

APIM_PORTAL_USER="${APIM_PORTAL_USER:-demo-dev}"
APIM_PORTAL_PRODUCT_ID="${APIM_PORTAL_PRODUCT_ID:-portal-premium}"
APIM_PORTAL_API_ID="${APIM_PORTAL_API_ID:-portal-hello}"
APIM_PORTAL_SUBSCRIPTION_ID="$APIM_PORTAL_USER-$APIM_PORTAL_PRODUCT_ID"
APIM_PORTAL_SUBSCRIPTION_KEY="sub-$APIM_PORTAL_SUBSCRIPTION_ID-primary"

usage() {
  cat <<EOF
Usage: ./docs/tutorials/apim-get-started/tutorial09.sh [--setup|--execute|--verify|--dry-run]

Runs tutorial step 9 for the APIM simulator: the adapted consumer
developer-portal loop (publish an approval-gated product, request a
subscription from the portal, approve it, and call the API).

Flags:
  --setup, --execute  Start the stack and run the portal sign-up and approval loop.
  --verify            Verify the existing tutorial state without restarting it.
  --dry-run           Show this help and preview the setup action without side effects.
  --help, -h          Show this help text.
EOF
}

verify_tutorial() {
  echo "Verifying the consumer developer portal"

  echo '$ curl -i "'"$APIM_BASE"'/apim/portal"'
  capture_http_request "$APIM_BASE/apim/portal"
  captured_expect_summary \
    '{"status_code":200}' \
    'summary = {"status_code": status}'

  echo
  echo '$ curl -sS -H "X-Apim-Portal-User: '"$APIM_PORTAL_USER"'" "'"$APIM_BASE"'/apim/portal/catalog"'
  catalog_response="$(portal_get "/apim/portal/catalog" "$APIM_PORTAL_USER")"
  json_expect_summary \
    "$catalog_response" \
    "{\"product_id\":\"$APIM_PORTAL_PRODUCT_ID\",\"approval_required\":true,\"api_ids\":[\"$APIM_PORTAL_API_ID\"]}" \
    'product = next(p for p in data["products"] if p["id"] == "'"$APIM_PORTAL_PRODUCT_ID"'")
summary = {"product_id": product["id"], "approval_required": product["approval_required"], "api_ids": [api["id"] for api in product["apis"]]}'

  echo
  echo '$ curl -sS -H "X-Apim-Portal-User: '"$APIM_PORTAL_USER"'" "'"$APIM_BASE"'/apim/portal/subscriptions"'
  subscriptions_response="$(portal_get "/apim/portal/subscriptions" "$APIM_PORTAL_USER")"
  json_expect_summary \
    "$subscriptions_response" \
    "{\"id\":\"$APIM_PORTAL_SUBSCRIPTION_ID\",\"state\":\"active\"}" \
    'subscription = next(s for s in data["subscriptions"] if s["id"] == "'"$APIM_PORTAL_SUBSCRIPTION_ID"'")
summary = {"id": subscription["id"], "state": subscription["state"]}'

  echo
  echo '$ curl -sS -H "Ocp-Apim-Subscription-Key: '"$APIM_PORTAL_SUBSCRIPTION_KEY"'" "'"$APIM_BASE"'/'"$APIM_PORTAL_API_ID"'/health"'
  authorized_response="$(gateway_get_with_subscription "/$APIM_PORTAL_API_ID/health" "$APIM_PORTAL_SUBSCRIPTION_KEY")"
  json_expect_summary \
    "$authorized_response" \
    '{"path":"/api/health","status":"ok"}' \
    'summary = {"path": data.get("path"), "status": data.get("status")}'
  echo
}

while (($# > 0)); do
  case "$1" in
    --setup|--execute)
      EXECUTE=1
      ;;
    --verify)
      VERIFY=1
      ;;
    --dry-run)
      DRY_RUN=1
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

if [[ "$EXECUTE" -eq 1 && "$VERIFY" -eq 1 ]]; then
  echo "Choose either --setup/--execute or --verify." >&2
  usage >&2
  exit 2
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  usage
  echo "INFO dry-run: would run $(basename "$0") setup; use --verify for read-only validation"
  exit 0
fi

if [[ "$EXECUTE" -eq 0 && "$VERIFY" -eq 0 ]]; then
  usage
  echo "INFO dry-run: would run $(basename "$0") setup; use --verify for read-only validation"
  exit 0
fi

if [[ "$VERIFY" -eq 1 ]]; then
  run_verify_with_setup_hint "./docs/tutorials/apim-get-started/tutorial09.sh" verify_tutorial
  exit 0
fi

echo "Starting tutorial 09 stack with docker compose"
start_ui_stack

echo "Waiting for gateway health at $APIM_BASE/apim/health"
wait_for_gateway

echo "Waiting for operator console at $OPERATOR_CONSOLE_BASE"
wait_for_operator_console

echo "Creating approval-gated product '$APIM_PORTAL_PRODUCT_ID'"
product_response="$(management_put "/apim/management/products/$APIM_PORTAL_PRODUCT_ID" "$(cat <<JSON
{"name":"Portal Premium","description":"Approval-gated portal demo product","require_subscription":true,"approval_required":true}
JSON
)")"
json_expect_summary \
  "$product_response" \
  "{\"id\":\"$APIM_PORTAL_PRODUCT_ID\",\"state\":\"published\",\"approval_required\":true}" \
  'summary = {"id": data.get("id"), "state": data.get("state"), "approval_required": data.get("approval_required")}'
echo

echo "Creating API '$APIM_PORTAL_API_ID' attached to '$APIM_PORTAL_PRODUCT_ID'"
api_response="$(management_put "/apim/management/apis/$APIM_PORTAL_API_ID" "$(cat <<JSON
{"name":"Portal Hello","path":"$APIM_PORTAL_API_ID","upstream_base_url":"http://mock-backend:8080","upstream_path_prefix":"/api","products":["$APIM_PORTAL_PRODUCT_ID"]}
JSON
)")"
json_expect_summary \
  "$api_response" \
  "{\"id\":\"$APIM_PORTAL_API_ID\",\"path\":\"$APIM_PORTAL_API_ID\",\"products\":[\"$APIM_PORTAL_PRODUCT_ID\"]}" \
  'summary = {"id": data.get("id"), "path": data.get("path"), "products": data.get("products")}'
echo

echo "Adding a health operation so the portal try-it console has a target"
management_put "/apim/management/apis/$APIM_PORTAL_API_ID/operations/health" "$(cat <<JSON
{"name":"Health","method":"GET","url_template":"/health"}
JSON
)" >/dev/null
echo

ensure_subscription_absent "$APIM_PORTAL_SUBSCRIPTION_ID"

echo "Requesting a subscription from the portal as '$APIM_PORTAL_USER'"
echo '$ curl -sS -X POST -H "X-Apim-Portal-User: '"$APIM_PORTAL_USER"'" "'"$APIM_BASE"'/apim/portal/subscriptions" --data '"'"'{"product_id":"'"$APIM_PORTAL_PRODUCT_ID"'"}'"'"
signup_response="$(portal_post "/apim/portal/subscriptions" "$APIM_PORTAL_USER" "{\"product_id\":\"$APIM_PORTAL_PRODUCT_ID\"}")"
json_expect_summary \
  "$signup_response" \
  "{\"id\":\"$APIM_PORTAL_SUBSCRIPTION_ID\",\"state\":\"submitted\",\"primary_key\":\"$APIM_PORTAL_SUBSCRIPTION_KEY\"}" \
  'summary = {"id": data.get("id"), "state": data.get("state"), "primary_key": (data.get("keys") or {}).get("primary")}'
echo

echo "A submitted subscription cannot call the API yet"
echo '$ curl -i -H "Ocp-Apim-Subscription-Key: '"$APIM_PORTAL_SUBSCRIPTION_KEY"'" "'"$APIM_BASE"'/'"$APIM_PORTAL_API_ID"'/health"'
capture_http_request "$APIM_BASE/$APIM_PORTAL_API_ID/health" -H "Ocp-Apim-Subscription-Key: $APIM_PORTAL_SUBSCRIPTION_KEY"
captured_expect_summary \
  '{"detail":"Subscription is not active (state: submitted)","status_code":403}' \
  'summary = {"detail": (body_json or {}).get("detail"), "status_code": status}'
echo

echo "Approving the subscription as the operator"
approve_response="$(management_patch "/apim/management/subscriptions/$APIM_PORTAL_SUBSCRIPTION_ID" '{"state":"active"}')"
json_expect_summary \
  "$approve_response" \
  "{\"id\":\"$APIM_PORTAL_SUBSCRIPTION_ID\",\"state\":\"active\"}" \
  'summary = {"id": data.get("id"), "state": data.get("state")}'
echo

echo "The approved subscription can call the API"
authorized_response="$(gateway_get_with_subscription "/$APIM_PORTAL_API_ID/health" "$APIM_PORTAL_SUBSCRIPTION_KEY")"
json_expect_summary \
  "$authorized_response" \
  '{"path":"/api/health","status":"ok"}' \
  'summary = {"path": data.get("path"), "status": data.get("status")}'
echo

echo "Portal page is available at $APIM_BASE/apim/portal"
echo "Operator console is available at $OPERATOR_CONSOLE_BASE"
echo
echo "Setup complete. Run ./docs/tutorials/apim-get-started/tutorial09.sh --verify to validate the portal loop."
