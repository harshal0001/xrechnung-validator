#!/usr/bin/env bash
# Keep one execution environment warm, so the first visitor does not pay for it.
#
# A cold start is 3.4-4.7 s measured: the container starts, then Python compiles
# three XSD schemas and four Schematron stylesheets. Lambda holds an environment
# for roughly five to ten idle minutes, so a ping every five keeps one alive and
# a visitor arriving at any moment lands on a warm one. Someone who waits four
# seconds on a blank page does not wait.
#
# An EventBridge rule rather than a scheduled CI job: scheduled workflows are
# delayed under load, dropped silently, and disabled entirely after sixty days
# without a commit — none of which is visible until the demo is slow again.
# Scheduled rules that target an AWS service are free, and 8,640 invocations a
# month against a 1,000,000 always-free allowance is noise.
#
#   AWS_PROFILE=xrv bash deploy/keepwarm.sh          # install
#   AWS_PROFILE=xrv bash deploy/keepwarm.sh --remove # take it out
#
# Idempotent.

set -euo pipefail

REGION="${AWS_REGION:-eu-central-1}"
NAME="${XRV_NAME:-xrechnung-validator}"
RULE="${NAME}-keepwarm"
MINUTES="${XRV_KEEPWARM_MINUTES:-5}"
AWS="aws --region $REGION"

if [ "${1:-}" = "--remove" ]; then
  $AWS events remove-targets --rule "$RULE" --ids keepwarm >/dev/null 2>&1 || true
  $AWS events delete-rule --name "$RULE" >/dev/null 2>&1 || true
  $AWS lambda remove-permission --function-name "$NAME" --statement-id "events-${RULE}" >/dev/null 2>&1 || true
  echo "  removed $RULE"
  exit 0
fi

ACCOUNT=$($AWS sts get-caller-identity --query Account --output text)
ARN=$($AWS lambda get-function --function-name "$NAME" --query 'Configuration.FunctionArn' --output text)

RULE_ARN=$($AWS events put-rule --name "$RULE" \
  --schedule-expression "rate(${MINUTES} minutes)" --state ENABLED \
  --description "Keeps ${NAME} warm so the first visitor does not pay the cold start" \
  --query RuleArn --output text)
echo "  rule       $RULE  every ${MINUTES} minutes"

if ! $AWS lambda get-policy --function-name "$NAME" --output text 2>/dev/null | grep -q "$RULE"; then
  $AWS lambda add-permission --function-name "$NAME" \
    --statement-id "events-${RULE}" --action lambda:InvokeFunction \
    --principal events.amazonaws.com --source-arn "$RULE_ARN" >/dev/null
  echo "  permission added"
else
  echo "  permission already granted"
fi

# The image runs the Lambda Web Adapter, which turns the event into an HTTP
# request against the app. A bare EventBridge event is not one, so the target
# carries a literal HTTP-shaped payload: the ping exercises the real route and
# would fail loudly if the app stopped answering.
read -r -d '' PING <<'JSON' || true
{"version":"2.0","rawPath":"/healthz","requestContext":{"http":{"method":"GET","path":"/healthz"}},"headers":{"host":"keepwarm"},"isBase64Encoded":false}
JSON

$AWS events put-targets --rule "$RULE" \
  --targets "Id=keepwarm,Arn=${ARN},Input=$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$PING")" \
  --query 'FailedEntryCount' --output text | grep -qx 0 && echo "  target     $NAME"

echo
echo "  Next invocation within ${MINUTES} minutes. Confirm it is landing with:"
echo "    aws logs tail /aws/lambda/${NAME} --region ${REGION} --since 10m --filter-pattern keepwarm"
