#!/bin/sh
set -eu

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
APIM_SUBSCRIPTION_KEY="${APIM_SUBSCRIPTION_KEY:-todo-demo-key}"

export API_BASE_URL
export APIM_SUBSCRIPTION_KEY

envsubst '${API_BASE_URL} ${APIM_SUBSCRIPTION_KEY}' \
  < /opt/runtime-config.template.js \
  > /usr/share/nginx/html/runtime-config.js
