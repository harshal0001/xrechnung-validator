#!/usr/bin/env bash
# Put CloudFront in front of the Lambda function URL.
#
# Two reasons, and the second is the durable one.
#
# 1. A new AWS account cannot serve public unauthenticated function URLs. With
#    CloudFront the URL stops being public: it switches to AWS_IAM auth and
#    CloudFront signs each request with SigV4 through Origin Access Control, so
#    the restriction no longer applies.
# 2. It is the better end state anyway. The function is never reachable except
#    through CloudFront, the hostname is presentable, and responses can be
#    cached at the edge.
#
#   AWS_PROFILE=xrv bash deploy/cloudfront.sh
#
# Idempotent: re-running updates rather than duplicating.

set -euo pipefail

REGION="${AWS_REGION:-eu-central-1}"
NAME="${XRV_NAME:-xrechnung-validator}"
AWS="aws --region $REGION"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

ACCOUNT=$($AWS sts get-caller-identity --query Account --output text)
say "Account ${ACCOUNT}, region ${REGION}"

FN_URL=$($AWS lambda get-function-url-config --function-name "$NAME" --query FunctionUrl --output text)
ORIGIN=$(printf '%s' "$FN_URL" | sed -E 's#^https://##; s#/$##')
say "Origin ${ORIGIN}"

# ---- 1. Origin Access Control ------------------------------------------------
# CloudFront signs requests to the function URL with SigV4. Without this the
# origin would have to stay public, which is the thing we are working around.
OAC_ID=$(aws cloudfront list-origin-access-controls \
  --query "OriginAccessControlList.Items[?Name=='${NAME}-oac'].Id | [0]" --output text 2>/dev/null || echo "None")
if [ "$OAC_ID" = "None" ] || [ -z "$OAC_ID" ]; then
  say "Creating Origin Access Control"
  OAC_ID=$(aws cloudfront create-origin-access-control --origin-access-control-config \
    "Name=${NAME}-oac,Description=Sign requests to the Lambda function URL,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=lambda" \
    --query 'OriginAccessControl.Id' --output text)
else
  say "Origin Access Control ${OAC_ID} already exists"
fi

# ---- 2. Distribution ---------------------------------------------------------
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='${NAME}'].Id | [0]" --output text 2>/dev/null || echo "None")

if [ "$DIST_ID" = "None" ] || [ -z "$DIST_ID" ]; then
  say "Creating distribution"
  # CachingDisabled and AllViewerExceptHostHeader are both deliberate:
  #  - every response depends on an uploaded file, so caching would be wrong
  #  - the Host header must NOT be forwarded, or SigV4 signs the wrong host and
  #    the origin rejects it. This is the single most common way this setup fails.
  cat > /tmp/xrv-dist.json <<JSON
{
  "CallerReference": "${NAME}-$(date +%s)",
  "Comment": "${NAME}",
  "Enabled": true,
  "Origins": {
    "Quantity": 1,
    "Items": [{
      "Id": "lambda-url",
      "DomainName": "${ORIGIN}",
      "OriginAccessControlId": "${OAC_ID}",
      "CustomOriginConfig": {
        "HTTPPort": 80, "HTTPSPort": 443,
        "OriginProtocolPolicy": "https-only",
        "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
        "OriginReadTimeout": 60
      }
    }]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "lambda-url",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 7,
      "Items": ["GET","HEAD","OPTIONS","PUT","POST","PATCH","DELETE"],
      "CachedMethods": {"Quantity": 2, "Items": ["GET","HEAD"]}
    },
    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
    "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac",
    "Compress": true
  },
  "PriceClass": "PriceClass_100"
}
JSON
  DIST_ID=$(aws cloudfront create-distribution --distribution-config file:///tmp/xrv-dist.json \
    --query 'Distribution.Id' --output text)
  rm -f /tmp/xrv-dist.json
else
  say "Distribution ${DIST_ID} already exists"
fi

DIST_ARN="arn:aws:cloudfront::${ACCOUNT}:distribution/${DIST_ID}"
DOMAIN=$(aws cloudfront get-distribution --id "$DIST_ID" --query 'Distribution.DomainName' --output text)

# ---- 3. Lock the origin to CloudFront ---------------------------------------
say "Switching the function URL to AWS_IAM"
$AWS lambda update-function-url-config --function-name "$NAME" --auth-type AWS_IAM >/dev/null
$AWS lambda remove-permission --function-name "$NAME" \
  --statement-id FunctionURLAllowPublicAccess >/dev/null 2>&1 || true
$AWS lambda add-permission --function-name "$NAME" \
  --statement-id AllowCloudFrontInvoke \
  --action lambda:InvokeFunctionUrl --principal cloudfront.amazonaws.com \
  --source-arn "$DIST_ARN" --function-url-auth-type AWS_IAM >/dev/null 2>&1 || true

say "Waiting for the distribution to deploy (5-15 minutes)"
aws cloudfront wait distribution-deployed --id "$DIST_ID"

say "Deployed"
echo "  https://${DOMAIN}"
echo
echo "  Distribution ${DIST_ID}"
echo "  The function URL is now AWS_IAM: reachable only through CloudFront."
