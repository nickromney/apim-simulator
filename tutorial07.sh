#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT_DIR/scripts/tutorial_lib.sh"

init_tutorial_env
VERIFY=0
CURRENT_REVISION_SOURCE="${CURRENT_REVISION_SOURCE:-service/apim-simulator/apis/tutorial-api;rev=1}"

usage() {
  cat <<EOF
Usage: ./tutorial07.sh [--verify]

Runs tutorial step 7 for the APIM simulator end-to-end, including starting the
local stack with docker compose.
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

echo "Starting tutorial 07 stack with docker compose"
start_public_stack

echo "Waiting for gateway health at $APIM_BASE/apim/health"
wait_for_gateway

import_tutorial_api
echo

echo "Adding revision metadata"
revision_one="$(management_put "/apim/management/apis/$APIM_API_ID/revisions/1" "$(cat <<JSON
{"description":"Initial revision","is_current":false,"is_online":false}
JSON
)")"
json_expect_summary \
  "$revision_one" \
  '{"description":"Initial revision","id":"1","is_current":false,"is_online":false}' \
  'summary = {"description": data.get("description"), "id": data.get("id"), "is_current": data.get("is_current"), "is_online": data.get("is_online")}'
echo

revision_two="$(management_put "/apim/management/apis/$APIM_API_ID/revisions/2" "$(cat <<JSON
{"description":"Current revision","is_current":true,"is_online":true,"source_api_id":"$CURRENT_REVISION_SOURCE"}
JSON
)")"
json_expect_summary \
  "$revision_two" \
  "{\"description\":\"Current revision\",\"id\":\"2\",\"is_current\":true,\"source_api_id\":\"$CURRENT_REVISION_SOURCE\"}" \
  'summary = {"description": data.get("description"), "id": data.get("id"), "is_current": data.get("is_current"), "source_api_id": data.get("source_api_id")}'
echo

echo "Creating release 'public'"
release_response="$(management_put "/apim/management/apis/$APIM_API_ID/releases/public" "$(cat <<JSON
{"notes":"Published revision","revision":"2"}
JSON
)")"
json_expect_summary \
  "$release_response" \
  '{"api_id":"service/apim-simulator/apis/tutorial-api;rev=2","id":"public","revision":"2"}' \
  'summary = {"api_id": data.get("api_id"), "id": data.get("id"), "revision": data.get("revision")}'

if [[ "$VERIFY" -eq 1 ]]; then
  echo
  echo "Verifying revision metadata"

  echo '$ curl -sS -H "X-Apim-Tenant-Key: '"$APIM_TENANT_KEY"'" "'"$APIM_BASE"'/apim/management/apis/'"$APIM_API_ID"'"'
  api_response="$(management_get "/apim/management/apis/$APIM_API_ID")"
  json_expect_summary \
    "$api_response" \
    '{"id":"tutorial-api","release_ids":["public"],"revision":"2","revision_ids":["1","2"]}' \
    'summary = {"id": data.get("id"), "release_ids": sorted(item.get("id") for item in data.get("releases", [])), "revision": data.get("revision"), "revision_ids": sorted(item.get("id") for item in data.get("revisions", []))}'

  echo
  echo '$ curl -sS -H "X-Apim-Tenant-Key: '"$APIM_TENANT_KEY"'" "'"$APIM_BASE"'/apim/management/apis/'"$APIM_API_ID"'/revisions"'
  revisions_response="$(management_get "/apim/management/apis/$APIM_API_ID/revisions")"
  json_expect_summary \
    "$revisions_response" \
    '{"revisions":[{"id":"1","is_current":false},{"id":"2","is_current":true}]}' \
    'summary = {"revisions": sorted([{"id": item.get("id"), "is_current": item.get("is_current")} for item in data], key=lambda item: item["id"])}'

  echo
  echo '$ curl -sS -H "X-Apim-Tenant-Key: '"$APIM_TENANT_KEY"'" "'"$APIM_BASE"'/apim/management/apis/'"$APIM_API_ID"'/releases"'
  releases_response="$(management_get "/apim/management/apis/$APIM_API_ID/releases")"
  json_expect_summary \
    "$releases_response" \
    '{"releases":[{"id":"public","revision":"2"}]}' \
    'summary = {"releases": [{"id": item.get("id"), "revision": item.get("revision")} for item in data]}'
  echo
fi
