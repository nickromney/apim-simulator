#!/bin/bash
set -euo pipefail

# LocalStack init hook: runs once the apigateway service is ready
# (mounted at /etc/localstack/init/ready.d/init-aws.sh). Builds a REST API
# that proxies every request to the same mock backend the APIM simulator
# fronts, so the AWS-shaped gateway and the APIM-shaped gateway can be
# compared side by side. See docs/adr/0001-goldilocks-ai-gateway-scope.md
# (decision D4).

REST_API_NAME="apim-comparison-api"
REST_API_ID="apimsim"
BACKEND_BASE_URL="http://mock-backend:8080/api"
STAGE_NAME="local"

echo "init-aws: creating REST API ${REST_API_NAME} (id=${REST_API_ID})"
awslocal apigateway create-rest-api \
  --name "${REST_API_NAME}" \
  --tags "{\"_custom_id_\":\"${REST_API_ID}\"}" \
  >/dev/null

# shellcheck disable=SC2016 # the query's backticks are JMESPath literals, not shell expansion
ROOT_RESOURCE_ID="$(
  awslocal apigateway get-resources \
    --rest-api-id "${REST_API_ID}" \
    --query 'items[?path==`/`].id' \
    --output text
)"
echo "init-aws: root resource id=${ROOT_RESOURCE_ID}"

PROXY_RESOURCE_ID="$(
  awslocal apigateway create-resource \
    --rest-api-id "${REST_API_ID}" \
    --parent-id "${ROOT_RESOURCE_ID}" \
    --path-part "{proxy+}" \
    --query 'id' \
    --output text
)"
echo "init-aws: proxy resource id=${PROXY_RESOURCE_ID}"

echo "init-aws: wiring ANY {proxy+} -> ${BACKEND_BASE_URL}/{proxy}"
awslocal apigateway put-method \
  --rest-api-id "${REST_API_ID}" \
  --resource-id "${PROXY_RESOURCE_ID}" \
  --http-method ANY \
  --authorization-type NONE \
  --request-parameters "method.request.path.proxy=true" \
  >/dev/null

awslocal apigateway put-integration \
  --rest-api-id "${REST_API_ID}" \
  --resource-id "${PROXY_RESOURCE_ID}" \
  --http-method ANY \
  --type HTTP_PROXY \
  --integration-http-method ANY \
  --uri "${BACKEND_BASE_URL}/{proxy}" \
  --request-parameters "integration.request.path.proxy=method.request.path.proxy" \
  >/dev/null

echo "init-aws: wiring ANY / -> ${BACKEND_BASE_URL}"
awslocal apigateway put-method \
  --rest-api-id "${REST_API_ID}" \
  --resource-id "${ROOT_RESOURCE_ID}" \
  --http-method ANY \
  --authorization-type NONE \
  >/dev/null

awslocal apigateway put-integration \
  --rest-api-id "${REST_API_ID}" \
  --resource-id "${ROOT_RESOURCE_ID}" \
  --http-method ANY \
  --type HTTP_PROXY \
  --integration-http-method ANY \
  --uri "${BACKEND_BASE_URL}" \
  >/dev/null

echo "init-aws: creating deployment (stage=${STAGE_NAME})"
awslocal apigateway create-deployment \
  --rest-api-id "${REST_API_ID}" \
  --stage-name "${STAGE_NAME}" \
  >/dev/null

echo "init-aws: ready - REST API ${REST_API_ID} deployed to stage ${STAGE_NAME}, proxying to ${BACKEND_BASE_URL}"
