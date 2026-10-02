#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
source "$ROOT_DIR/scripts/tutorial_lib.sh"

init_tutorial_env
EXECUTE=0
VERIFY=0
DRY_RUN=0
VERSION_SET_ID="${VERSION_SET_ID:-public}"
VERSIONED_PATH="${VERSIONED_PATH:-versioned}"
PATH_VERSION_SET_ID="${PATH_VERSION_SET_ID:-tutorial-path-versions}"
PATH_VERSIONED_PATH="${PATH_VERSIONED_PATH:-path-versioned}"
VERSION_PRODUCT_ID="${VERSION_PRODUCT_ID:-version-tutorial-product}"
VERSION_SUBSCRIPTION_ID="${VERSION_SUBSCRIPTION_ID:-version-tutorial-sub}"
VERSION_SUBSCRIPTION_KEY="${VERSION_SUBSCRIPTION_KEY:-version-tutorial-key}"
APIM_PORTAL_USER="${APIM_PORTAL_USER:-demo-dev}"

usage() {
  cat <<EOF
Usage: ./docs/tutorials/apim-get-started/tutorial08.sh [--setup|--execute|--verify|--dry-run]

Runs tutorial step 8 for the APIM simulator.

Flags:
  --setup, --execute  Clone an API into a Path version and set up Header routing.
  --verify            Verify the existing tutorial state without restarting it.
  --dry-run           Show this help and preview the setup action without side effects.
  --help, -h          Show this help text.
EOF
}

