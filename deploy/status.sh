#!/usr/bin/env bash
# What is deployed, and what of it is actually working.
#
# The distinction matters: a function can be deployed, healthy and returning 200
# to an authenticated caller while its public URL refuses everyone. That is not
# a hypothetical — it is the state this deployment was in on day one, because a
# new AWS account cannot serve public unauthenticated endpoints until AWS lifts
# the restriction. So this checks the pieces separately and says which is which.
#
#   AWS_PROFILE=xrv bash deploy/status.sh

set -uo pipefail   # not -e: a failing check is a result, not a reason to stop

REGION="${AWS_REGION:-eu-central-1}"
NAME="${XRV_NAME:-xrechnung-validator}"
AWS="aws --region $REGION"

ok()   { printf '  \033[32m✓\033[0m %-30s %s\n' "$1" "${2:-}"; }
bad()  { printf '  \033[31m✗\033[0m %-30s %s\n' "$1" "${2:-}"; }
info() { printf '    %-28s %s\n' "$1" "${2:-}"; }

echo
ACCOUNT=$($AWS sts get-caller-identity --query Account --output text 2>/dev/null) \
  && ok "AWS credentials" "account $ACCOUNT, $REGION" \
  || { bad "AWS credentials" "not authenticated — run: aws configure --profile xrv"; exit 1; }

# ---- account restrictions ----------------------------------------------------
# A new account is capped at 10 concurrent executions and cannot serve public
# function URLs. Both lift together when AWS finishes vetting, so the concurrency
# figure is a usable proxy for "has the restriction gone".
CONC=$($AWS lambda get-account-settings --query 'AccountLimit.ConcurrentExecutions' --output text 2>/dev/null)
if [ "${CONC:-0}" -ge 100 ] 2>/dev/null; then
  ok "account limits" "concurrency $CONC — account is out of new-account restriction"
else
  bad "account limits" "concurrency $CONC — new-account restriction still applied"
  info "" "public function URLs stay blocked until this rises"
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
# confounded by whether the front door is open.
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

# ---- is it reachable by the public? -----------------------------------------
URL=$($AWS lambda get-function-url-config --function-name "$NAME" --query FunctionUrl --output text 2>/dev/null)
if [ -n "$URL" ] && [ "$URL" != "None" ]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "${URL}healthz" 2>/dev/null)
  if [ "$CODE" = "200" ]; then
    ok "public url" "$URL"
    echo; echo "  Everything is live. Measure the cold start:"
    echo "    bash deploy/measure.sh $URL"
  else
    bad "public url" "HTTP $CODE — $URL"
    info "" "the application is fine; the door is shut"
  fi
else
  bad "public url" "no function URL configured"
fi
echo
