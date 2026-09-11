#!/usr/bin/env bash
# Put an HTTP API in front of the function — the front door that works.
#
# A Lambda Function URL is the cheaper, simpler front door and the deploy script
# still creates one. On a new AWS account it returns 403 to everyone while the
# same function answers a signed request with 200. An API Gateway HTTP API in
# front of that same unchanged function returns 200 to the public, so the
# restriction is specific to Function URLs rather than to public traffic. This
# script builds that door. See deploy/README.md for how that was established.
#
# Safe to re-run: it adopts an existing API of the same name instead of making
# a second one.
#
#   AWS_PROFILE=xrv bash deploy/apigateway.sh

set -euo pipefail

REGION="${AWS_REGION:-eu-central-1}"
NAME="${XRV_NAME:-xrechnung-validator}"
API_NAME="${XRV_API_NAME:-$NAME}"
AWS="aws --region $REGION"

ACCOUNT=$($AWS sts get-caller-identity --query Account --output text)
ARN=$($AWS lambda get-function --function-name "$NAME" --query 'Configuration.FunctionArn' --output text)
echo "  function   $ARN"

# ---- the API ----------------------------------------------------------------
# --target is the whole configuration in one flag: it creates an AWS_PROXY
# integration, a catch-all $default route and an auto-deploying $default stage.
# Every path reaches the app, which is what a service that does its own routing
# wants; declaring routes here would mean maintaining them in two places.
API=$($AWS apigatewayv2 get-apis --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text)
if [ "$API" = "None" ] || [ -z "$API" ]; then
  API=$($AWS apigatewayv2 create-api --name "$API_NAME" --protocol-type HTTP \
          --target "$ARN" --query ApiId --output text)
  echo "  api        $API (created)"
else
  echo "  api        $API (existing)"
fi

# ---- let the API call the function ------------------------------------------
# Scoped to this API, not to apigateway.amazonaws.com at large: without the
# source ARN any API in any account could invoke the function.
if ! $AWS lambda get-policy --function-name "$NAME" --output text 2>/dev/null | grep -q "$API"; then
  $AWS lambda add-permission --function-name "$NAME" \
    --statement-id "apigw-${API}" --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT}:${API}/*" >/dev/null
  echo "  permission added"
else
  echo "  permission already granted"
fi

ENDPOINT=$($AWS apigatewayv2 get-api --api-id "$API" --query ApiEndpoint --output text)

# ---- prove it ---------------------------------------------------------------
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "${ENDPOINT}/healthz")
echo
if [ "$CODE" = "200" ]; then
  echo "  live: $ENDPOINT"
  echo
  echo "  bash deploy/measure.sh ${ENDPOINT}/"
else
  echo "  $ENDPOINT/healthz returned HTTP $CODE"
  echo "  the API exists; run deploy/status.sh to see which piece is refusing"
  exit 1
fi