verify_tutorial() {
  echo "Verifying Original and cloned Path version"
  original_api="$(management_get "/apim/management/apis/path-versioned-original")"
  json_expect_summary "$original_api" \
    "{\"id\":\"path-versioned-original\",\"api_version\":null,\"version_description\":\"Original\",\"api_version_set\":\"$PATH_VERSION_SET_ID\"}" \
    'summary = {"id": data.get("id"), "api_version": data.get("api_version"), "version_description": data.get("version_description"), "api_version_set": data.get("api_version_set")}'
  capture_http_request -H "Ocp-Apim-Subscription-Key: $VERSION_SUBSCRIPTION_KEY" "$APIM_BASE/$PATH_VERSIONED_PATH/echo"
  captured_expect_summary \
    '{"path":"/api/echo","status_code":200,"x_version":null}' \
    'summary = {"path": (body_json or {}).get("path"), "status_code": status, "x_version": headers.get("x-version")}'
  capture_http_request -H "Ocp-Apim-Subscription-Key: $VERSION_SUBSCRIPTION_KEY" "$APIM_BASE/$PATH_VERSIONED_PATH/v2/echo"
  captured_expect_summary \
    '{"path":"/api/echo","status_code":200,"x_version":"v2"}' \
    'summary = {"path": (body_json or {}).get("path"), "status_code": status, "x_version": headers.get("x-version")}'
  catalog="$(portal_get "/apim/portal/catalog" "$APIM_PORTAL_USER")"
  json_expect_summary "$catalog" \
    "{\"product_id\":\"$VERSION_PRODUCT_ID\",\"api_ids\":[\"path-versioned-original\",\"path-versioned-v2\"]}" \
    'product = next(p for p in data["products"] if p["id"] == "'"$VERSION_PRODUCT_ID"'")
summary = {"product_id": product["id"], "api_ids": sorted(api["id"] for api in product["apis"])}'
  echo
  echo "Verifying version routing"

  echo '$ curl -sS -H "X-Apim-Tenant-Key: '"$APIM_TENANT_KEY"'" "'"$APIM_BASE"'/apim/management/api-version-sets/'"$VERSION_SET_ID"'"'
  fetched_version_set="$(management_get "/apim/management/api-version-sets/$VERSION_SET_ID")"
  json_expect_summary \
    "$fetched_version_set" \
    "{\"id\":\"$VERSION_SET_ID\",\"version_header_name\":\"x-api-version\"}" \
    'summary = {"id": data.get("id"), "version_header_name": data.get("version_header_name")}'

  echo
  echo '$ curl -i -H "x-api-version: v1" "'"$APIM_BASE"'/'"$VERSIONED_PATH"'/echo"'
  capture_http_request -H "x-api-version: v1" "$APIM_BASE/$VERSIONED_PATH/echo"
  captured_expect_summary \
    '{"path":"/api/echo","status_code":200,"x_version":null}' \
    'summary = {"path": (body_json or {}).get("path"), "status_code": status, "x_version": headers.get("x-version")}'

  echo
  echo '$ curl -i -H "x-api-version: v2" "'"$APIM_BASE"'/'"$VERSIONED_PATH"'/echo"'
  capture_http_request -H "x-api-version: v2" "$APIM_BASE/$VERSIONED_PATH/echo"
  captured_expect_summary \
    '{"path":"/api/echo","status_code":200,"x_version":"v2"}' \
    'summary = {"path": (body_json or {}).get("path"), "status_code": status, "x_version": headers.get("x-version")}'
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
  run_verify_with_setup_hint "./docs/tutorials/apim-get-started/tutorial08.sh" verify_tutorial
  exit 0
fi

echo "Starting tutorial 08 stack with docker compose"
start_public_stack

echo "Waiting for gateway health at $APIM_BASE/apim/health"
wait_for_gateway

echo "Creating a published product and an unversioned source API"
management_put "/apim/management/products/$VERSION_PRODUCT_ID" \
  "{\"name\":\"Version tutorial product\",\"state\":\"published\",\"require_subscription\":true}" >/dev/null
ensure_subscription_absent "$VERSION_SUBSCRIPTION_ID"
management_post "/apim/management/subscriptions" \
  "{\"id\":\"$VERSION_SUBSCRIPTION_ID\",\"name\":\"Version tutorial\",\"products\":[\"$VERSION_PRODUCT_ID\"],\"primary_key\":\"$VERSION_SUBSCRIPTION_KEY\"}" >/dev/null
for api_id in path-versioned-v2 path-versioned-original; do
  if management_get "/apim/management/apis/$api_id" >/dev/null 2>&1; then
    management_delete "/apim/management/apis/$api_id" >/dev/null
  fi
done
management_put "/apim/management/apis/path-versioned-original" \
  "{\"name\":\"Path versioned API\",\"path\":\"$PATH_VERSIONED_PATH\",\"upstream_base_url\":\"http://mock-backend:8080/api\",\"products\":[\"$VERSION_PRODUCT_ID\"]}" >/dev/null
management_put "/apim/management/apis/path-versioned-original/operations/echo" \
  '{"name":"Echo","method":"GET","url_template":"/echo"}' >/dev/null
echo "Cloning the source into version v2; the source remains Original"
path_version="$(management_post "/apim/management/apis/path-versioned-original/versions" \
  "{\"version_id\":\"path-versioned-v2\",\"api_version\":\"v2\",\"version_set_id\":\"$PATH_VERSION_SET_ID\",\"versioning_scheme\":\"Path\"}")"
json_expect_summary "$path_version" \
  "{\"id\":\"path-versioned-v2\",\"api_version\":\"v2\",\"products\":[\"$VERSION_PRODUCT_ID\"],\"operation_ids\":[\"echo\"]}" \
  'summary = {"id": data.get("id"), "api_version": data.get("api_version"), "products": data.get("products"), "operation_ids": sorted(op["id"] for op in data.get("operations", []))}'
management_put "/apim/management/apis/path-versioned-v2/operations/echo" \
  '{"name":"Echo v2","method":"GET","url_template":"/echo","policies_xml":"<policies><inbound><base /></inbound><backend><base /></backend><outbound><base /><set-header name=\"x-version\" exists-action=\"override\"><value>v2</value></set-header></outbound><on-error><base /></on-error></policies>"}' >/dev/null
echo

echo "Creating version set '$VERSION_SET_ID'"
version_set_response="$(management_put "/apim/management/api-version-sets/$VERSION_SET_ID" "$(cat <<JSON
{"display_name":"Public","versioning_scheme":"Header","version_header_name":"x-api-version"}
JSON
)")"
json_expect_summary \
  "$version_set_response" \
  "{\"id\":\"$VERSION_SET_ID\",\"version_header_name\":\"x-api-version\",\"versioning_scheme\":\"Header\"}" \
  'summary = {"id": data.get("id"), "version_header_name": data.get("version_header_name"), "versioning_scheme": data.get("versioning_scheme")}'
echo

echo "Creating versioned APIs"
v1_api="$(management_put "/apim/management/apis/versioned-v1" "$(cat <<JSON
{"name":"Versioned V1","path":"$VERSIONED_PATH","upstream_base_url":"http://mock-backend:8080/api","api_version_set":"$VERSION_SET_ID","api_version":"v1"}
JSON
)")"
json_expect_summary \
  "$v1_api" \
  "{\"api_version\":\"v1\",\"id\":\"versioned-v1\",\"path\":\"$VERSIONED_PATH\"}" \
  'summary = {"api_version": data.get("api_version"), "id": data.get("id"), "path": data.get("path")}'
echo

v2_api="$(management_put "/apim/management/apis/versioned-v2" "$(cat <<JSON
{"name":"Versioned V2","path":"$VERSIONED_PATH","upstream_base_url":"http://mock-backend:8080/api","api_version_set":"$VERSION_SET_ID","api_version":"v2","policies_xml":"<policies><inbound /><backend><forward-request /></backend><outbound><set-header name=\"x-version\" exists-action=\"override\"><value>v2</value></set-header></outbound><on-error /></policies>"}
JSON
)")"
json_expect_summary \
  "$v2_api" \
  "{\"api_version\":\"v2\",\"id\":\"versioned-v2\",\"path\":\"$VERSIONED_PATH\"}" \
  'summary = {"api_version": data.get("api_version"), "id": data.get("id"), "path": data.get("path")}'
echo

management_put "/apim/management/apis/versioned-v1/operations/echo" "$(cat <<JSON
{"name":"echo","method":"GET","url_template":"/echo"}
JSON
)" >/dev/null
management_put "/apim/management/apis/versioned-v2/operations/echo" "$(cat <<JSON
{"name":"echo","method":"GET","url_template":"/echo"}
JSON
)" >/dev/null
echo "Added the echo operation to both versions"
echo
echo "Setup complete. Run ./docs/tutorials/apim-get-started/tutorial08.sh --verify to validate version routing."
