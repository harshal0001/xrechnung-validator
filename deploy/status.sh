#!/usr/bin/env bash
# What is deployed, and what of it is actually working.
#
# The distinction matters: a function can be deployed, healthy and answering an
# authenticated caller with 200 while one of its public front doors refuses
# everyone. That is the state this deployment is in — the Function URL returns
# 403 on this account and the HTTP API in front of the same function returns
# 200 — so this checks the pieces separately and says which is which.
#
#   AWS_PROFILE=xrv bash deploy/status.sh

set -uo pipefail   # not -e: a failing check is a result, not a reason to stop

REGION="${AWS_REGION:-eu-central-1}"
NAME="${XRV_NAME:-xrechnung-validator}"
API_NAME="${XRV_API_NAME:-$NAME}"
AWS="aws --region $REGION"

ok()   { printf '  \033[32m✓\033[0m %-30s %s\n' "$1" "${2:-}"; }
bad()  { printf '  \033[31m✗\033[0m %-30s %s\n' "$1" "${2:-}"; }
info() { printf '    %-28s %s\n' "$1" "${2:-}"; }

echo
ACCOUNT=$($AWS sts get-caller-identity --query Account --output text 2>/dev/null) \
  && ok "AWS credentials" "account $ACCOUNT, $REGION" \
  || { bad "AWS credentials" "not authenticated — run: aws configure --profile xrv"; exit 1; }

# ---- account maturity --------------------------------------------------------
# A new account is capped at 10 concurrent executions instead of 1000. That cap
# is not itself the reason the Function URL is blocked, but it lifts at the same
# time, so it is a usable proxy for "has this account been vetted yet".
CONC=$($AWS lambda get-account-settings --query 'AccountLimit.ConcurrentExecutions' --output text 2>/dev/null)
if [ "${CONC:-0}" -ge 100 ] 2>/dev/null; then
  ok "account limits" "concurrency $CONC — account is out of new-account restriction"
else
  bad "account limits" "concurrency $CONC — still a new account"
  info "" "expect the Function URL to stay blocked until this rises"
fi

# ---- what exists -------------------------------------------------------------
STATE=$($AWS lambda get-function --function-name "$NAME" --query 'Configuration.State' --output text 2>/dev/null)
[ "$STATE" = "Active" ] && ok "lambda function" "$NAME, $STATE" || bad "lambda function" "${STATE:-missing}"

IMG=$($AWS ecr describe-images --repository-name "$NAME" \
       --query 'sum(imageDetails[].imageSizeInBytes)' --output text 2>/dev/null)
if [ -n "$IMG" ] && [ "$IMG" != "None" ]; then
  ok "ecr image" "$(awk -v b="$IMG" 'BEGIN{printf "%.0f MB", b/1e6}') (free tier: 500 MB)"
else
  bad "ecr image" "none"
fi

# ---- does the application work? ---------------------------------------------
# Authenticated invoke, so this answers "is the software healthy" without being
# confounded by whether any front door is open.
TMP=$(mktemp -d)
printf '{"version":"2.0","rawPath":"/healthz","requestContext":{"http":{"method":"GET","path":"/healthz"}},"headers":{"host":"x"},"isBase64Encoded":false}' > "$TMP/ev.json"
if $AWS lambda invoke --function-name "$NAME" --payload "fileb://$TMP/ev.json" "$TMP/out.json" >/dev/null 2>&1; then
  BODY=$(python3 -c "import json;print(json.load(open('$TMP/out.json')).get('body',''))" 2>/dev/null)
  case "$BODY" in
    *'"status":"ok"'*) ok "application (direct invoke)" "$BODY" ;;
    *) bad "application (direct invoke)" "unexpected: ${BODY:0:80}" ;;
  esac
else
  bad "application (direct invoke)" "invoke failed"
fi
rm -rf "$TMP"

# ---- the front doors ---------------------------------------------------------
probe() {  # label, url
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$2/healthz" 2>/dev/null)
  [ "$code" = "200" ] && ok "$1" "$2" || bad "$1" "HTTP $code — $2"
  [ "$code" = "200" ]
}

LIVE=""
API=$($AWS apigatewayv2 get-apis --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text 2>/dev/null)
if [ -n "$API" ] && [ "$API" != "None" ]; then
  ENDPOINT=$($AWS apigatewayv2 get-api --api-id "$API" --query ApiEndpoint --output text)
  probe "http api (public)" "$ENDPOINT" && LIVE="$ENDPOINT/"
else
  bad "http api (public)" "no API named $API_NAME — run: bash deploy/apigateway.sh"
fi

URL=$($AWS lambda get-function-url-config --function-name "$NAME" --query FunctionUrl --output text 2>/dev/null)
if [ -n "$URL" ] && [ "$URL" != "None" ]; then
  probe "function url (public)" "${URL%/}" && LIVE="${LIVE:-$URL}" \
    || info "" "expected on a new account; the HTTP API is the working door"
else
  info "function url" "none configured"
fi

if [ -n "$LIVE" ]; then
  echo; echo "  Live. Measure the cold start:"
  echo "    bash deploy/measure.sh $LIVE"
fi
echo
