#!/usr/bin/env bash
# Measure cold start and warm latency against a deployed URL.
#
# The README has an empty "cold start to first response" row, and it stays empty
# until something real fills it. This is that something: it forces a cold start
# by updating the function's configuration — which discards running execution
# environments — then times the first request against the ones that follow.
#
#   bash deploy/measure.sh https://xxxx.lambda-url.eu-central-1.on.aws/

set -euo pipefail
URL="${1:?usage: measure.sh <function-url>}"
NAME="${XRV_NAME:-xrechnung-validator}"
REGION="${AWS_REGION:-eu-central-1}"
SAMPLE="${2:-frontend/public/samples/missing-buyer-reference.xml}"

timed() { curl -s -o /dev/null -w '%{time_total}' -F "file=@${SAMPLE}" "${URL}validate?explain=true"; }

echo "Forcing a cold start (updating configuration discards warm environments)..."
aws lambda update-function-configuration --function-name "$NAME" --region "$REGION" \
  --description "cold-start measurement $(date -u +%FT%TZ)" >/dev/null
aws lambda wait function-updated --function-name "$NAME" --region "$REGION"
sleep 3

printf '\n  cold start   %.2f s\n' "$(timed)"
echo "  warm requests:"
for i in 1 2 3 4 5; do printf '    %d  %.3f s\n' "$i" "$(timed)"; done
echo
echo "Put the cold figure in README.md — measured, not estimated."
